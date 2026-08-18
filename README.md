# Admin Vote — Python/Vercel

A Flask recreation of the Admin Vote Roblox website with:

- A completely public home page — no account is needed to view the site
- Public roadmap and release updates managed from the admin panel
- Simple username-and-password registration with no email or external login
- Display names and optional uploaded profile pictures
- Profile editing and password changes
- Private developer feedback logs, tagged responses, and analytics
- Administrator controls for roles, team members, social links, and roadmap items
- Automatic YouTube, Discord, and Roblox icons for matching social platforms
- PostgreSQL for Vercel and SQLite for local development

## Deploy through GitHub to Vercel

1. Extract this ZIP.
2. Upload **the files inside the extracted folder** to the root of your GitHub repository.
3. Import the repository into Vercel.
4. Do not choose a frontend framework or add a build command. Vercel detects
   the Python entrypoint from `pyproject.toml`.
5. In **Vercel → Project → Settings → Environment Variables**, add:

```text
SECRET_KEY=your-long-random-secret
DATABASE_URL=your-postgresql-connection-string
ADMIN_USERNAME=your_admin_username
ADMIN_PASSWORD=your_secure_admin_password
ADMIN_DISPLAY_NAME=Administrator
```

`ADMIN_DISPLAY_NAME` is optional. `ADMIN_USERNAME` must contain 3–24 letters,
numbers, or underscores. `ADMIN_PASSWORD` must be at least 8 characters.

Choose Production and Preview for every variable, save, and redeploy. The admin
account is created automatically. If you change `ADMIN_PASSWORD` later and
redeploy, the environment-controlled admin password is updated automatically.

You no longer need `RESEND_API_KEY`, `EMAIL_FROM`, `ADMIN_EMAIL`, or any Roblox/
Google login variables. They may be deleted from Vercel.

## Accounts and permissions

- Anyone can view Home, Roadmap, Updates, Team, and Socials without logging in.
- Anyone can register with a username, display name, password, and optional image.
- New registrations are ordinary Members.
- The environment account is always an Administrator.
- An Administrator can promote an account to Developer in the Access tab.
- Only Developers and Administrators can view or manage feedback logs.
- Only Administrators can manage accounts, team members, socials, and roadmap items.

Profile images are checked as PNG, JPG, GIF, or WebP, limited to 700 KB, and
stored in PostgreSQL so they persist across Vercel deployments.

## Run locally

Requires Python 3.12 or later.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Project structure

```text
app.py              Flask app, authentication, database models, and routes
templates/          Public, account, feedback, profile, and admin pages
public/             CSS, JavaScript, logo, artwork, and social icons
pyproject.toml      Python project and Vercel entrypoint
requirements.txt    Python dependencies
vercel.json         Vercel Function settings
tests/              Automated account and permissions tests
```
