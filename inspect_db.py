
import sqlite3

conn = sqlite3.connect("focuslog.db")

print("TABLES:")
print([
    row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
])

print("IMPORT RECORDS:",
      conn.execute("SELECT COUNT(*) FROM user_imports").fetchone()[0])

for table in ["users", "sessions", "tasks"]:
    print(f"\n{table.upper()} COLUMNS:")
    print([
        row[1] for row in
        conn.execute(f"PRAGMA table_info({table})")
    ])

for table in ["users", "sessions", "tasks", "user_imports"]:
    count = conn.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]
    print(f"{table}: {count} records")
conn.close()
