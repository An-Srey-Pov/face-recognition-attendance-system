"""
Database utilities for the Face Recognition Attendance System.

Features:
- SQLite database connection
- Database table initialization
- Attendance schedule settings
- Student CRUD operations
- Secure user authentication
- Admin and Class Monitor roles
- Password hashing
- Automatic check-in and check-out detection
- On Time, Late, and Early Leave status calculation
- Duplicate attendance prevention
- Student attendance history
- Attendance reports
- Dashboard statistics
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_PATH = Path(__file__).resolve().with_name(
    "attendance.db"
)

DEFAULT_SETTINGS = {
    "checkin_start": "07:00",
    "checkin_ontime_end": "08:45",
    "checkout_ontime_start": "17:00",
    "midday_cutoff": "12:00",
}

ALLOWED_USER_ROLES = {
    "admin",
    "monitor",
}


# ============================================================
# TIME HELPER
# ============================================================

def _time_string_to_object(time_string):
    """
    Convert an HH:MM or HH:MM:SS string into a time object.
    """

    if not isinstance(time_string, str):
        raise ValueError(
            "Time value must be a string."
        )

    time_string = time_string.strip()

    if not time_string:
        raise ValueError(
            "Time value cannot be empty."
        )

    if time_string.count(":") == 2:
        time_format = "%H:%M:%S"
    else:
        time_format = "%H:%M"

    return datetime.strptime(
        time_string,
        time_format,
    ).time()


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    """
    Return a SQLite connection with named-column access.

    sqlite3.Row allows values to be accessed using:

        row["column_name"]
    """

    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=10,
    )

    connection.row_factory = sqlite3.Row

    # Enable foreign-key enforcement for this connection.
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    """
    Create required tables if they do not exist.

    Existing tables and records are preserved.

    Tables:
    - settings
    - students
    - attendance
    - users
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # ----------------------------------------------------
        # Settings table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY
                    CHECK (id = 1),

                checkin_start TEXT NOT NULL
                    DEFAULT '07:00',

                checkin_ontime_end TEXT NOT NULL
                    DEFAULT '08:45',

                checkout_ontime_start TEXT NOT NULL
                    DEFAULT '17:00',

                midday_cutoff TEXT NOT NULL
                    DEFAULT '12:00'
            )
            """
        )

        cursor.execute(
            """
            INSERT OR IGNORE INTO settings
            (
                id,
                checkin_start,
                checkin_ontime_end,
                checkout_ontime_start,
                midday_cutoff
            )
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                DEFAULT_SETTINGS["checkin_start"],
                DEFAULT_SETTINGS["checkin_ontime_end"],
                DEFAULT_SETTINGS["checkout_ontime_start"],
                DEFAULT_SETTINGS["midday_cutoff"],
            ),
        )

        # ----------------------------------------------------
        # Students table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                student_code TEXT UNIQUE NOT NULL,

                full_name TEXT NOT NULL,

                email TEXT,

                department TEXT,

                year_level TEXT,

                face_folder TEXT,

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ----------------------------------------------------
        # Attendance table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                student_id INTEGER NOT NULL,

                time TEXT NOT NULL,

                date TEXT NOT NULL,

                type TEXT NOT NULL
                    CHECK (
                        type IN (
                            'check-in',
                            'check-out'
                        )
                    ),

                status TEXT NOT NULL
                    CHECK (
                        status IN (
                            'On Time',
                            'Late',
                            'Early Leave'
                        )
                    ),

                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE,

                UNIQUE (
                    student_id,
                    date,
                    type
                )
            )
            """
        )

        # ----------------------------------------------------
        # Users table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                username TEXT UNIQUE NOT NULL,

                password_hash TEXT NOT NULL,

                full_name TEXT NOT NULL,

                role TEXT NOT NULL
                    CHECK (
                        role IN (
                            'admin',
                            'monitor'
                        )
                    ),

                is_active INTEGER NOT NULL
                    DEFAULT 1
                    CHECK (
                        is_active IN (0, 1)
                    ),

                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.commit()

        print(
            f"[DATABASE] Initialized: {DB_PATH}"
        )

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not initialize database: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


# ============================================================
# USER ACCOUNT OPERATIONS
# ============================================================

def create_user(
    username,
    password,
    full_name,
    role,
):
    """
    Create a system user account.

    Supported roles:
    - admin
    - monitor

    The password is stored as a secure hash.

    Returns:
        True when the user is created.
        False when validation or insertion fails.
    """

    connection = None

    try:
        username = (
            username or ""
        ).strip().lower()

        password = password or ""

        full_name = (
            full_name or ""
        ).strip()

        role = (
            role or ""
        ).strip().lower()

        if not username:
            print(
                "[USER ERROR] "
                "Username is required."
            )

            return False

        if len(username) < 3:
            print(
                "[USER ERROR] "
                "Username must contain at least "
                "3 characters."
            )

            return False

        if not full_name:
            print(
                "[USER ERROR] "
                "Full name is required."
            )

            return False

        if len(password) < 6:
            print(
                "[USER ERROR] "
                "Password must contain at least "
                "6 characters."
            )

            return False

        if role not in ALLOWED_USER_ROLES:
            print(
                "[USER ERROR] "
                "Role must be 'admin' or 'monitor'."
            )

            return False

        password_hash = generate_password_hash(
            password
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO users
            (
                username,
                password_hash,
                full_name,
                role,
                is_active
            )
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                username,
                password_hash,
                full_name,
                role,
            ),
        )

        connection.commit()

        print(
            f"[USER] Created {role} account: "
            f"{username}"
        )

        return True

    except sqlite3.IntegrityError:
        if connection:
            connection.rollback()

        print(
            "[USER ERROR] "
            f"Username '{username}' already exists."
        )

        return False

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not create user: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def get_user(user_id):
    """
    Return one system user by ID.

    The password hash is intentionally excluded.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                username,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )

        return cursor.fetchone()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load user: {error}"
        )

        return None

    finally:
        if connection:
            connection.close()


def get_user_by_username(username):
    """
    Return one user account by username.

    This function includes password_hash because it is used
    internally for authentication.
    """

    connection = None

    try:
        username = (
            username or ""
        ).strip().lower()

        if not username:
            return None

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        )

        return cursor.fetchone()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load user account: {error}"
        )

        return None

    finally:
        if connection:
            connection.close()


def get_users():
    """
    Return all system user accounts.

    Password hashes are not returned.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                username,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            ORDER BY
                role ASC,
                full_name ASC
            """
        )

        return cursor.fetchall()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load users: {error}"
        )

        return []

    finally:
        if connection:
            connection.close()


def authenticate_user(
    username,
    password,
):
    """
    Authenticate a user with username and password.

    Returns:
        User database row when authentication succeeds.
        None when authentication fails.
    """

    username = (
        username or ""
    ).strip().lower()

    password = password or ""

    if not username or not password:
        return None

    user = get_user_by_username(
        username
    )

    if user is None:
        return None

    if user["is_active"] != 1:
        print(
            "[LOGIN] Disabled account attempted "
            f"to sign in: {username}"
        )

        return None

    password_is_correct = check_password_hash(
        user["password_hash"],
        password,
    )

    if not password_is_correct:
        return None

    return user


def update_user_password(
    user_id,
    new_password,
):
    """
    Change a user's password.

    Returns:
        True when the password is updated.
        False when validation or update fails.
    """

    connection = None

    try:
        new_password = new_password or ""

        if len(new_password) < 6:
            print(
                "[USER ERROR] "
                "New password must contain at least "
                "6 characters."
            )

            return False

        password_hash = generate_password_hash(
            new_password
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE users
            SET password_hash = ?
            WHERE id = ?
            """,
            (
                password_hash,
                user_id,
            ),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            print(
                "[USER ERROR] "
                f"User ID {user_id} does not exist."
            )

            return False

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not update password: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def set_user_active(
    user_id,
    is_active,
):
    """
    Enable or disable a system user account.

    Args:
        user_id: User database ID.
        is_active: True to enable, False to disable.
    """

    connection = None

    try:
        active_value = (
            1
            if is_active
            else 0
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE users
            SET is_active = ?
            WHERE id = ?
            """,
            (
                active_value,
                user_id,
            ),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            return False

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not update account status: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def delete_user(user_id):
    """
    Delete one system login account.

    This does not delete any student or attendance records.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM users
            WHERE id = ?
            """,
            (user_id,),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            return False

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not delete user: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


# ============================================================
# SETTINGS
# ============================================================

def get_settings():
    """
    Return the current attendance schedule settings.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                checkin_start,
                checkin_ontime_end,
                checkout_ontime_start,
                midday_cutoff
            FROM settings
            WHERE id = 1
            """
        )

        row = cursor.fetchone()

        if row is None:
            return dict(DEFAULT_SETTINGS)

        return {
            "checkin_start": row["checkin_start"],
            "checkin_ontime_end": (
                row["checkin_ontime_end"]
            ),
            "checkout_ontime_start": (
                row["checkout_ontime_start"]
            ),
            "midday_cutoff": row["midday_cutoff"],
        }

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load settings: {error}"
        )

        return dict(DEFAULT_SETTINGS)

    finally:
        if connection:
            connection.close()


def update_settings(
    checkin_start,
    checkin_ontime_end,
    checkout_ontime_start,
    midday_cutoff,
):
    """
    Validate and save attendance schedule settings.
    """

    connection = None

    try:
        start_time = _time_string_to_object(
            checkin_start
        )

        ontime_end = _time_string_to_object(
            checkin_ontime_end
        )

        checkout_start = _time_string_to_object(
            checkout_ontime_start
        )

        cutoff_time = _time_string_to_object(
            midday_cutoff
        )

        if start_time > ontime_end:
            raise ValueError(
                "Check-in start must not be later "
                "than the on-time deadline."
            )

        if ontime_end >= cutoff_time:
            raise ValueError(
                "Midday cutoff must be later than "
                "the check-in deadline."
            )

        if cutoff_time >= checkout_start:
            raise ValueError(
                "Check-out on-time threshold must "
                "be later than midday cutoff."
            )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE settings
            SET
                checkin_start = ?,
                checkin_ontime_end = ?,
                checkout_ontime_start = ?,
                midday_cutoff = ?
            WHERE id = 1
            """,
            (
                checkin_start.strip(),
                checkin_ontime_end.strip(),
                checkout_ontime_start.strip(),
                midday_cutoff.strip(),
            ),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            print(
                "[SETTINGS ERROR] "
                "Settings row is missing."
            )

            return False

        connection.commit()

        return True

    except (TypeError, ValueError) as error:
        print(
            "[SETTINGS ERROR] "
            f"Invalid settings: {error}"
        )

        return False

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not update settings: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


# ============================================================
# STUDENT OPERATIONS
# ============================================================

def add_student(
    student_code,
    full_name,
    email,
    department,
    year_level,
    face_folder,
):
    """
    Add a student.

    Returns True on success, otherwise False.
    """

    connection = None

    try:
        student_code = (
            student_code or ""
        ).strip()

        full_name = (
            full_name or ""
        ).strip()

        if not student_code:
            print(
                "[STUDENT ERROR] "
                "Student code is required."
            )

            return False

        if not full_name:
            print(
                "[STUDENT ERROR] "
                "Student full name is required."
            )

            return False

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO students
            (
                student_code,
                full_name,
                email,
                department,
                year_level,
                face_folder
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                student_code,
                full_name,
                (email or "").strip(),
                (department or "").strip(),
                (year_level or "").strip(),
                (face_folder or "").strip(),
            ),
        )

        connection.commit()

        return True

    except sqlite3.IntegrityError:
        if connection:
            connection.rollback()

        print(
            "[STUDENT ERROR] "
            f"Student code '{student_code}' "
            "already exists."
        )

        return False

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not add student: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def get_students():
    """
    Return all students ordered by full name.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                student_code,
                full_name,
                email,
                department,
                year_level,
                face_folder,
                created_at
            FROM students
            ORDER BY full_name ASC
            """
        )

        return cursor.fetchall()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load students: {error}"
        )

        return []

    finally:
        if connection:
            connection.close()


def get_student(student_id):
    """
    Return one student by database ID.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                student_code,
                full_name,
                email,
                department,
                year_level,
                face_folder,
                created_at
            FROM students
            WHERE id = ?
            """,
            (student_id,),
        )

        return cursor.fetchone()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load student: {error}"
        )

        return None

    finally:
        if connection:
            connection.close()


def update_student(
    student_id,
    student_code,
    full_name,
    email,
    department,
    year_level,
):
    """
    Update student details without changing the face folder.
    """

    connection = None

    try:
        student_code = (
            student_code or ""
        ).strip()

        full_name = (
            full_name or ""
        ).strip()

        if not student_code or not full_name:
            return False

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE students
            SET
                student_code = ?,
                full_name = ?,
                email = ?,
                department = ?,
                year_level = ?
            WHERE id = ?
            """,
            (
                student_code,
                full_name,
                (email or "").strip(),
                (department or "").strip(),
                (year_level or "").strip(),
                student_id,
            ),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            return False

        connection.commit()

        return True

    except sqlite3.IntegrityError:
        if connection:
            connection.rollback()

        print(
            "[STUDENT ERROR] "
            f"Student code '{student_code}' "
            "already exists."
        )

        return False

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not update student: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def set_face_folder(
    student_id,
    face_folder,
):
    """
    Set the dataset folder associated with a student.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE students
            SET face_folder = ?
            WHERE id = ?
            """,
            (
                (face_folder or "").strip(),
                student_id,
            ),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            return False

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not update face folder: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


def delete_student(student_id):
    """
    Delete a student and the student's attendance records.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Supports old schemas without ON DELETE CASCADE.
        cursor.execute(
            """
            DELETE FROM attendance
            WHERE student_id = ?
            """,
            (student_id,),
        )

        cursor.execute(
            """
            DELETE FROM students
            WHERE id = ?
            """,
            (student_id,),
        )

        if cursor.rowcount == 0:
            connection.rollback()

            return False

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not delete student: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


# ============================================================
# ATTENDANCE STATUS
# ============================================================

def compute_attendance_status(
    record_type,
    current_time,
    settings,
):
    """
    Calculate On Time, Late, or Early Leave status.
    """

    if isinstance(current_time, str):
        scan_time = _time_string_to_object(
            current_time
        )
    else:
        scan_time = current_time

    if record_type == "check-in":
        deadline = _time_string_to_object(
            settings["checkin_ontime_end"]
        )

        if scan_time <= deadline:
            return "On Time"

        return "Late"

    if record_type == "check-out":
        threshold = _time_string_to_object(
            settings["checkout_ontime_start"]
        )

        if scan_time >= threshold:
            return "On Time"

        return "Early Leave"

    raise ValueError(
        f"Unknown attendance type: {record_type}"
    )


# ============================================================
# RECORD ATTENDANCE
# ============================================================

def record_attendance(
    student_id,
    current_datetime=None,
):
    """
    Record check-in before midday cutoff and check-out at or
    after the cutoff.

    Returns:
        (record_type, status, time_text) when recorded.
        None for duplicates, rejected scans, or errors.
    """

    current_datetime = (
        current_datetime
        if current_datetime is not None
        else datetime.now()
    )

    connection = None

    try:
        settings = get_settings()

        current_time = current_datetime.time()

        date_text = current_datetime.strftime(
            "%Y-%m-%d"
        )

        time_text = current_datetime.strftime(
            "%H:%M:%S"
        )

        cutoff = _time_string_to_object(
            settings["midday_cutoff"]
        )

        if current_time < cutoff:
            record_type = "check-in"
        else:
            record_type = "check-out"

        # Reject a check-in before the configured start.
        if record_type == "check-in":
            checkin_start = _time_string_to_object(
                settings["checkin_start"]
            )

            if current_time < checkin_start:
                print(
                    "[ATTENDANCE] "
                    "Scan occurred before check-in start."
                )

                return None

        status = compute_attendance_status(
            record_type,
            current_time,
            settings,
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM students
            WHERE id = ?
            """,
            (student_id,),
        )

        if cursor.fetchone() is None:
            print(
                "[ATTENDANCE ERROR] "
                f"Student {student_id} does not exist."
            )

            return None

        cursor.execute(
            """
            SELECT id
            FROM attendance
            WHERE student_id = ?
              AND date = ?
              AND type = ?
            """,
            (
                student_id,
                date_text,
                record_type,
            ),
        )

        if cursor.fetchone() is not None:
            return None

        cursor.execute(
            """
            INSERT INTO attendance
            (
                student_id,
                time,
                date,
                type,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                student_id,
                time_text,
                date_text,
                record_type,
                status,
            ),
        )

        connection.commit()

        return (
            record_type,
            status,
            time_text,
        )

    except (
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as error:
        if connection:
            connection.rollback()

        print(
            "[ATTENDANCE ERROR] "
            f"Could not record attendance: {error}"
        )

        return None

    finally:
        if connection:
            connection.close()


# ============================================================
# ATTENDANCE HISTORY AND REPORTS
# ============================================================

def get_student_attendance(student_id):
    """
    Return a student's attendance history, newest first.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                date,
                time,
                type,
                status
            FROM attendance
            WHERE student_id = ?
            ORDER BY date DESC, time DESC
            """,
            (student_id,),
        )

        return cursor.fetchall()

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load attendance history: {error}"
        )

        return []

    finally:
        if connection:
            connection.close()


def get_attendance_by_date(selected_date):
    """
    Return attendance records for a selected date,
    including student names.
    """

    connection = None

    try:
        datetime.strptime(
            selected_date,
            "%Y-%m-%d",
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                students.student_code,
                students.full_name,
                attendance.time,
                attendance.date,
                attendance.type,
                attendance.status
            FROM attendance
            JOIN students
                ON students.id = attendance.student_id
            WHERE attendance.date = ?
            ORDER BY
                attendance.time ASC,
                students.full_name ASC
            """,
            (selected_date,),
        )

        return cursor.fetchall()

    except ValueError:
        print(
            "[ATTENDANCE ERROR] "
            f"Invalid date: {selected_date}"
        )

        return []

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load attendance records: {error}"
        )

        return []

    finally:
        if connection:
            connection.close()


def reset_attendance():
    """
    Delete all attendance records.

    Students, user accounts, and settings are preserved.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM attendance"
        )

        connection.commit()

        return True

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        print(
            "[DATABASE ERROR] "
            f"Could not reset attendance: {error}"
        )

        return False

    finally:
        if connection:
            connection.close()


# ============================================================
# DASHBOARD STATISTICS
# ============================================================

def get_total_students():
    """
    Return the number of registered students.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM students
            """
        )

        return cursor.fetchone()[0]

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not count students: {error}"
        )

        return 0

    finally:
        if connection:
            connection.close()


def get_attendance_stats():
    """
    Return today's dashboard statistics.

    Present:
        Unique students with a check-in today.

    Late:
        Unique students whose check-in status is Late.
    """

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT student_id)
            FROM attendance
            WHERE date = ?
              AND type = 'check-in'
            """,
            (today,),
        )

        present = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(DISTINCT student_id)
            FROM attendance
            WHERE date = ?
              AND type = 'check-in'
              AND status = 'Late'
            """,
            (today,),
        )

        late = cursor.fetchone()[0]

        return {
            "present": present,
            "late": late,
        }

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load statistics: {error}"
        )

        return {
            "present": 0,
            "late": 0,
        }

    finally:
        if connection:
            connection.close()


# ============================================================
# DIRECT FILE TEST
# ============================================================

if __name__ == "__main__":
    success = init_db()

    print(f"Database path: {DB_PATH}")
    print(
        f"Initialization successful: {success}"
    )

    if success:
        print(
            f"Total students: {get_total_students()}"
        )

        print(
            f"User accounts: {len(get_users())}"
        )

        print(
            "Today's statistics:",
            get_attendance_stats(),
        )