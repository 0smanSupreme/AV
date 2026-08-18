"""Admin Vote — Flask/Vercel edition.

This app mirrors the Base44 project while keeping the backend entirely Python.
Set DATABASE_URL to a Postgres URL in production; local development uses SQLite.
"""

from __future__ import annotations

import json
import os
import secrets
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured.startswith("postgres://"):
        configured = "postgresql+psycopg://" + configured.removeprefix("postgres://")
    elif configured.startswith("postgresql://") and "+psycopg" not in configured:
        configured = "postgresql+psycopg://" + configured.removeprefix("postgresql://")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "sqlite:////tmp/admin-vote.sqlite3"
    return "sqlite:///admin-vote.sqlite3"


app = Flask(__name__, static_folder="public", static_url_path="")
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
db = SQLAlchemy(app)


def _utcnow() -> datetime:
    """Return naive UTC for consistent SQLite and Postgres comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class VerificationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    consumed_at = db.Column(db.DateTime, nullable=True)


class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    team_role = db.Column(db.String(30), nullable=False, default="Contributor")
    title = db.Column(db.String(120), nullable=False, default="")
    avatar_url = db.Column(db.String(500), nullable=False, default="")
    profile_url = db.Column(db.String(500), nullable=False, default="")
    bio = db.Column(db.Text, nullable=False, default="")


class SocialLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(30), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    icon = db.Column(db.String(30), nullable=False, default="website")
    order = db.Column(db.Integer, nullable=False, default=0)


class FeedbackLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    interviewee_name = db.Column(db.String(100), nullable=False, default="")
    interview_date = db.Column(db.Date, nullable=True)
    location = db.Column(db.String(120), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    creator = db.relationship("User")
    responses = db.relationship(
        "FeedbackResponse", backref="log", cascade="all, delete-orphan", lazy=True
    )


class FeedbackTag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(240), nullable=False, default="")
    options_json = db.Column(db.Text, nullable=False, default="[]")

    @property
    def options(self) -> list[str]:
        try:
            return json.loads(self.options_json)
        except (TypeError, json.JSONDecodeError):
            return []


class FeedbackResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey("feedback_log.id"), nullable=False)
    tag_id = db.Column(db.Integer, db.ForeignKey("feedback_tag.id"), nullable=True)
    tag_name = db.Column(db.String(100), nullable=False, default="")
    question_text = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    tag = db.relationship("FeedbackTag")


def _valid_external_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _social_icon_path(platform: str) -> str | None:
    return {
        "youtube": "/social-youtube.png",
        "discord": "/social-discord.png",
        "roblox": "/social-roblox.jpg",
    }.get((platform or "").lower())


def _send_verification_email(email: str, code: str) -> None:
    """Deliver a one-time code through Resend or an SMTP provider."""
    app.extensions["last_verification_code"] = {"email": email, "code": code}
    if app.testing:
        return

    sender = os.getenv("EMAIL_FROM", "Admin Vote <login@adminvote.com>")
    subject = f"{code} is your Admin Vote verification code"
    plain = (
        f"Your Admin Vote verification code is {code}.\n\n"
        "It expires in 10 minutes. If you did not request this code, you can ignore this email."
    )
    html = f"""
    <div style="background:#0d0c0a;padding:32px;font-family:Arial,sans-serif;color:#f7f3ec">
      <div style="max-width:520px;margin:auto;background:#1a1613;border:1px solid #3b332d;border-radius:16px;padding:32px">
        <p style="color:#e7bd3d;font-size:12px;letter-spacing:2px;margin:0 0 8px">ADMIN VOTE</p>
        <h1 style="font-size:24px;margin:0 0 14px">Your verification code</h1>
        <div style="font-size:36px;font-weight:700;letter-spacing:10px;color:#e7bd3d;margin:22px 0">{code}</div>
        <p style="color:#b8afa5;line-height:1.6">Enter this code to sign in. It expires in 10 minutes.</p>
        <p style="color:#756d65;font-size:12px;margin-top:28px">If you did not request this code, you can ignore this email.</p>
      </div>
    </div>"""

    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    if resend_key:
        payload = json.dumps(
            {"from": sender, "to": [email], "subject": subject, "text": plain, "html": html}
        ).encode("utf-8")
        req = urllib_request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=15) as response:
                if response.status >= 300:
                    raise RuntimeError("The email provider rejected the verification email.")
            return
        except (urllib_error.URLError, TimeoutError) as exc:
            raise RuntimeError("The verification email could not be sent.") from exc

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if smtp_host:
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = email
        message["Subject"] = subject
        message.set_content(plain)
        message.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        try:
            if os.getenv("SMTP_USE_SSL", "0") == "1":
                with smtplib.SMTP_SSL(smtp_host, port, timeout=15, context=context) as server:
                    if username:
                        server.login(username, password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(smtp_host, port, timeout=15) as server:
                    server.starttls(context=context)
                    if username:
                        server.login(username, password)
                    server.send_message(message)
            return
        except (OSError, smtplib.SMTPException) as exc:
            raise RuntimeError("The verification email could not be sent.") from exc

    if os.getenv("EMAIL_DELIVERY_MODE") == "console" and not os.getenv("VERCEL"):
        app.logger.warning("Admin Vote verification code for %s: %s", email, code)
        return
    raise RuntimeError("Email delivery is not configured yet.")


@app.context_processor
def template_globals():
    return {
        "csrf_token": _csrf_token,
        "role_label": {"admin": "Administrator", "developer": "Developer", "user": "Member"},
        "social_icon_path": _social_icon_path,
        "current_year": datetime.now().year,
    }


@app.before_request
def load_user_and_check_csrf():
    g.user = db.session.get(User, session.get("user_id")) if session.get("user_id") else None
    if request.method == "POST":
        supplied = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            abort(400, "Invalid form token. Please reload the page and try again.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", returnTo=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _safe_return_to(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return url_for("home")
    return value


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
            flash("Enter a valid email address", "error")
            return render_template("auth.html", mode="login", return_to=request.form.get("return_to", "/"))

        latest = VerificationCode.query.filter_by(email=email).order_by(VerificationCode.id.desc()).first()
        now = _utcnow()
        if latest and latest.requested_at > now - timedelta(seconds=60):
            flash("Please wait one minute before requesting another code.", "error")
            return render_template("auth.html", mode="login", return_to=request.form.get("return_to", "/"))

        code = f"{secrets.randbelow(1_000_000):06d}"
        record = VerificationCode(
            email=email,
            code_hash=generate_password_hash(code),
            requested_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.session.add(record)
        try:
            _send_verification_email(email, code)
            db.session.commit()
        except RuntimeError as exc:
            db.session.rollback()
            app.logger.exception("Verification email delivery failed")
            flash(str(exc), "error")
            return render_template("auth.html", mode="login", return_to=request.form.get("return_to", "/"))

        session["pending_email"] = email
        session["pending_return_to"] = _safe_return_to(request.form.get("return_to"))
        return redirect(url_for("verify"))
    return render_template("auth.html", mode="login", return_to=request.args.get("returnTo", "/"))


@app.route("/verify", methods=["GET", "POST"])
def verify():
    if g.user:
        return redirect(url_for("home"))
    email = session.get("pending_email")
    if not email:
        flash("Enter your email to request a verification code.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        submitted = "".join(filter(str.isdigit, request.form.get("code", "")))
        record = (
            VerificationCode.query.filter_by(email=email, consumed_at=None)
            .order_by(VerificationCode.id.desc())
            .first()
        )
        now = _utcnow()
        if not record or record.expires_at < now:
            flash("That code has expired. Request a new one.", "error")
            return redirect(url_for("login", returnTo=session.get("pending_return_to", "/")))
        if record.attempts >= 5:
            flash("Too many incorrect attempts. Request a new code.", "error")
            return redirect(url_for("login", returnTo=session.get("pending_return_to", "/")))
        if len(submitted) != 6 or not check_password_hash(record.code_hash, submitted):
            record.attempts += 1
            if record.attempts >= 5:
                record.consumed_at = now
            db.session.commit()
            flash("Incorrect verification code.", "error")
            return render_template("auth.html", mode="verify", pending_email=email)

        record.consumed_at = now
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                email=email,
                full_name=email.split("@", 1)[0],
                password_hash=generate_password_hash(secrets.token_urlsafe(32)),
                role="admin" if User.query.count() == 0 else "user",
            )
            db.session.add(user)
        db.session.commit()
        return_to = _safe_return_to(session.get("pending_return_to"))
        session.clear()
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect(return_to)

    return render_template("auth.html", mode="verify", pending_email=email)


@app.route("/register", methods=["GET", "POST"])
def register():
    return redirect(url_for("login", returnTo=request.args.get("returnTo", "/")))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    return redirect(url_for("login"))


@app.route("/reset-password")
def reset_password():
    return redirect(url_for("login"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/")
def home():
    socials = SocialLink.query.order_by(SocialLink.order.asc(), SocialLink.id.asc()).all()
    team = TeamMember.query.order_by(TeamMember.id.asc()).all()
    return render_template("home.html", socials=socials, team=team)


@app.route("/feedback")
@role_required("admin", "developer")
def feedback():
    logs = FeedbackLog.query.order_by(FeedbackLog.created_at.desc()).all()
    tags = FeedbackTag.query.order_by(FeedbackTag.name.asc()).all()
    responses = FeedbackResponse.query.all()
    return render_template("feedback.html", logs=logs, tags=tags, responses=responses)


@app.post("/feedback/new")
@role_required("admin", "developer")
def feedback_new():
    title = request.form.get("title", "").strip()
    if not title:
        flash("A log title is required", "error")
        return redirect(url_for("feedback"))
    log = FeedbackLog(
        title=title[:160],
        interviewee_name=request.form.get("interviewee_name", "").strip()[:100],
        interview_date=_safe_date(request.form.get("interview_date", "")),
        location=request.form.get("location", "").strip()[:120],
        notes=request.form.get("notes", "").strip(),
        created_by_id=g.user.id,
    )
    db.session.add(log)
    db.session.commit()
    flash("Interview log created", "success")
    return redirect(url_for("feedback_detail", log_id=log.id))


@app.route("/feedback/<int:log_id>")
@role_required("admin", "developer")
def feedback_detail(log_id: int):
    log = db.get_or_404(FeedbackLog, log_id)
    tags = FeedbackTag.query.order_by(FeedbackTag.name.asc()).all()
    return render_template("feedback_detail.html", log=log, tags=tags)


@app.post("/feedback/<int:log_id>/edit")
@role_required("admin", "developer")
def feedback_edit(log_id: int):
    log = db.get_or_404(FeedbackLog, log_id)
    log.title = request.form.get("title", "").strip()[:160] or log.title
    log.interviewee_name = request.form.get("interviewee_name", "").strip()[:100]
    log.interview_date = _safe_date(request.form.get("interview_date", ""))
    log.location = request.form.get("location", "").strip()[:120]
    log.notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("Interview log updated", "success")
    return redirect(url_for("feedback_detail", log_id=log.id))


@app.post("/feedback/<int:log_id>/response")
@role_required("admin", "developer")
def feedback_response_new(log_id: int):
    log = db.get_or_404(FeedbackLog, log_id)
    question = request.form.get("question_text", "").strip()
    answer = request.form.get("answer", "").strip()
    if not question or not answer:
        flash("Both a question and answer are required", "error")
        return redirect(url_for("feedback_detail", log_id=log.id))
    tag = db.session.get(FeedbackTag, request.form.get("tag_id", type=int))
    response = FeedbackResponse(
        log_id=log.id,
        tag_id=tag.id if tag else None,
        tag_name=tag.name if tag else "",
        question_text=question,
        answer=answer,
    )
    db.session.add(response)
    db.session.commit()
    flash("Response added", "success")
    return redirect(url_for("feedback_detail", log_id=log.id))


@app.post("/feedback/<int:log_id>/response/<int:response_id>/delete")
@role_required("admin", "developer")
def feedback_response_delete(log_id: int, response_id: int):
    response = db.get_or_404(FeedbackResponse, response_id)
    if response.log_id != log_id:
        abort(404)
    db.session.delete(response)
    db.session.commit()
    flash("Response removed", "success")
    return redirect(url_for("feedback_detail", log_id=log_id))


@app.post("/feedback/<int:log_id>/delete")
@role_required("admin")
def feedback_delete(log_id: int):
    db.session.delete(db.get_or_404(FeedbackLog, log_id))
    db.session.commit()
    flash("Interview log removed", "success")
    return redirect(url_for("feedback"))


@app.post("/tags/new")
@role_required("admin", "developer")
def tag_new():
    name = request.form.get("name", "").strip()[:100]
    options = [line.strip() for line in request.form.get("options", "").splitlines() if line.strip()]
    if not name or len(options) < 2:
        flash("Add a name and at least two answer options", "error")
    else:
        db.session.add(
            FeedbackTag(
                name=name,
                description=request.form.get("description", "").strip()[:240],
                options_json=json.dumps(options),
            )
        )
        try:
            db.session.commit()
            flash("Tag created", "success")
        except IntegrityError:
            db.session.rollback()
            flash("A tag with that name already exists", "error")
    return redirect(url_for("feedback") + "#tags")


@app.route("/admin")
@role_required("admin")
def admin():
    return render_template(
        "admin.html",
        users=User.query.order_by(User.created_at.asc()).all(),
        team=TeamMember.query.order_by(TeamMember.id.asc()).all(),
        socials=SocialLink.query.order_by(SocialLink.order.asc()).all(),
    )


@app.post("/admin/user/<int:user_id>/role")
@role_required("admin")
def admin_user_role(user_id: int):
    user = db.get_or_404(User, user_id)
    role = request.form.get("role", "")
    if role not in {"admin", "developer", "user"}:
        abort(400)
    if user.id == g.user.id and role != "admin" and User.query.filter_by(role="admin").count() == 1:
        flash("You cannot remove the only administrator", "error")
    else:
        user.role = role
        db.session.commit()
        flash(f"{user.email} is now {role}", "success")
    return redirect(url_for("admin"))


@app.post("/admin/team/new")
@role_required("admin")
def admin_team_new():
    name = request.form.get("name", "").strip()
    role = request.form.get("team_role", "Contributor")
    avatar_url = request.form.get("avatar_url", "").strip()
    profile_url = request.form.get("profile_url", "").strip()
    if not name or role not in {"Management", "Moderation", "Contributor"}:
        flash("Name and a valid role group are required", "error")
    elif not _valid_external_url(avatar_url) or not _valid_external_url(profile_url):
        flash("Profile and avatar links must use http or https", "error")
    else:
        db.session.add(
            TeamMember(
                name=name[:100],
                team_role=role,
                title=request.form.get("title", "").strip()[:120],
                avatar_url=avatar_url[:500],
                profile_url=profile_url[:500],
                bio=request.form.get("bio", "").strip(),
            )
        )
        db.session.commit()
        flash("Team member added", "success")
    return redirect(url_for("admin") + "#team")


@app.post("/admin/team/<int:member_id>/delete")
@role_required("admin")
def admin_team_delete(member_id: int):
    db.session.delete(db.get_or_404(TeamMember, member_id))
    db.session.commit()
    flash("Team member removed", "success")
    return redirect(url_for("admin") + "#team")


@app.post("/admin/social/new")
@role_required("admin")
def admin_social_new():
    link = request.form.get("url", "").strip()
    if not _valid_external_url(link) or not link:
        flash("A valid http or https URL is required", "error")
    else:
        platform = request.form.get("platform", "website").lower()[:30]
        db.session.add(
            SocialLink(
                platform=platform,
                label=request.form.get("label", "").strip()[:100] or platform.title(),
                url=link[:500],
                icon=platform,
                order=request.form.get("order", 0, type=int) or 0,
            )
        )
        db.session.commit()
        flash("Social link added", "success")
    return redirect(url_for("admin") + "#socials")


@app.post("/admin/social/<int:link_id>/delete")
@role_required("admin")
def admin_social_delete(link_id: int):
    db.session.delete(db.get_or_404(SocialLink, link_id))
    db.session.commit()
    flash("Social link removed", "success")
    return redirect(url_for("admin") + "#socials")


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Administrator access required."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="That page could not be found."), 404


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
