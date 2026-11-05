# FocusLog — Growly Progress Upgrade

This version includes account-specific email/password authentication, optional Google sign-in, SQLite session storage, XP levels, achievement badges, streak tracking, and a one-time opt-in legacy progress import.

## Run locally

1. Use Python 3.10 or newer.
2. Open a terminal in this folder.
3. Install dependencies:
   ```powershell
   py -m pip install -r requirements.txt
   ```
4. Set a development secret (PowerShell):
   ```powershell
   $env:FLASK_SECRET_KEY = "replace-with-a-long-random-secret"
   ```
5. Start the app:
   ```powershell
   py app.py
   ```
6. Open http://127.0.0.1:5000 and create an account.

The app creates `focuslog.db` beside `app.py`. Keep a backup of this database; it contains account and focus history data.

## XP and achievements

Each valid focus minute earns 1 XP. Levels increase every 100 XP. Achievements unlock automatically when their conditions are met. Growth stages remain based on the existing Growly thresholds (Seed 0, Sprout 30, Young Plant 60, Tree 120).

## Import old Growly progress

After logging into the account that owns the old progress, choose **Import my old Growly progress** on the dashboard. It imports sessions from `focuslog_data_legacy_backup.json` once per account and records that the import was attempted, preventing duplicate imports. The bundled backup currently contains 3 sessions totaling 64 points and 64 minutes. Do not import it into another person's account. Keep the backup file alongside `app.py`.

## Enable Google sign-in

Google sign-in code is included, but it cannot authenticate until you create your own OAuth credentials:

1. Open the Google Cloud Console: https://console.cloud.google.com/
2. Create/select a project and configure the OAuth consent screen.
3. Create an OAuth Client ID for a Web application.
4. Add this redirect URI for local development: `http://127.0.0.1:5000/login/google/callback`
5. In the same PowerShell terminal where you start Flask, set:
   ```powershell
   $env:GOOGLE_CLIENT_ID = "your-client-id"
   $env:GOOGLE_CLIENT_SECRET = "your-client-secret"
   py app.py
   ```
6. Restart Flask after changing environment variables, then test **Continue with Google**.

For deployment, add the exact HTTPS callback URL for your domain in Google Cloud Console. Never commit or share the client secret. Google sign-in has not been live-tested without your credentials.

## Security and deployment

This is a local development project, not yet production-ready. Before public deployment, configure a strong secret, HTTPS and secure cookies; add CSRF protection, login rate limiting, account recovery, and production WSGI hosting. Never share `.env` files, database files, or OAuth secrets publicly.


## Optional AI Focus Coach (Nebius Token Factory)

The dashboard includes an on-demand reflection coach. It sends the reflection text only after the signed-in user presses the button. Do not enter passwords or sensitive personal details. The server keeps the API key in an environment variable and does not send it to the browser.

1. Create an API key in your Nebius Token Factory account and check the current endpoint and model names in its dashboard/docs.
2. In the same PowerShell terminal used to run Flask, set your key (do not paste it into source code or commit it):
   ```powershell
   $env:NEBIUS_API_KEY = "your-real-key"
   $env:NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1/"
   $env:NEBIUS_MODEL = "meta-llama/Meta-Llama-3.1-70B-Instruct"
   py app.py
   ```
3. Sign in, open the Focus Coach card, enter a short reflection, and press **Get my reflection**.

If the model name shown in your Nebius account differs, set `NEBIUS_MODEL` to an available chat-completion model. If your account docs specify a different API base URL, set `NEBIUS_BASE_URL` accordingly. The key is never exposed to the browser. If the key is missing, the coach displays a setup message and the rest of FocusLog remains usable.
