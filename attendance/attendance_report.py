import argparse
import csv
from datetime import date
from pathlib import Path

from database.db_utils import get_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "csv"


def export_attendance_report(report_date):
    connection = get_connection()
    rows = connection.execute(
        "SELECT name, time, date, type, status FROM attendance "
        "WHERE date = ? ORDER BY time",
        (report_date,),
    ).fetchall()
    connection.close()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"attendance_{report_date}.csv"
    with report_path.open("w", newline="", encoding="utf-8") as report_file:
        writer = csv.writer(report_file)
        writer.writerow(["Name", "Time", "Date", "Type", "Status"])
        writer.writerows(rows)
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Export an attendance report as CSV.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Report date (YYYY-MM-DD)")
    args = parser.parse_args()
    print(export_attendance_report(args.date))


if __name__ == "__main__":
    main()