import os
import json
import psycopg
from psycopg.rows import dict_row
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from flask import render_template_string

from authlib.integrations.flask_client import OAuth
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager, UserMixin, current_user, login_required,
    login_user, logout_user
)
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-this-before-deploying")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

GROWTH_STAGES = [
    (0, "Seed", "🌰"),
    (30, "Sprout", "🌱"),
    (60, "Young Plant", "🪴"),
    (120, "Tree", "🌳"),
]

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to open your FocusLog garden."
login_manager.login_message_category = "info"
login_manager.init_app(app)

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured. Add your Neon connection string to .env.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, sslmode="require")


def init_db():
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS sessions (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            activity TEXT NOT NULL,
            minutes INTEGER NOT NULL CHECK(minutes > 0),
            points INTEGER NOT NULL CHECK(points >= 0),
            date TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS user_imports (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL UNIQUE,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS tasks (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            subject TEXT,
            category TEXT,
            due_date TEXT,
            priority TEXT NOT NULL DEFAULT 'Medium',
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
    ]
    with get_db() as db:
        for statement in statements:
            db.execute(statement)


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.email = row["email"]
        self.password_hash = row["password_hash"]
        self.google_sub = row["google_sub"]


@login_manager.user_loader
def load_user(user_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return User(row) if row else None


def get_growth_stage(points):
    stage = GROWTH_STAGES[0]
    for candidate in GROWTH_STAGES:
        if points >= candidate[0]:
            stage = candidate
    return stage


def get_progress(points):
    current_stage = get_growth_stage(points)
    next_stage = next((stage for stage in GROWTH_STAGES if stage[0] > points), None)
    if next_stage is None:
        return None, 100
    percent = ((points - current_stage[0]) / (next_stage[0] - current_stage[0])) * 100
    return next_stage, round(percent)


# XP is calculated from focus minutes: one focused minute earns one XP.
LEVEL_STEP = 100
ACHIEVEMENTS = [
    {"id": "first_session", "name": "First Seed", "emoji": "🌱", "description": "Log your first focus session.", "kind": "sessions", "target": 1},
    {"id": "hour_focused", "name": "Hour of Power", "emoji": "⏳", "description": "Focus for 60 minutes in total.", "kind": "minutes", "target": 60},
    {"id": "century_xp", "name": "Century Club", "emoji": "💯", "description": "Earn 100 XP.", "kind": "xp", "target": 100},
    {"id": "five_sessions", "name": "Showing Up", "emoji": "📚", "description": "Complete 5 focus sessions.", "kind": "sessions", "target": 5},
    {"id": "three_day_streak", "name": "Growing Habit", "emoji": "🔥", "description": "Build a 3-day focus streak.", "kind": "streak", "target": 3},
    {"id": "tree_stage", "name": "Rooted & Rising", "emoji": "🌳", "description": "Reach the Tree growth stage (120 XP).", "kind": "xp", "target": 120},
]

def get_level(xp):
    level = xp // LEVEL_STEP + 1
    xp_in_level = xp % LEVEL_STEP
    return level, xp_in_level, LEVEL_STEP - xp_in_level

def get_achievements(xp, total_minutes, session_count, longest_streak):
    values = {"xp": xp, "minutes": total_minutes, "sessions": session_count, "streak": longest_streak}
    result = []
    for badge in ACHIEVEMENTS:
        item = dict(badge)
        item["unlocked"] = values[item["kind"]] >= item["target"]
        result.append(item)
    return result


@app.route("/")
def root():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not 2 <= len(username) <= 40:
            flash("Your name must be between 2 and 40 characters.", "error")
        elif len(email) > 254 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            flash("Please enter a valid email address.", "error")
        elif len(password) < 8:
            flash("Use a password with at least 8 characters.", "error")
        elif password != confirm:
            flash("Those passwords do not match yet.", "error")
        else:
            try:
                with get_db() as db:
                    cursor = db.execute(
                        "INSERT INTO users (username, email, password_hash, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                        (username, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds")),
                    )
                    user_id = cursor.fetchone()["id"]
                with get_db() as db:
                    row = db.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
                login_user(User(row))
                flash("Your FocusLog garden is ready. Welcome! 🌱", "success")
                return redirect(url_for("dashboard"))
            except psycopg.IntegrityError:
                flash("An account with that email already exists. Try logging in instead.", "error")
    return render_template("auth.html", mode="signup", google_ready=bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as db:
            row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if row and row["password_hash"] and check_password_hash(row["password_hash"], password):
            login_user(User(row), remember=request.form.get("remember") == "on")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        flash("That email and password combination wasn't recognized.", "error")
    return render_template("auth.html", mode="login", google_ready=bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")))


@app.route("/login/google")
def google_login():
    if not (os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")):
        flash("Google sign-in needs Google OAuth credentials first. You can still use email and password.", "info")
        return redirect(url_for("login"))
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    if not (os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")):
        flash("Google sign-in is not configured yet.", "info")
        return redirect(url_for("login"))
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")
        if not user_info:
            user_info = google.parse_id_token(token, nonce=None)
        email = (user_info.get("email") or "").strip().lower()
        google_sub = user_info.get("sub")
        username = (user_info.get("name") or email.split("@")[0] or "FocusLog user")[:40]
        if not email or not user_info.get("email_verified", False):
            flash("Google couldn't verify this email address. Please try another sign-in method.", "error")
            return redirect(url_for("login"))
        with get_db() as db:
            row = db.execute(
                "SELECT * FROM users WHERE google_sub = %s OR email = %s",
                (google_sub, email),
            ).fetchone()
            if row:
                if not row["google_sub"]:
                    db.execute("UPDATE users SET google_sub = %s WHERE id = %s", (google_sub, row["id"]))
                    row = db.execute("SELECT * FROM users WHERE id = %s", (row["id"],)).fetchone()
            else:
                cursor = db.execute(
                    "INSERT INTO users (username, email, google_sub, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                    (username, email, google_sub, datetime.now().isoformat(timespec="seconds")),
                )
                new_user_id = cursor.fetchone()["id"]
                row = db.execute("SELECT * FROM users WHERE id = %s", (new_user_id,)).fetchone()
        login_user(User(row))
        flash("You're signed in. Let's grow! 🌱", "success")
        return redirect(url_for("dashboard"))
    except Exception:
        app.logger.exception("Google sign-in failed")
        flash("Google sign-in didn't finish. Please try again or use email and password.", "error")
        return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You've been logged out safely. See you soon! 🌿", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as db:
        rows = db.execute(
            "SELECT activity, minutes, points, date FROM sessions WHERE user_id = %s ORDER BY id DESC",
            (int(current_user.id),),
        ).fetchall()
    sessions = [dict(row) for row in rows]
    
    with get_db() as db:
        task_rows = db.execute(
            """
            SELECT * FROM tasks
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (int(current_user.id),)
        ).fetchall()

    tasks = [dict(row) for row in task_rows]

    points = sum(session["points"] for session in sessions)
    total_minutes = sum(session["minutes"] for session in sessions)
    today_date = datetime.now().strftime("%Y-%m-%d")
    # Monthly overview calculations
    today = datetime.now().date()
    month_start = today.replace(day=1)

    monthly_sessions = []

    for session in sessions:
        try:
            session_date = datetime.strptime(
                 session["date"][:10], "%Y-%m-%d"
            ).date()

            if month_start <= session_date <= today:
                monthly_sessions.append(session)

        except (ValueError, TypeError):
            continue

    monthly_focus_minutes = sum(
        session["minutes"] for session in monthly_sessions
        )

    monthly_session_count = len(monthly_sessions)

    days_elapsed = today.day

    monthly_daily_average = round(
    monthly_focus_minutes / days_elapsed
    ) if days_elapsed else 0
    # Focus Insights calculations

    # Most productive day this month
    daily_focus = {}

    for session in monthly_sessions:
        session_date = datetime.strptime(
        session["date"][:10], "%Y-%m-%d"
        ).date()

        day_name = session_date.strftime("%A")

        daily_focus[day_name] = (
            daily_focus.get(day_name, 0) + session["minutes"]
            )

    most_productive_day = (
        max(daily_focus, key=daily_focus.get)
        if daily_focus else "No sessions yet"
        )

    # Average session duration this month
    average_session_duration = (
        round(monthly_focus_minutes / monthly_session_count)
        if monthly_session_count else 0
        )

    today_focus_minutes = sum(
        session["minutes"]
        for session in sessions
       if session["date"].startswith(today_date)
       )

    productivity_score = min(100, round((today_focus_minutes / 120) * 100))
    _, plant_name, plant_emoji = get_growth_stage(points)
    next_stage, progress_percent = get_progress(points)
    recent_sessions = sessions[:5]
        # Calculate focus minutes for the current week (Monday–Sunday)
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())

    weekly_focus = [
        {
            "day": (week_start + timedelta(days=i)).strftime("%a"),
            "minutes": 0
        }
        for i in range(7)
    ]

    for session in sessions:
        try:
            session_day = datetime.strptime(
                session["date"][:10], "%Y-%m-%d"
            ).date()

            day_index = (session_day - week_start).days

            if 0 <= day_index < 7:
                weekly_focus[day_index]["minutes"] += session["minutes"]

        except (ValueError, TypeError):
            continue

    session_dates = set()
    for session in sessions:
        try:
            session_dates.add(datetime.strptime(session["date"][:10], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            continue

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    current_streak = 0
    streak_day = today if today in session_dates else yesterday if yesterday in session_dates else None
    while streak_day is not None and streak_day in session_dates:
        current_streak += 1
        streak_day -= timedelta(days=1)

    longest_streak = 0
    running_streak = 0
    previous_day = None
    for session_day in sorted(session_dates):
        running_streak = running_streak + 1 if previous_day and session_day == previous_day + timedelta(days=1) else 1
        longest_streak = max(longest_streak, running_streak)
        previous_day = session_day

    
    
    return render_template(
        "index.html",
        points=points,
        plant_name=plant_name,
        plant_emoji=plant_emoji,
        next_stage_name=next_stage[1] if next_stage else None,
        next_stage_emoji=next_stage[2] if next_stage else None,
        progress_percent=progress_percent,
        total_minutes=total_minutes,
        today_focus_minutes=today_focus_minutes,
        productivity_score=productivity_score,
        sessions=recent_sessions,
        tasks=tasks,
        weekly_focus=weekly_focus,
        monthly_focus_minutes=monthly_focus_minutes,
        monthly_session_count=monthly_session_count,
        monthly_daily_average=monthly_daily_average,
        most_productive_day=most_productive_day,
        average_session_duration=average_session_duration,
        current_streak=current_streak,
        longest_streak=longest_streak,
        level=get_level(points)[0],
        level_xp=get_level(points)[1],
        xp_to_next_level=get_level(points)[2],
        achievements=get_achievements(points, total_minutes, len(sessions), longest_streak),
        unlocked_achievements=sum(1 for item in get_achievements(points, total_minutes, len(sessions), longest_streak) if item["unlocked"]),
        legacy_imported=legacy_imported(int(current_user.id)),
    )


def legacy_imported(user_id):
    with get_db() as db:
        return db.execute("SELECT 1 FROM user_imports WHERE user_id = %s", (user_id,)).fetchone() is not None


@app.route("/import-legacy", methods=["POST"])
@login_required
def import_legacy():
    user_id = int(current_user.id)
    backup_path = BASE_DIR / "focuslog_data_legacy_backup.json"
    if legacy_imported(user_id):
        flash("Your legacy Growly progress has already been imported.", "info")
        return redirect(url_for("dashboard"))
    try:
        with backup_path.open("r", encoding="utf-8") as file:
            legacy = json.load(file)
        legacy_sessions = legacy.get("sessions", [])
        clean_sessions = []
        for item in legacy_sessions:
            activity = str(item.get("activity", "Imported focus session")).strip()[:100]
            minutes = int(item.get("minutes", 0))
            points = int(item.get("points", minutes))
            date = str(item.get("date", datetime.now().strftime("%Y-%m-%d %H:%M")))[:30]
            if activity and 1 <= minutes <= 1440 and points >= 0:
                clean_sessions.append((user_id, activity, minutes, points, date))
        with get_db() as db:
            db.execute("INSERT INTO user_imports (user_id, imported_at) VALUES (%s, %s)", (user_id, datetime.now().isoformat(timespec="seconds")))
            db.executemany("INSERT INTO sessions (user_id, activity, minutes, points, date) VALUES (%s, %s, %s, %s, %s)", clean_sessions)
        flash(f"Imported {len(clean_sessions)} Growly sessions into your account! 🌱", "success")
    except FileNotFoundError:
        flash("The legacy backup file was not found. Your existing data was not changed.", "error")
    except (ValueError, TypeError, json.JSONDecodeError, psycopg.IntegrityError):
        flash("The backup could not be imported safely. No duplicate import was made.", "error")
    return redirect(url_for("dashboard"))


@app.route("/log", methods=["POST"])
@login_required
def log_session():
    activity = request.form.get("activity", "").strip()
    raw_minutes = request.form.get("minutes", "").strip()
    if not activity or len(activity) > 100:
        return "Please enter an activity of 1–100 characters.", 400
    try:
        minutes = int(raw_minutes)
    except (ValueError, TypeError):
        return "Duration must be a whole number.", 400
    if not 1 <= minutes <= 1440:
        return "Duration must be between 1 and 1440 minutes.", 400

    with get_db() as db:
        db.execute(
            "INSERT INTO sessions (user_id, activity, minutes, points, date) VALUES (%s, %s, %s, %s, %s)",
            (int(current_user.id), activity, minutes, minutes, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    return redirect(url_for("dashboard"))



@app.route("/tasks/add", methods=["POST"])
@login_required
def add_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    subject = request.form.get("subject", "").strip()
    category = request.form.get("category", "").strip()
    due_date = request.form.get("due_date", "").strip()
    priority = request.form.get("priority", "Medium").strip()

    if not title or len(title) > 150:
        flash("Please enter a task title (maximum 150 characters).", "error")
        return redirect(url_for("dashboard"))

    if priority not in ["Low", "Medium", "High"]:
        priority = "Medium"

    with get_db() as db:
        db.execute(
            """
            INSERT INTO tasks
            (user_id, title, description, subject, category,
             due_date, priority, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Pending', %s)
            """,
            (
                int(current_user.id),
                title,
                description,
                subject,
                category,
                due_date or None,
                priority,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            )
        )

    flash("Task added successfully! 🌱", "success")
    return redirect(url_for("dashboard"))
@app.route("/tasks/complete/<int:task_id>", methods=["POST"])
@login_required
def complete_task(task_id):
    with get_db() as db:
        task = db.execute(
            """
            SELECT id FROM tasks
            WHERE id = %s AND user_id = %s
            """,
            (task_id, int(current_user.id))
        ).fetchone()

        if task is None:
            flash("Task not found.", "error")
            return redirect(url_for("dashboard"))

        db.execute(
            """
            UPDATE tasks
            SET status = 'Completed',
                completed_at = %s
            WHERE id = %s AND user_id = %s
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                task_id,
                int(current_user.id)
            )
        )

    flash("Task completed! Great work! 🌱", "success")
    return redirect(url_for("dashboard"))

@app.route("/api/focus-coach", methods=["POST"])
@login_required
def focus_coach():
    """Generate a private study reflection using Google's Gemini API."""

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    app.logger.info(
    "Gemini key detected by Flask: %s",
    bool(api_key)
)

    if not api_key:
        return {
            "error": "Gemini AI is not configured. Add GEMINI_API_KEY in your terminal and restart FocusLog."
        }, 503

    payload = request.get_json(silent=True) or {}
    reflection = str(payload.get("reflection", "")).strip()

    if not reflection:
        return {"error": "Write a short reflection first."}, 400

    if len(reflection) > 1800:
        return {
            "error": "Please keep your reflection under 1,800 characters."
        }, 400

    model = os.environ.get(
        "GEMINI_MODEL",
        "gemini-3.8-flash"
    ).strip()

    prompt = (
        "You are FocusLog's supportive study reflection coach. "
        "Be practical, kind, concise, and non-judgmental. "
        "Use only the student's reflection; do not diagnose medical "
        "or mental-health conditions. Do not shame them or encourage "
        "extreme study schedules. Respond with three short headings: "
        "What I notice, Possible obstacle, One small next step. "
        "If evidence is limited, say so."
    )

    body = {
        "systemInstruction": {
            "parts": [{"text": prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": reflection}]
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 350
        }
    }

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))

        candidates = result.get("candidates", [])

        if not candidates:
            return {
                "error": "Gemini could not generate a response. Please try a different reflection."
            }, 502

        parts = candidates[0].get("content", {}).get("parts", [])
        answer = "\n".join(
            part.get("text", "")
            for part in parts
            if part.get("text")
        ).strip()

        if not answer:
            return {
                "error": "Gemini returned an empty response. Please try again."
            }, 502

        return {"answer": answer}

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")

        app.logger.error(
            "Gemini HTTP error %s: %s",
            exc.code,
            error_body[:2000]
        )

        if exc.code in (401, 403):
            return {
                "error": "Gemini rejected the API key or project access."
            }, 502

        if exc.code == 429:
            return {
                "error": "Gemini usage limit reached. Please try later."
            }, 502

        return {
            "error": f"Gemini API error (HTTP {exc.code}). Check terminal logs."
        }, 502

    except (urllib.error.URLError, TimeoutError) as exc:
        app.logger.error("Gemini connection error: %s", exc)
        return {
        "error": "FocusLog could not reach Gemini. Check your internet connection."
        }, 502

    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        app.logger.exception("Unexpected response from Gemini API")
        return {
            "error": "The AI response could not be read. Please try again."
        }, 502

@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="We couldn't find that garden path."), 404


init_db()

if __name__ == "__main__":
    # Local development only. For deployment, use a production WSGI server and HTTPS.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")