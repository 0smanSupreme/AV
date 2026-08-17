# Admin Vote — Python/Vercel

A Flask recreation of the Admin Vote Roblox site, including:

- Matching dark gold login, registration, and password-reset screens
- Protected Admin Vote game hub and responsive mobile navigation
- Role-based accounts: Administrator, Developer, and Member
- Player interview logs, tagged questions, answers, and analytics
- Admin controls for account roles, public team members, and social links
- Light/dark theme switching
- Local SQLite support and production Postgres support

## Run locally

Requires Python 3.12 or later.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`. The first account created becomes the
Administrator. Later accounts are Members; the Administrator can promote them.

## Deploy through GitHub to Vercel

1. Extract this ZIP.
2. Upload all files inside the extracted folder to the root of a new GitHub repository.
3. In Vercel, choose **Add New → Project**, import that repository, and deploy it.
4. Do not select a frontend framework and do not set a build command. Vercel detects
   the Flask `app` in `app.py` through `pyproject.toml`.
5. Add these environment variables in **Project Settings → Environment Variables**:

   - `SECRET_KEY`: a long random value used to sign login sessions.
   - `DATABASE_URL`: a Postgres connection string for durable accounts and content.

The site starts without `DATABASE_URL`, but Vercel's local function storage is
temporary. For real accounts, use a managed Postgres database such as Neon,
Supabase, or Vercel Postgres and set its connection string as `DATABASE_URL`.

## Security and setup notes

- Passwords are stored as secure hashes, never as readable text.
- All modifying forms use CSRF protection.
- The first registered account is the initial Administrator, so create your own
  account immediately after the first production deployment.
- The Google button is visually included to match the original. It becomes functional
  only after adding your own Google OAuth application and callback flow.
- Password reset deliberately returns a generic response; connect an email provider
  before using it for production recovery.

## Project structure

```text
app.py              Flask application and database models
templates/          Jinja page templates
public/             CSS, JavaScript, and image assets served by Vercel's CDN
pyproject.toml      Python and Vercel entrypoint configuration
requirements.txt    Python dependencies
vercel.json         Vercel Function settings
tests/              Basic application tests
```
