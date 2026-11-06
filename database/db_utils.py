"""
Database utilities for the Face Recognition Attendance System.

Features:
- SQLite database connection
- Database table initialization
- Attendance schedule settings
- Individual student CRUD operations
- Bulk student import support
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


# --------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------

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


# --------------------------------------
# GENERAL HELPERS
# --------------------------------------

def _clean_text(value):
    """
    Convert a value to a clean string.

    None becomes an empty string. Surrounding spaces are removed.
    """

    if value is None:
        return ""

    return str(value).strip()


def normalize_student_code(value):
    """
    Normalize a student code.

    Numeric student codes are padded to at least two digits:

        1     -> 01
        2     -> 02
        10    -> 10
        125   -> 125
        1.0   -> 01

    Non-numeric codes are preserved after trimming spaces.

    Examples:

        "01"      -> "01"
        " 2 "     -> "02"
        3         -> "03"
        4.0       -> "04"
        "ITE001"  -> "ITE001"
    """

    if value is None:
        return ""

    # Excel may return numeric cells as int or float.
    if isinstance(value, bool):
        return ""

    if isinstance(value, int):
        return str(value).zfill(2)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value)).zfill(2)

        return str(value).strip()

    code = str(value).strip()

    if not code:
        return ""

    # Handle text values such as "1.0" from spreadsheets.
    try:
        numeric_value = float(code)

        if numeric_value.is_integer():
            integer_text = str(int(numeric_value))

            # Only normalize when the original value is numeric.
            return integer_text.zfill(2)

    except ValueError:
        pass

    return code


def _time_string_to_object(time_string):
    """
    Convert HH:MM or HH:MM:SS text into a time object.
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

    time_format = (
        "%H:%M:%S"
        if time_string.count(":") == 2
        else "%H:%M"
    )

    return datetime.strptime(
        time_string,
        time_format,
    ).time()


# --------------------------------------
# DATABASE CONNECTION
# --------------------------------------

def get_connection():
    """
    Return a SQLite connection with named-column access.

    Example:

        row["full_name"]
    """

    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=10,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


# --------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------

def init_db():
    """
    Create all required database tables.

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


# --------------------------------------
# USER ACCOUNT OPERATIONS
# --------------------------------------

def create_user(
    username,
    password,
    full_name,
    role,
):
    """
    Create a system account.

    Supported roles:
    - admin
    - monitor
    """

    connection = None

    try:
        username = _clean_text(
            username
        ).lower()

        password = password or ""

        full_name = _clean_text(
            full_name
        )

        role = _clean_text(
            role
        ).lower()

        if not username:
            print(
                "[USER ERROR] Username is required."
            )
            return False

        if len(username) < 3:
            print(
                "[USER ERROR] Username must contain "
                "at least 3 characters."
            )
            return False

        if not full_name:
            print(
                "[USER ERROR] Full name is required."
            )
            return False

        if len(password) < 6:
            print(
                "[USER ERROR] Password must contain "
                "at least 6 characters."
            )
            return False

        if role not in ALLOWED_USER_ROLES:
            print(
                "[USER ERROR] Role must be "
                "'admin' or 'monitor'."
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
    Return one system user without the password hash.
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

    The password hash is included for authentication.
    """

    connection = None

    try:
        username = _clean_text(
            username
        ).lower()

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
    Return all accounts without their password hashes.
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
    Authenticate a user.

    Returns the user row when successful, otherwise None.
    """

    username = _clean_text(
        username
    ).lower()

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

    if not check_password_hash(
        user["password_hash"],
        password,
    ):
        return None

    return user


def update_user_password(
    user_id,
    new_password,
):
    """
    Change a user's password.
    """

    connection = None

    try:
        new_password = new_password or ""

        if len(new_password) < 6:
            print(
                "[USER ERROR] New password must "
                "contain at least 6 characters."
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
    Enable or disable a user account.
    """

    connection = None

    try:
        active_value = 1 if is_active else 0

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
    Delete a login account.

    Student and attendance records are not affected.
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


# --------------------------------------
# SETTINGS
# --------------------------------------

def get_settings():
    """
    Return the current attendance schedule.
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
            return dict(
                DEFAULT_SETTINGS
            )

        return {
            "checkin_start": row["checkin_start"],
            "checkin_ontime_end": (
                row["checkin_ontime_end"]
            ),
            "checkout_ontime_start": (
                row["checkout_ontime_start"]
            ),
            "midday_cutoff": (
                row["midday_cutoff"]
            ),
        }

    except sqlite3.Error as error:
        print(
            "[DATABASE ERROR] "
            f"Could not load settings: {error}"
        )

        return dict(
            DEFAULT_SETTINGS
        )

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
        checkin_start = _clean_text(
            checkin_start
        )

        checkin_ontime_end = _clean_text(
            checkin_ontime_end
        )

        checkout_ontime_start = _clean_text(
            checkout_ontime_start
        )

        midday_cutoff = _clean_text(
            midday_cutoff
        )

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
                checkin_start,
                checkin_ontime_end,
                checkout_ontime_start,
                midday_cutoff,
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


# --------------------------------------
# INDIVIDUAL STUDENT OPERATIONS
# --------------------------------------

def add_student(
    student_code,
    full_name,
    email,
    department,
    year_level,
    face_folder,
):
    """
    Add one student.

    Numeric codes are padded to at least two digits.

    Examples:

        1  -> 01
        2  -> 02
        10 -> 10
    """

    connection = None

    try:
        student_code = normalize_student_code(
            student_code
        )

        full_name = _clean_text(
            full_name
        )

        email = _clean_text(
            email
        )

        department = _clean_text(
            department
        )

        year_level = _clean_text(
            year_level
        )

        face_folder = _clean_text(
            face_folder
        )

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
                email,
                department,
                year_level,
                face_folder,
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


# --------------------------------------
# BULK STUDENT IMPORT
# --------------------------------------

def bulk_add_students(student_rows):
    """
    Validate and import multiple students in one transaction.

    Each row must be a dictionary with these keys:

        student_code
        full_name
        email
        department
        year_level

    Required:
        student_code
        full_name

    Optional:
        email
        department
        year_level

    Numeric codes are normalized to at least two digits:

        1   -> 01
        2   -> 02
        3   -> 03
        10  -> 10

    Existing database codes are skipped.

    Duplicate codes inside the uploaded file are skipped.

    Returns a detailed result dictionary.
    """

    result = {
        "total_rows": 0,
        "imported_count": 0,
        "duplicate_count": 0,
        "invalid_count": 0,
        "imported_students": [],
        "duplicate_rows": [],
        "invalid_rows": [],
        "success": False,
        "database_error": None,
    }

    if student_rows is None:
        result["database_error"] = (
            "No student records were provided."
        )
        return result

    try:
        student_rows = list(
            student_rows
        )

    except TypeError:
        result["database_error"] = (
            "Student records must be a list."
        )
        return result

    result["total_rows"] = len(
        student_rows
    )

    if not student_rows:
        result["database_error"] = (
            "The uploaded file contains no "
            "student records."
        )
        return result

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT student_code
            FROM students
            """
        )

        existing_codes = {
            normalize_student_code(
                row["student_code"]
            ).lower()
            for row in cursor.fetchall()
        }

        file_codes = set()
        valid_students = []

        # Excel/CSV row 1 is the header, so data begins at row 2.
        for row_number, row in enumerate(
            student_rows,
            start=2,
        ):
            if not isinstance(row, dict):
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": "",
                        "full_name": "",
                        "reason": (
                            "The row is not a valid "
                            "student record."
                        ),
                    }
                )
                continue

            student_code = normalize_student_code(
                row.get("student_code", "")
            )

            full_name = _clean_text(
                row.get("full_name", "")
            )

            email = _clean_text(
                row.get("email", "")
            )

            department = _clean_text(
                row.get("department", "")
            )

            year_level = _clean_text(
                row.get("year_level", "")
            )

            normalized_code = (
                student_code.lower()
            )

            if not student_code:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": "",
                        "full_name": full_name,
                        "reason": (
                            "Student code is required."
                        ),
                    }
                )
                continue

            if not full_name:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": "",
                        "reason": (
                            "Full name is required."
                        ),
                    }
                )
                continue

            if len(student_code) > 50:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Student code must not exceed "
                            "50 characters."
                        ),
                    }
                )
                continue

            if len(full_name) > 150:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Full name must not exceed "
                            "150 characters."
                        ),
                    }
                )
                continue

            if len(email) > 254:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Email must not exceed "
                            "254 characters."
                        ),
                    }
                )
                continue

            if len(department) > 100:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Department must not exceed "
                            "100 characters."
                        ),
                    }
                )
                continue

            if len(year_level) > 30:
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Year level must not exceed "
                            "30 characters."
                        ),
                    }
                )
                continue

            if email and (
                "@" not in email
                or email.startswith("@")
                or email.endswith("@")
            ):
                result["invalid_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Email format is invalid."
                        ),
                    }
                )
                continue

            if normalized_code in existing_codes:
                result["duplicate_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Student code already exists "
                            "in the database."
                        ),
                    }
                )
                continue

            if normalized_code in file_codes:
                result["duplicate_rows"].append(
                    {
                        "row": row_number,
                        "student_code": student_code,
                        "full_name": full_name,
                        "reason": (
                            "Student code appears more than "
                            "once in the uploaded file."
                        ),
                    }
                )
                continue

            file_codes.add(
                normalized_code
            )

            valid_students.append(
                {
                    "row": row_number,
                    "student_code": student_code,
                    "full_name": full_name,
                    "email": email,
                    "department": department,
                    "year_level": year_level,
                    "face_folder": "",
                }
            )

        for student in valid_students:
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
                    student["student_code"],
                    student["full_name"],
                    student["email"],
                    student["department"],
                    student["year_level"],
                    student["face_folder"],
                ),
            )

            result["imported_students"].append(
                {
                    "row": student["row"],
                    "student_code": (
                        student["student_code"]
                    ),
                    "full_name": (
                        student["full_name"]
                    ),
                }
            )

        connection.commit()

        result["imported_count"] = len(
            result["imported_students"]
        )

        result["duplicate_count"] = len(
            result["duplicate_rows"]
        )

        result["invalid_count"] = len(
            result["invalid_rows"]
        )

        result["success"] = True

        print(
            "[STUDENT IMPORT] "
            f"Rows: {result['total_rows']}, "
            f"Imported: {result['imported_count']}, "
            f"Duplicates: {result['duplicate_count']}, "
            f"Invalid: {result['invalid_count']}"
        )

        return result

    except sqlite3.IntegrityError as error:
        if connection:
            connection.rollback()

        result["database_error"] = (
            "The import was cancelled because a "
            "database constraint failed: "
            f"{error}"
        )

        result["imported_students"] = []
        result["imported_count"] = 0
        result["success"] = False

        print(
            "[STUDENT IMPORT ERROR] "
            f"{result['database_error']}"
        )

        return result

    except sqlite3.Error as error:
        if connection:
            connection.rollback()

        result["database_error"] = (
            "The import was cancelled because of "
            f"a database error: {error}"
        )

        result["imported_students"] = []
        result["imported_count"] = 0
        result["success"] = False

        print(
            "[DATABASE ERROR] "
            f"{result['database_error']}"
        )

        return result

    finally:
        if connection:
            connection.close()


def get_students():
    """
    Return all students ordered numerically by student code.

    Examples:
        01, 02, 03, 04, 05
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
            ORDER BY
                CAST(student_code AS INTEGER) ASC,
                student_code ASC
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
    Return one student by ID.
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
        student_code = normalize_student_code(
            student_code
        )

        full_name = _clean_text(
            full_name
        )

        email = _clean_text(
            email
        )

        department = _clean_text(
            department
        )

        year_level = _clean_text(
            year_level
        )

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
                email,
                department,
                year_level,
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
    Set the student face dataset folder.
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
                _clean_text(face_folder),
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
    Delete one student and associated attendance records.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

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


# --------------------------------------
# ATTENDANCE STATUS
# --------------------------------------

def compute_attendance_status(
    record_type,
    current_time,
    settings,
):
    """
    Calculate On Time, Late, or Early Leave.
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


# --------------------------------------
# RECORD ATTENDANCE
# --------------------------------------

def record_attendance(
    student_id,
    current_datetime=None,
):
    """
    Record check-in before the midday cutoff and check-out
    at or after the cutoff.

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

        if record_type == "check-in":
            checkin_start = _time_string_to_object(
                settings["checkin_start"]
            )

            if current_time < checkin_start:
                print(
                    "[ATTENDANCE] Scan occurred before "
                    "check-in start."
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


# --------------------------------------
# ATTENDANCE HISTORY AND REPORTS
# --------------------------------------

def get_student_attendance(student_id):
    """
    Return one student's attendance history, newest first.
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
    Return attendance records for a selected date.
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

    Students, accounts, and settings are preserved.
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


# --------------------------------------
# DASHBOARD STATISTICS
# --------------------------------------

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
    Return today's attendance statistics.

    Present:
        Unique students with a check-in today.

    Late:
        Unique students whose check-in is Late.
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


# --------------------------------------
# DIRECT FILE TEST
# --------------------------------------

if __name__ == "__main__":
    success = init_db()

    print(
        f"Database path: {DB_PATH}"
    )

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