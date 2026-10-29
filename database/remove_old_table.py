import sqlite3

from db_utils import DB_PATH


def remove_attendance_old():
    connection = None

    try:
        connection = sqlite3.connect(DB_PATH)
        cursor = connection.cursor()

        # Confirm whether the old table exists.
        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'attendance_old'
            """
        )

        table_exists = cursor.fetchone() is not None

        if not table_exists:
            print("attendance_old does not exist.")
            return

        # Display how many old records will be removed.
        cursor.execute("SELECT COUNT(*) FROM attendance_old")
        record_count = cursor.fetchone()[0]

        print(
            f"attendance_old contains {record_count} record(s)."
        )

        # Delete the old migration table.
        cursor.execute("DROP TABLE attendance_old")

        connection.commit()

        print("attendance_old was deleted successfully.")

    except sqlite3.Error as error:
        print(f"[DATABASE ERROR] {error}")

        if connection:
            connection.rollback()

    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    remove_attendance_old()
