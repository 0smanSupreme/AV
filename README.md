# Admin Vote — Python/Vercel

A Flask recreation of the Admin Vote Roblox site, including:

- Public Admin Vote game hub with responsive mobile navigation
- Passwordless email login with a six-digit, single-use verification code
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

Open `http://127.0.0.1:5000`. The home page is public. Feedback and Administration
require login. The first email successfully verified becomes the Administrator;
later verified emails become Members and can be promoted by an Administrator.

For local email testing, copy `.env.example` to `.env`, export its values in your
terminal, and set `EMAIL_DELIVERY_MODE=console`. The code is printed in the server
log. Do not use console delivery in production.

## Deploy through GitHub to Vercel

1. Extract this ZIP.
2. Upload all files inside the extracted folder to the root of a new GitHub repository.
3. In Vercel, choose **Add New → Project**, import that repository, and deploy it.
4. Do not select a frontend framework and do not set a build command. Vercel detects
   the Flask `app` in `app.py` through `pyproject.toml`.
5. Add these environment variables in **Project Settings → Environment Variables**:

   - `SECRET_KEY`: a long random value used to sign login sessions.
   - `DATABASE_URL`: a Postgres connection string for durable accounts and content.
   - `RESEND_API_KEY`: your Resend API key for sending verification codes.
   - `EMAIL_FROM`: a verified sender, such as `Admin Vote <login@yourdomain.com>`.

The site starts without `DATABASE_URL`, but Vercel's local function storage is
temporary. For real accounts, use a managed Postgres database such as Neon,
Supabase, or Vercel Postgres and set its connection string as `DATABASE_URL`.

## Security and setup notes

- Login codes are stored as secure hashes, expire after 10 minutes, allow five
  attempts, and can only be used once.
- All modifying forms use CSRF protection.
- The first verified email is the initial Administrator, so verify your own email
  immediately after the first production deployment.
- Google login and passwords are not used.
- Verification emails can be sent through Resend or an SMTP provider.
- The supplied YouTube, Discord, and Roblox images are selected automatically when
  an Administrator adds a social link with the matching platform.

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
