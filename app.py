"""Face Recognition Attendance System
Roles:
- admin:
    Full access to students, settings, and attendance records.

- monitor:
    Read-only access to student information and attendance records.
"""

import os
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin, urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from database.db_utils import (
    add_student,
    authenticate_user,
    delete_student,
    get_student,
    get_student_attendance,
    get_students,
    get_user,
    init_db,
    update_student,
)

from web.dashboard import dashboard


# ------------------------------------
# APPLICATION CONFIGURATION
# ------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static",
)


# In production, set FACE_ATTENDANCE_SECRET_KEY
# as an environment variable.
app.config["SECRET_KEY"] = os.environ.get(
    "FACE_ATTENDANCE_SECRET_KEY",
    "development-secret-key-change-before-deployment",
)

# Session cookie security settings.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Keep False during local HTTP development.
# Change to True only when the application uses HTTPS.
app.config["SESSION_COOKIE_SECURE"] = False


# Create missing database tables, including the users table.
database_ready = init_db()

if not database_ready:
    raise RuntimeError(
        "The database could not be initialized."
    )


# Register attendance dashboard routes.
app.register_blueprint(dashboard)


# ------------------------------------
# AUTHENTICATION HELPERS
# ------------------------------------

def is_safe_redirect_url(target):
    """
    Verify that a redirect URL belongs to this application.

    This prevents a malicious external URL from being supplied
    through the login page's next parameter.
    """

    if not target:
        return False

    host_url = urlparse(request.host_url)

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
    Require a valid logged-in user.

    Logged-out users are redirected to the login page.
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
            **kwargs
        )

    return wrapped_view


def admin_required(view_function):
    """
    Require an administrator account.

    Class Monitor accounts receive a 403 Forbidden response.
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
            **kwargs
        )

    return wrapped_view

# ------------------------------------
# LOAD CURRENT USER
# ------------------------------------

@app.before_request
def load_current_user():
    """
    Load the logged-in user before every request.

    The session stores only the user ID. Current user information
    is reloaded from the database on each request.
    """

    user_id = session.get("user_id")

    if user_id is None:
        g.current_user = None
        return

    user = get_user(user_id)

    if user is None or user["is_active"] != 1:
        session.clear()
        g.current_user = None
        return

    g.current_user = user


# ------------------------------------
# TEMPLATE CONTEXT
# ------------------------------------

@app.context_processor
def inject_current_user():
    """
    Make current_user available in every Jinja template.

    Template example:

        {% if current_user.role == "admin" %}
            Admin-only controls
        {% endif %}
    """

    return {
        "current_user": g.current_user,
    }


# ------------------------------------
# LOGIN
# ------------------------------------

@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():
    """
    Authenticate an Admin or Class Monitor account.
    """

    if g.current_user is not None:
        return redirect(
            url_for("dashboard.attendance")
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
            # Remove any previous session information.
            session.clear()

            session["user_id"] = user["id"]

            # Remember the role for convenience.
            # Authorization still uses the current database row.
            session["role"] = user["role"]

            flash(
                f"Welcome, {user['full_name']}.",
                "success",
            )

            next_url = request.args.get(
                "next"
            )

            if is_safe_redirect_url(next_url):
                return redirect(next_url)

            return redirect(
                url_for("dashboard.attendance")
            )

    return render_template(
        "login.html",
        error=error,
    )


# ------------------------------------
# LOGOUT
# ------------------------------------

@app.route(
    "/logout",
    methods=["POST"],
)
@login_required
def logout():
    """
    End the current login session.
    """

    session.clear()

    flash(
        "You have been signed out.",
        "success",
    )

    return redirect(
        url_for("login")
    )


# ------------------------------------
# HOME
# ------------------------------------

@app.route("/")
def home():
    """
    Send logged-in users to the Attendance Dashboard.

    Send logged-out users to the Login page.
    """

    if g.current_user is None:
        return redirect(
            url_for("login")
        )

    return redirect(
        url_for("dashboard.attendance")
    )


# ------------------------------------
# STUDENT LIST
# ------------------------------------

@app.route("/students")
@login_required
def students():
    """
    Display all registered students.

    Admin:
        Sees full management controls in the template.

    Monitor:
        Sees the same student list without modification controls.
    """

    student_list = get_students()

    return render_template(
        "students.html",
        students=student_list,
    )


# ------------------------------------
# STUDENT PROFILE
# ------------------------------------

@app.route("/student/<int:id>")
@login_required
def student_profile(id):
    """
    Display one student's information, face-registration status,
    attendance summary, and complete attendance history.

    Both Admin and Class Monitor may view this page.
    """

    student = get_student(id)

    if student is None:
        abort(404)

    # --------------------------------------------------------
    # Count registered face photos
    # --------------------------------------------------------

    photo_count = 0

    face_folder_value = student["face_folder"]

    if face_folder_value:
        face_folder = Path(
            face_folder_value
        )

        # Support both absolute and project-relative paths.
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
    # Attendance history
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

    # --------------------------------------------------------
    # Check-in statistics
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Check-out statistics
    # --------------------------------------------------------

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


# ------------------------------------
# ADD STUDENT
# ------------------------------------

@app.route(
    "/add-student",
    methods=["GET", "POST"],
)
@admin_required
def add_student_page():
    """
    Display and process the Add Student form.

    Admin only.
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
            url_for("students")
        )

    return render_template(
        "add_student.html"
    )


# ------------------------------------
# EDIT STUDENT
# ------------------------------------

@app.route(
    "/edit-student/<int:id>",
    methods=["GET", "POST"],
)
@admin_required
def edit_student(id):
    """
    Display and process the Edit Student form.

    Admin only.
    """

    student = get_student(id)

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


# ------------------------------------
# DELETE STUDENT
# ------------------------------------

@app.route(
    "/delete-student/<int:id>",
    methods=["POST"],
)
@admin_required
def delete_student_route(id):
    """
    Delete a student and related attendance records.

    Admin only.
    """

    student = get_student(id)

    if student is None:
        abort(404)

    success = delete_student(id)

    if not success:
        abort(500)

    flash(
        "Student deleted successfully.",
        "success",
    )

    return redirect(
        url_for("students")
    )


# ------------------------------------
# ERROR HANDLERS
# ------------------------------------

@app.errorhandler(403)
def forbidden(error):
    """
    Display the existing access-denied page.
    """

    return render_template(
        "access.html"
    ), 403


@app.errorhandler(404)
def page_not_found(error):
    """
    Return a simple missing-page response.
    """

    return "Page not found", 404


@app.errorhandler(500)
def internal_server_error(error):
    """
    Return a simple internal-error response.
    """

    return "Internal server error", 500


# ------------------------------------
# RUN APPLICATION
# ------------------------------------

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )