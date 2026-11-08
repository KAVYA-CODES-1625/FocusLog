import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    print("DATABASE_URL is missing!")
else:
    try:
        connection = psycopg.connect(
            database_url,
            connect_timeout=15
        )

        print("NEON CONNECTION SUCCESSFUL!")

        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            for table in ["users", "sessions", "tasks", "user_imports"]:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"{table}: {cursor.fetchone()[0]} records")
            print("DATABASE QUERY SUCCESSFUL!")

        connection.close()

    except Exception as error:
        print("Connection failed!")
        print("Error type:", type(error).__name__)
sslmode="require"