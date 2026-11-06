"""
Face Recognition Attendance System.

Features:
- Administrator and Class Monitor authentication
- Role-Based Access Control
- Attendance dashboard
- Individual student registration
- Bulk student import from CSV and Excel
- Student profiles
- Student attendance history
- Student edit and delete operations

Roles:
- admin:
    Full access to students, imports, settings, and attendance.

- monitor:
    Read-only access to student and attendance information.
"""

import csv
import io
import os
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin, urlparse

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from openpyxl import load_workbook

from database.db_utils import (
    add_student,
    authenticate_user,
    bulk_add_students,
    delete_student,
    get_student,
    get_student_attendance,
    get_students,
    get_user,
    init_db,
    update_student,
)

from web.dashboard import dashboard


# --------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent

ALLOWED_IMPORT_EXTENSIONS = {
    ".csv",
    ".xlsx",
}

MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024

REQUIRED_IMPORT_COLUMNS = {
    "student_code",
    "full_name",
}

OPTIONAL_IMPORT_COLUMNS = {
    "email",
    "department",
    "year_level",
}

ALL_IMPORT_COLUMNS = [
    "student_code",
    "full_name",
    "email",
    "department",
    "year_level",
]


app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
)

app.config["SECRET_KEY"] = os.environ.get(
    "FACE_ATTENDANCE_SECRET_KEY",
    "development-secret-key-change-before-deployment",
)

# Maximum upload size: 5 MB.
app.config["MAX_CONTENT_LENGTH"] = (
    MAX_IMPORT_FILE_SIZE
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Keep False when using local HTTP.
# Set True only when the website is served through HTTPS.
app.config["SESSION_COOKIE_SECURE"] = False


database_ready = init_db()

if not database_ready:
    raise RuntimeError(
        "The database could not be initialized."
    )


app.register_blueprint(
    dashboard
)


# --------------------------------------
# AUTHENTICATION HELPERS
# --------------------------------------

def is_safe_redirect_url(target):
    """
    Confirm that a redirect URL belongs to this application.

    This prevents external redirect attacks through the
    login page's next parameter.
    """

    if not target:
        return False

    host_url = urlparse(
        request.host_url
    )

    redirect_url = urlparse(
        urljoin(
            request.host_url,
            target,
        )
    )

    return (
        redirect_url.scheme in ("http", "https")
        and host_url.netloc == redirect_url.netloc
    )


def login_required(view_function):
    """
    Require a valid authenticated user.
    """

    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if g.current_user is None:
            flash(
                "Please sign in to continue.",
                "warning",
            )

            return redirect(
                url_for(
                    "login",
                    next=request.url,
                )
            )

        return view_function(
            *args,
            **kwargs,
        )

    return wrapped_view


def admin_required(view_function):
    """
    Require an Administrator account.

    A Class Monitor receives a 403 response.
    """

    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if g.current_user is None:
            flash(
                "Please sign in to continue.",
                "warning",
            )

            return redirect(
                url_for(
                    "login",
                    next=request.url,
                )
            )

        if g.current_user["role"] != "admin":
            abort(403)

        return view_function(
            *args,
            **kwargs,
        )

    return wrapped_view


# --------------------------------------
# CURRENT USER
# --------------------------------------

@app.before_request
def load_current_user():
    """
    Load the authenticated user before every request.
    """

    user_id = session.get(
        "user_id"
    )

    if user_id is None:
        g.current_user = None
        return

    user = get_user(
        user_id
    )

    if (
        user is None
        or user["is_active"] != 1
    ):
        session.clear()
        g.current_user = None
        return

    g.current_user = user


# --------------------------------------
# TEMPLATE CONTEXT
# --------------------------------------

@app.context_processor
def inject_current_user():
    """
    Make current_user available in every template.
    """

    return {
        "current_user": g.current_user,
    }


# --------------------------------------
# STUDENT IMPORT HELPERS
# --------------------------------------

def normalize_column_name(column_name):
    """
    Normalize a CSV or Excel column heading.

    Examples:

        Student Code    -> student_code
        Full-Name       -> full_name
        YEAR LEVEL      -> year_level
        Year of Study   -> year_level
    """

    if column_name is None:
        return ""

    normalized = str(
        column_name
    ).strip().lower()

    normalized = normalized.replace(
        "-",
        "_",
    )

    normalized = normalized.replace(
        " ",
        "_",
    )

    while "__" in normalized:
        normalized = normalized.replace(
            "__",
            "_",
        )

    aliases = {
        "student_id": "student_code",
        "student_number": "student_code",
        "student_no": "student_code",
        "code": "student_code",
        "name": "full_name",
        "student_name": "full_name",
        "fullname": "full_name",
        "year": "year_level",
        "year_of_study": "year_level",
        "year_of_studies": "year_level",
        "study_year": "year_level",
        "level": "year_level",
        "e_mail": "email",
    }

    return aliases.get(
        normalized,
        normalized,
    )


def allowed_import_file(filename):
    """
    Return True when the file extension is supported.
    """

    if not filename:
        return False

    extension = Path(
        filename
    ).suffix.lower()

    return extension in ALLOWED_IMPORT_EXTENSIONS


def validate_import_columns(column_names):
    """
    Normalize and validate uploaded file headings.

    Returns:
        normalized column names

    Raises:
        ValueError when required columns are missing.
    """

    normalized_columns = [
        normalize_column_name(column_name)
        for column_name in column_names
    ]

    available_columns = {
        column
        for column in normalized_columns
        if column
    }

    missing_columns = (
        REQUIRED_IMPORT_COLUMNS
        - available_columns
    )

    if missing_columns:
        missing_text = ", ".join(
            sorted(missing_columns)
        )

        raise ValueError(
            "The uploaded file is missing required "
            f"column(s): {missing_text}."
        )

    return normalized_columns


def normalize_import_row(row):
    """
    Return one import row with the expected database keys.
    """

    return {
        column: row.get(column, "")
        for column in ALL_IMPORT_COLUMNS
    }


def parse_csv_students(uploaded_file):
    """
    Read student records from a UTF-8 CSV file.

    UTF-8 with BOM is supported through utf-8-sig.
    """

    file_bytes = uploaded_file.read()

    if not file_bytes:
        raise ValueError(
            "The uploaded CSV file is empty."
        )

    try:
        file_text = file_bytes.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError as error:
        raise ValueError(
            "The CSV file must use UTF-8 encoding."
        ) from error

    csv_stream = io.StringIO(
        file_text,
        newline="",
    )

    reader = csv.reader(
        csv_stream
    )

    try:
        original_headers = next(
            reader
        )

    except StopIteration as error:
        raise ValueError(
            "The uploaded CSV file is empty."
        ) from error

    normalized_headers = validate_import_columns(
        original_headers
    )

    student_rows = []

    for values in reader:
        # Ignore completely empty rows.
        if not any(
            str(value).strip()
            for value in values
        ):
            continue

        row_data = {}

        for index, column_name in enumerate(
            normalized_headers
        ):
            if not column_name:
                continue

            value = (
                values[index]
                if index < len(values)
                else ""
            )

            row_data[column_name] = value

        student_rows.append(
            normalize_import_row(
                row_data
            )
        )

    return student_rows


def parse_excel_students(uploaded_file):
    """
    Read student records from the first worksheet of an
    .xlsx workbook.

    The workbook is opened with data_only=True so formula cells
    return their last calculated values rather than formulas.
    """

    try:
        workbook = load_workbook(
            uploaded_file,
            read_only=True,
            data_only=True,
        )

    except Exception as error:
        raise ValueError(
            "The Excel file could not be read. "
            "Make sure it is a valid .xlsx file."
        ) from error

    try:
        worksheet = workbook.active

        row_iterator = worksheet.iter_rows(
            values_only=True
        )

        try:
            original_headers = next(
                row_iterator
            )

        except StopIteration as error:
            raise ValueError(
                "The uploaded Excel file is empty."
            ) from error

        normalized_headers = validate_import_columns(
            original_headers
        )

        student_rows = []

        for values in row_iterator:
            # Ignore completely empty worksheet rows.
            if not any(
                value is not None
                and str(value).strip()
                for value in values
            ):
                continue

            row_data = {}

            for index, column_name in enumerate(
                normalized_headers
            ):
                if not column_name:
                    continue

                value = (
                    values[index]
                    if index < len(values)
                    else ""
                )

                row_data[column_name] = (
                    ""
                    if value is None
                    else value
                )

            student_rows.append(
                normalize_import_row(
                    row_data
                )
            )

        return student_rows

    finally:
        workbook.close()


def parse_student_import_file(uploaded_file):
    """
    Select the correct parser using the uploaded extension.
    """

    extension = Path(
        uploaded_file.filename
    ).suffix.lower()

    if extension == ".csv":
        return parse_csv_students(
            uploaded_file
        )

    if extension == ".xlsx":
        return parse_excel_students(
            uploaded_file
        )

    raise ValueError(
        "Unsupported file format. "
        "Upload a .csv or .xlsx file."
    )


# --------------------------------------
# LOGIN
# --------------------------------------

@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():
    """
    Authenticate an Administrator or Class Monitor.
    """

    if g.current_user is not None:
        return redirect(
            url_for(
                "dashboard.attendance"
            )
        )

    error = None

    if request.method == "POST":
        username = request.form.get(
            "username",
            "",
        ).strip()

        password = request.form.get(
            "password",
            "",
        )

        user = authenticate_user(
            username,
            password,
        )

        if user is None:
            error = (
                "Invalid username or password, "
                "or the account is disabled."
            )

        else:
            session.clear()

            session["user_id"] = user["id"]
            session["role"] = user["role"]

            flash(
                f"Welcome, {user['full_name']}.",
                "success",
            )

            next_url = request.args.get(
                "next"
            )

            if is_safe_redirect_url(
                next_url
            ):
                return redirect(
                    next_url
                )

            return redirect(
                url_for(
                    "dashboard.attendance"
                )
            )

    return render_template(
        "login.html",
        error=error,
    )


# --------------------------------------
# LOGOUT
# --------------------------------------

@app.route(
    "/logout",
    methods=["POST"],
)
@login_required
def logout():
    """
    End the current authenticated session.
    """

    session.clear()

    flash(
        "You have been signed out.",
        "success",
    )

    return redirect(
        url_for(
            "login"
        )
    )


# --------------------------------------
# HOME
# --------------------------------------

@app.route("/")
def home():
    """
    Redirect to Login or Attendance Dashboard.
    """

    if g.current_user is None:
        return redirect(
            url_for(
                "login"
            )
        )

    return redirect(
        url_for(
            "dashboard.attendance"
        )
    )


# --------------------------------------
# STUDENT LIST
# --------------------------------------

@app.route("/students")
@login_required
def students():
    """
    Display all registered students.
    """

    student_list = get_students()

    return render_template(
        "students.html",
        students=student_list,
    )


# --------------------------------------
# STUDENT PROFILE
# --------------------------------------

@app.route("/student/<int:id>")
@login_required
def student_profile(id):
    """
    Display student information and attendance history.
    """

    student = get_student(
        id
    )

    if student is None:
        abort(404)

    # --------------------------------------------------------
    # Count registered face photographs
    # --------------------------------------------------------

    photo_count = 0

    face_folder_value = student[
        "face_folder"
    ]

    if face_folder_value:
        face_folder = Path(
            face_folder_value
        )

        if not face_folder.is_absolute():
            face_folder = (
                PROJECT_ROOT
                / face_folder
            )

        if face_folder.is_dir():
            allowed_extensions = {
                ".jpg",
                ".jpeg",
                ".png",
                ".bmp",
            }

            photo_count = sum(
                1
                for file_path in face_folder.iterdir()
                if (
                    file_path.is_file()
                    and file_path.suffix.lower()
                    in allowed_extensions
                )
            )

    # --------------------------------------------------------
    # Attendance history and statistics
    # --------------------------------------------------------

    records = get_student_attendance(
        id
    )

    checkin_records = [
        record
        for record in records
        if record["type"] == "check-in"
    ]

    checkout_records = [
        record
        for record in records
        if record["type"] == "check-out"
    ]

    attendance_days = len(
        checkin_records
    )

    on_time_checkin_count = sum(
        1
        for record in checkin_records
        if record["status"] == "On Time"
    )

    late_count = sum(
        1
        for record in checkin_records
        if record["status"] == "Late"
    )

    on_time_checkout_count = sum(
        1
        for record in checkout_records
        if record["status"] == "On Time"
    )

    early_leave_count = sum(
        1
        for record in checkout_records
        if record["status"] == "Early Leave"
    )

    last_record = (
        records[0]
        if records
        else None
    )

    return render_template(
        "student_profile.html",
        student=student,
        photo_count=photo_count,
        records=records,
        attendance_days=attendance_days,
        on_time_checkin_count=on_time_checkin_count,
        late_count=late_count,
        on_time_checkout_count=on_time_checkout_count,
        early_leave_count=early_leave_count,
        last_record=last_record,
    )


# --------------------------------------
# ADD ONE STUDENT
# --------------------------------------

@app.route(
    "/add-student",
    methods=["GET", "POST"],
)
@admin_required
def add_student_page():
    """
    Display and process the individual student form.

    Administrator only.
    """

    if request.method == "POST":
        student_code = request.form.get(
            "student_code",
            "",
        ).strip()

        full_name = request.form.get(
            "full_name",
            "",
        ).strip()

        email = request.form.get(
            "email",
            "",
        ).strip()

        department = request.form.get(
            "department",
            "",
        ).strip()

        year_level = request.form.get(
            "year_level",
            "",
        ).strip()

        if not student_code:
            return render_template(
                "add_student.html",
                error="Student code is required.",
            )

        if not full_name:
            return render_template(
                "add_student.html",
                error="Full name is required.",
            )

        success = add_student(
            student_code,
            full_name,
            email,
            department,
            year_level,
            "",
        )

        if not success:
            return render_template(
                "add_student.html",
                error=(
                    "Could not add the student. "
                    "The student code may already exist."
                ),
            )

        flash(
            "Student added successfully.",
            "success",
        )

        return redirect(
            url_for(
                "students"
            )
        )

    return render_template(
        "add_student.html"
    )


# --------------------------------------
# BULK STUDENT IMPORT
# --------------------------------------

@app.route(
    "/import-students",
    methods=["GET", "POST"],
)
@admin_required
def import_students():
    """
    Upload and import a CSV or Excel student list.

    Administrator only.
    """

    import_result = None
    upload_error = None

    if request.method == "POST":
        uploaded_file = request.files.get(
            "student_file"
        )

        if (
            uploaded_file is None
            or not uploaded_file.filename
        ):
            upload_error = (
                "Please select a CSV or Excel file."
            )

        elif not allowed_import_file(
            uploaded_file.filename
        ):
            upload_error = (
                "Unsupported file format. "
                "Upload a .csv or .xlsx file."
            )

        else:
            try:
                student_rows = (
                    parse_student_import_file(
                        uploaded_file
                    )
                )

                if not student_rows:
                    upload_error = (
                        "The uploaded file contains no "
                        "student records."
                    )

                else:
                    import_result = bulk_add_students(
                        student_rows
                    )

                    if import_result["success"]:
                        imported_count = import_result[
                            "imported_count"
                        ]

                        duplicate_count = import_result[
                            "duplicate_count"
                        ]

                        invalid_count = import_result[
                            "invalid_count"
                        ]

                        flash(
                            "Import completed. "
                            f"Imported: {imported_count}, "
                            f"Duplicates: {duplicate_count}, "
                            f"Invalid: {invalid_count}.",
                            (
                                "success"
                                if imported_count > 0
                                else "warning"
                            ),
                        )

                    else:
                        upload_error = (
                            import_result.get(
                                "database_error"
                            )
                            or "The student import failed."
                        )

            except ValueError as error:
                upload_error = str(
                    error
                )

            except Exception:
                app.logger.exception(
                    "Unexpected student import error."
                )

                upload_error = (
                    "An unexpected error occurred while "
                    "reading the uploaded file."
                )

    return render_template(
        "import_students.html",
        import_result=import_result,
        upload_error=upload_error,
        required_columns=sorted(
            REQUIRED_IMPORT_COLUMNS
        ),
        optional_columns=sorted(
            OPTIONAL_IMPORT_COLUMNS
        ),
        maximum_file_size_mb=5,
    )


# --------------------------------------
# DOWNLOAD STUDENT IMPORT TEMPLATE
# --------------------------------------

@app.route(
    "/student-import-template.csv",
    methods=["GET"],
)
@admin_required
def download_student_import_template():
    """
    Download a UTF-8 CSV template for student bulk import.

    Administrator only.
    """

    output = io.StringIO(
        newline=""
    )

    writer = csv.writer(
        output,
        lineterminator="\n",
    )

    writer.writerow(
        ALL_IMPORT_COLUMNS
    )

    writer.writerow(
        [
            "01",
            "Example Student One",
            "student1@example.com",
            "ITE",
            "3",
        ]
    )

    writer.writerow(
        [
            "02",
            "Example Student Two",
            "student2@example.com",
            "ITE",
            "3",
        ]
    )

    csv_content = (
        "\ufeff"
        + output.getvalue()
    )

    output.close()

    return Response(
        csv_content,
        content_type=(
            "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                'attachment; filename="'
                'student_import_template.csv"'
            ),
        },
    )


# --------------------------------------
# EDIT STUDENT
# --------------------------------------

@app.route(
    "/edit-student/<int:id>",
    methods=["GET", "POST"],
)
@admin_required
def edit_student(id):
    """
    Edit one student's information.

    Administrator only.
    """

    student = get_student(
        id
    )

    if student is None:
        abort(404)

    if request.method == "POST":
        student_code = request.form.get(
            "student_code",
            "",
        ).strip()

        full_name = request.form.get(
            "full_name",
            "",
        ).strip()

        email = request.form.get(
            "email",
            "",
        ).strip()

        department = request.form.get(
            "department",
            "",
        ).strip()

        year_level = request.form.get(
            "year_level",
            "",
        ).strip()

        if not student_code:
            return render_template(
                "edit_student.html",
                student=student,
                error="Student code is required.",
            )

        if not full_name:
            return render_template(
                "edit_student.html",
                student=student,
                error="Full name is required.",
            )

        success = update_student(
            id,
            student_code,
            full_name,
            email,
            department,
            year_level,
        )

        if not success:
            return render_template(
                "edit_student.html",
                student=student,
                error=(
                    "Could not save the student. "
                    "The student code may already exist."
                ),
            )

        flash(
            "Student updated successfully.",
            "success",
        )

        return redirect(
            url_for(
                "student_profile",
                id=id,
            )
        )

    return render_template(
        "edit_student.html",
        student=student,
    )


# --------------------------------------
# DELETE STUDENT
# --------------------------------------

@app.route(
    "/delete-student/<int:id>",
    methods=["POST"],
)
@admin_required
def delete_student_route(id):
    """
    Delete one student and related attendance records.

    Administrator only.
    """

    student = get_student(
        id
    )

    if student is None:
        abort(404)

    success = delete_student(
        id
    )

    if not success:
        abort(500)

    flash(
        "Student deleted successfully.",
        "success",
    )

    return redirect(
        url_for(
            "students"
        )
    )


# --------------------------------------
# ERROR HANDLERS
# --------------------------------------

@app.errorhandler(403)
def forbidden(error):
    """
    Display the access-denied page.
    """

    return render_template(
        "access.html"
    ), 403


@app.errorhandler(404)
def page_not_found(error):
    """
    Return a missing-page response.
    """

    return (
        "Page not found",
        404,
    )


@app.errorhandler(413)
def uploaded_file_too_large(error):
    """
    Handle files larger than the 5 MB upload limit.
    """

    return render_template(
        "import_students.html",
        import_result=None,
        upload_error=(
            "The uploaded file is too large. "
            "The maximum allowed size is 5 MB."
        ),
        required_columns=sorted(
            REQUIRED_IMPORT_COLUMNS
        ),
        optional_columns=sorted(
            OPTIONAL_IMPORT_COLUMNS
        ),
        maximum_file_size_mb=5,
    ), 413


@app.errorhandler(500)
def internal_server_error(error):
    """
    Return an internal-server-error response.
    """

    return (
        "Internal server error",
        500,
    )


# --------------------------------------
# RUN APPLICATION
# --------------------------------------

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )