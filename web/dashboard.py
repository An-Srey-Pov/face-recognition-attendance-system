"""
Attendance dashboard routes for the Face Recognition Attendance System.

Features:
- Attendance dashboard statistics
- Attendance search by date
- CSV export
- Attendance schedule settings
- Attendance record reset
- Administrator and Class Monitor permissions

Permissions:
- Admin:
    View dashboard
    Search attendance
    Export CSV
    Update settings
    Reset attendance records

- Monitor:
    View dashboard
    Search attendance
    Export CSV
    Cannot update settings
    Cannot reset attendance records
"""

import csv
import io
from datetime import date, datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    g,
    redirect,
    request,
    url_for,
)

from flask import render_template

from database.db_utils import (
    get_attendance_by_date,
    get_attendance_stats,
    get_settings,
    get_total_students,
    reset_attendance,
    update_settings,
)


# ------------------------------------
# BLUEPRINT
# ------------------------------------

dashboard = Blueprint(
    "dashboard",
    __name__,
)


# ------------------------------------
# ACCESS CONTROL HELPERS
# ------------------------------------

def require_login():
    """
    Require a logged-in account.

    Returns:
        A redirect response when the user is not logged in.
        None when the user is logged in.
    """

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

    return None


def require_admin():
    """
    Require an Administrator account.

    The Class Monitor receives a 403 Access Denied page.
    """

    login_response = require_login()

    if login_response is not None:
        return login_response

    if g.current_user["role"] != "admin":
        abort(403)

    return None


# ------------------------------------
# PROTECT ALL DASHBOARD ROUTES
# ------------------------------------

@dashboard.before_request
def protect_dashboard():
    """
    Require login for every route in this Blueprint.

    Admin-only permissions are checked separately inside
    the settings and reset routes.
    """

    return require_login()


# ------------------------------------
# DATE VALIDATION
# ------------------------------------

def validate_date(date_text):
    """
    Validate a date using YYYY-MM-DD format.

    Returns:
        A standardized date string.

    Raises:
        ValueError when the date is invalid.
    """

    parsed_date = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )

    return parsed_date.strftime(
        "%Y-%m-%d"
    )


# ------------------------------------
# ATTENDANCE DASHBOARD
# ------------------------------------

@dashboard.route(
    "/attendance",
    methods=["GET", "POST"],
)
def attendance():
    """
    Display attendance records and dashboard statistics.

    Both Admin and Class Monitor accounts can access this page.
    """

    total_students = get_total_students()
    stats = get_attendance_stats()
    settings_data = get_settings()

    attendance_data = []

    # Display today's records initially.
    selected_date = request.args.get(
        "selected_date",
        date.today().isoformat(),
    ).strip()

    no_data = False
    date_error = None

    # These values are used by attendance.html to display
    # success or error alerts.
    settings_saved = request.args.get(
        "saved"
    )

    records_reset = request.args.get(
        "reset"
    )

    # The date may be submitted through the date form.
    if request.method == "POST":
        selected_date = request.form.get(
            "selected_date",
            "",
        ).strip()

    if not selected_date:
        date_error = "Please select a date."
        selected_date = date.today().isoformat()

    else:
        try:
            selected_date = validate_date(
                selected_date
            )

            records = get_attendance_by_date(
                selected_date
            )

            # attendance.html currently expects:
            #
            # name, time, type, status
            #
            # Therefore, convert database rows into the
            # structure expected by the template.
            attendance_data = [
                (
                    record["full_name"],
                    record["time"],
                    record["type"],
                    record["status"],
                )
                for record in records
            ]

            no_data = not attendance_data

        except ValueError:
            date_error = (
                "Invalid date. "
                "Please select a valid date."
            )

            attendance_data = []
            no_data = True

    return render_template(
        "attendance.html",
        attendance_data=attendance_data,
        selected_date=selected_date,
        no_data=no_data,
        date_error=date_error,
        settings=settings_data,
        total_students=total_students,
        stats=stats,
        settings_saved=settings_saved,
        records_reset=records_reset,
    )


# ------------------------------------
# EXPORT ATTENDANCE TO CSV
# ------------------------------------

@dashboard.route(
    "/export",
    methods=["GET"],
)
def export():
    """
    Export attendance records for one date as CSV.

    Both Admin and Class Monitor accounts may export reports.
    """

    selected_date = request.args.get(
        "selected_date",
        "",
    ).strip()

    if not selected_date:
        return "Missing date.", 400

    try:
        formatted_date = validate_date(
            selected_date
        )

    except ValueError:
        return (
            "Invalid date. Use YYYY-MM-DD format.",
            400,
        )

    attendance_records = get_attendance_by_date(
        formatted_date
    )

    output = io.StringIO()

    writer = csv.writer(
        output,
        lineterminator="\n",
    )

    writer.writerow(
        [
            "Student Code",
            "Full Name",
            "Time",
            "Date",
            "Type",
            "Status",
        ]
    )

    for record in attendance_records:
        record_type = (
            "Check-In"
            if record["type"] == "check-in"
            else "Check-Out"
        )

        writer.writerow(
            [
                record["student_code"],
                record["full_name"],
                record["time"],
                record["date"],
                record_type,
                record["status"],
            ]
        )

    # Add a UTF-8 byte-order mark so Microsoft Excel
    # recognizes UTF-8 student names correctly.
    csv_data = "\ufeff" + output.getvalue()

    output.close()

    filename = (
        f"attendance_{formatted_date}.csv"
    )

    return Response(
        csv_data,
        content_type=(
            "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )


# ------------------------------------
# UPDATE ATTENDANCE SETTINGS
# ------------------------------------

@dashboard.route(
    "/settings",
    methods=["POST"],
)
def settings():
    """
    Update attendance time settings.

    Administrator access only.
    """

    admin_response = require_admin()

    if admin_response is not None:
        return admin_response

    checkin_start = request.form.get(
        "checkin_start",
        "",
    ).strip()

    checkin_ontime_end = request.form.get(
        "checkin_ontime_end",
        "",
    ).strip()

    checkout_ontime_start = request.form.get(
        "checkout_ontime_start",
        "",
    ).strip()

    midday_cutoff = request.form.get(
        "midday_cutoff",
        "",
    ).strip()

    success = update_settings(
        checkin_start,
        checkin_ontime_end,
        checkout_ontime_start,
        midday_cutoff,
    )

    if success:
        flash(
            "Attendance settings were saved successfully.",
            "success",
        )

    else:
        flash(
            "Could not save attendance settings. "
            "Please check the selected times.",
            "error",
        )

    return redirect(
        url_for(
            "dashboard.attendance",
            saved=1 if success else 0,
        )
    )


# ------------------------------------
# RESET ATTENDANCE
# ------------------------------------

@dashboard.route(
    "/reset",
    methods=["POST"],
)
def reset():
    """
    Delete all attendance records.

    Administrator access only.

    Student information, settings, and user accounts
    are preserved.
    """

    admin_response = require_admin()

    if admin_response is not None:
        return admin_response

    success = reset_attendance()

    if success:
        flash(
            "All attendance records were deleted.",
            "success",
        )

    else:
        flash(
            "Could not reset attendance records.",
            "error",
        )

    return redirect(
        url_for(
            "dashboard.attendance",
            reset=1 if success else 0,
        )
    )