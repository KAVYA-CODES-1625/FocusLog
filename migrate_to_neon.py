
import os
import sqlite3
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "focuslog.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

TABLES = {
    "users": [
        "id", "username", "email", "password_hash",
        "google_sub", "created_at"
    ],
    "sessions": [
        "id", "user_id", "activity", "minutes", "points", "date"
    ],
    "tasks": [
        "id", "user_id", "title", "description", "subject",
        "category", "due_date", "priority", "status",
        "created_at", "completed_at"
    ],
    "user_imports": ["id", "user_id", "imported_at"],
}


def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from .env")

    if not SQLITE_PATH.exists():
        raise FileNotFoundError("focuslog.db was not found.")

    source = sqlite3.connect(SQLITE_PATH)
    source.row_factory = sqlite3.Row

    try:
        source.execute("PRAGMA foreign_keys = ON")

        source_counts = {}

        for table, columns in TABLES.items():
            actual = [
                row["name"]
                for row in source.execute(
                    f"PRAGMA table_info({table})"
                )
            ]

            if actual != columns:
                raise RuntimeError(
                    f"SQLite schema mismatch in {table}. "
                    "Migration stopped."
                )

            source_counts[table] = source.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

        print("SQLite source verified.")
        print("Records to transfer:", source_counts)

        with psycopg.connect(
            DATABASE_URL,
            sslmode="require"
        ) as destination:

            with destination.cursor() as cursor:

                # Verify destination tables and columns.
                for table, columns in TABLES.items():
                    cursor.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s
                        ORDER BY ordinal_position
                        """,
                        (table,)
                    )

                    actual = [row[0] for row in cursor.fetchall()]

                    if actual != columns:
                        raise RuntimeError(
                            f"Neon schema mismatch in {table}. "
                            "Migration stopped."
                        )

                # Never overwrite existing Neon records.
                for table in TABLES:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    )
                    if cursor.fetchone()[0] != 0:
                        raise RuntimeError(
                            f"Neon table {table} is not empty. "
                            "Migration stopped."
                        )

                # Copy parent records before child records.
                for table, columns in TABLES.items():
                    rows = source.execute(
                        f"SELECT * FROM {table} ORDER BY id"
                    ).fetchall()

                    if not rows:
                        continue

                    column_sql = ", ".join(columns)
                    placeholders = ", ".join(
                        ["%s"] * len(columns)
                    )

                    insert_sql = (
                        f"INSERT INTO {table} ({column_sql}) "
                        f"VALUES ({placeholders})"
                    )

                    cursor.executemany(
                        insert_sql,
                        [
                            tuple(row[column] for column in columns)
                            for row in rows
                        ]
                    )

                # Synchronize generated ID sequences.
                for table in TABLES:
                    cursor.execute(
                        """
                        SELECT setval(
                            pg_get_serial_sequence(%s, 'id'),
                            COALESCE((SELECT MAX(id) FROM """
                        + table
                        + """), 1),
                            EXISTS(SELECT 1 FROM """
                        + table
                        + """)
                        )
                        """,
                        (f"public.{table}",)
                    )

                # Verify copied record counts before commit.
                for table, expected in source_counts.items():
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    )
                    actual = cursor.fetchone()[0]

                    if actual != expected:
                        raise RuntimeError(
                            f"Count mismatch in {table}. "
                            "Transaction will roll back."
                        )

        print("\nMIGRATION SUCCESSFUL!")
        print("All four tables were transferred and verified.")

    finally:
        source.close()


if __name__ == "__main__":
    main()
