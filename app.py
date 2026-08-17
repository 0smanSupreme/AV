"""Admin Vote — Flask/Vercel edition.

This app mirrors the Base44 project while keeping the backend entirely Python.
Set DATABASE_URL to a Postgres URL in production; local development uses SQLite.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timezone
from functools import wraps
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
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


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
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
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
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
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


@app.context_processor
def template_globals():
    return {
        "csrf_token": _csrf_token,
        "role_label": {"admin": "Administrator", "developer": "Developer", "user": "Member"},
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
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            session.clear()
            session["user_id"] = user.id
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(_safe_return_to(request.form.get("return_to")))
        flash("Invalid email or password", "error")
    return render_template("auth.html", mode="login", return_to=request.args.get("returnTo", "/"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(email) > 254 or "@" not in email:
            flash("Enter a valid email address", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters", "error")
        elif password != confirm:
            flash("Passwords do not match", "error")
        else:
            first_user = User.query.count() == 0
            user = User(
                email=email,
                full_name=full_name or email.split("@", 1)[0],
                password_hash=generate_password_hash(password),
                role="admin" if first_user else "user",
            )
            db.session.add(user)
            try:
                db.session.commit()
                session.clear()
                session["user_id"] = user.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                flash("Account created. The first account receives administrator access.", "success")
                return redirect(_safe_return_to(request.form.get("return_to")))
            except IntegrityError:
                db.session.rollback()
                flash("An account already exists with that email", "error")
    return render_template("auth.html", mode="register", return_to=request.args.get("returnTo", "/"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = request.method == "POST"
    return render_template("auth.html", mode="forgot", sent=sent)


@app.route("/reset-password")
def reset_password():
    return render_template("auth.html", mode="invalid-reset")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
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
