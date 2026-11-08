
import os
import json
import hashlib
import sqlite3
import psycopg
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "focuslog.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

TABLES = ["users", "sessions", "tasks", "user_imports"]

def fingerprint(rows):
    data = [
        [None if value is None else str(value) for value in row]
        for row in rows
    ]
    encoded = json.dumps(data, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing.")

sqlite_db = sqlite3.connect(SQLITE_PATH)

try:
    with psycopg.connect(DATABASE_URL, sslmode="require") as neon_db:
        print("DATABASE COMPARISON")
        print("-------------------")

        for table in TABLES:
            local_rows = sqlite_db.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()

            with neon_db.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {table} ORDER BY id"
                )
                neon_rows = cursor.fetchall()

            local_hash = fingerprint(local_rows)
            neon_hash = fingerprint(neon_rows)

            result = "MATCH" if local_hash == neon_hash else "MISMATCH"

            print(
                f"{table}: SQLite={len(local_rows)}, "
                f"Neon={len(neon_rows)} — {result}"
            )

finally:
    sqlite_db.close()
