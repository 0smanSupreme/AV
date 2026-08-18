"""Admin Vote — Flask/Vercel edition.

The public game hub needs no account. Optional local accounts use a username
and password; administrators and developers can access private feedback logs.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


USERNAME_RE = re.compile(r"^[a-z0-9_]{3,24}$")
ALLOWED_AVATAR_BYTES = 700 * 1024


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
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    """Local account using old column names for database compatibility."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column("email", db.String(254), unique=True, nullable=False, index=True)
    display_name = db.Column("full_name", db.String(100), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    avatar = db.relationship(
        "UserAvatar", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserAvatar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    mime_type = db.Column(db.String(30), nullable=False)
    image_data = db.Column(db.LargeBinary, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    user = db.relationship("User", back_populates="avatar")


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


class RoadmapItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="planned")
    target_date = db.Column(db.Date, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


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


def _normalise_username(value: str) -> str:
    return (value or "").strip().lower()


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


def _avatar_payload(upload) -> tuple[str, bytes] | None:
    if not upload or not upload.filename:
        return None
    data = upload.read(ALLOWED_AVATAR_BYTES + 1)
    if len(data) > ALLOWED_AVATAR_BYTES:
        raise ValueError("Profile pictures must be 700 KB or smaller.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Use a PNG, JPG, GIF, or WebP profile picture.")
    return mime, data


def _set_avatar(user: User, payload: tuple[str, bytes] | None) -> None:
    if not payload:
        return
    mime, data = payload
    if user.avatar:
        user.avatar.mime_type = mime
        user.avatar.image_data = data
    else:
        user.avatar = UserAvatar(mime_type=mime, image_data=data)


def _safe_return_to(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return url_for("home")
    return value


def _ensure_environment_admin() -> None:
    username = _normalise_username(os.getenv("ADMIN_USERNAME", ""))
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username and not password:
        return
    if not USERNAME_RE.fullmatch(username) or len(password) < 8:
        app.logger.error(
            "ADMIN_USERNAME must be 3-24 letters/numbers/underscores and ADMIN_PASSWORD must be at least 8 characters."
        )
        return
    user = User.query.filter_by(username=username).first()
    display_name = os.getenv("ADMIN_DISPLAY_NAME", "Administrator").strip()[:50] or "Administrator"
    if not user:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=generate_password_hash(password),
            role="admin",
        )
        db.session.add(user)
    else:
        user.role = "admin"
        if not check_password_hash(user.password_hash, password):
            user.password_hash = generate_password_hash(password)
        if not user.display_name:
            user.display_name = display_name
    db.session.commit()


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


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    return_to = _safe_return_to(request.values.get("return_to") or request.args.get("returnTo"))
    if request.method == "POST":
        username = _normalise_username(request.form.get("username", ""))
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Incorrect username or password.", "error")
            return render_template("auth.html", mode="login", return_to=return_to), 401
        session.clear()
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect(return_to)
    return render_template("auth.html", mode="login", return_to=return_to)


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("profile"))
    if request.method == "POST":
        username = _normalise_username(request.form.get("username", ""))
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        errors = []
        if not USERNAME_RE.fullmatch(username):
            errors.append("Username must be 3-24 letters, numbers, or underscores.")
        if not 2 <= len(display_name) <= 50:
            errors.append("Display name must be 2-50 characters.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        try:
            avatar = _avatar_payload(request.files.get("avatar"))
        except ValueError as exc:
            errors.append(str(exc))
            avatar = None
        if User.query.filter_by(username=username).first():
            errors.append("That username is already taken.")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("auth.html", mode="register"), 400

        user = User(
            username=username,
            display_name=display_name,
            password_hash=generate_password_hash(password),
            role="user",
        )
        _set_avatar(user, avatar)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That username is already taken.", "error")
            return render_template("auth.html", mode="register"), 409
        session.clear()
        session["user_id"] = user.id
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        flash("Your account has been created.", "success")
        return redirect(url_for("profile"))
    return render_template("auth.html", mode="register")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        if not 2 <= len(display_name) <= 50:
            flash("Display name must be 2-50 characters.", "error")
            return render_template("profile.html"), 400
        if new_password:
            if not check_password_hash(g.user.password_hash, current_password):
                flash("Enter your current password before changing it.", "error")
                return render_template("profile.html"), 400
            if len(new_password) < 8:
                flash("The new password must be at least 8 characters.", "error")
                return render_template("profile.html"), 400
            g.user.password_hash = generate_password_hash(new_password)
        try:
            avatar = _avatar_payload(request.files.get("avatar"))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("profile.html"), 400
        if request.form.get("remove_avatar") == "1" and g.user.avatar:
            db.session.delete(g.user.avatar)
        elif avatar:
            _set_avatar(g.user, avatar)
        g.user.display_name = display_name
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.get("/profile-picture/<int:user_id>")
def profile_picture(user_id: int):
    avatar = UserAvatar.query.filter_by(user_id=user_id).first_or_404()
    response = make_response(avatar.image_data)
    response.headers["Content-Type"] = avatar.mime_type
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/")
def home():
    socials = SocialLink.query.order_by(SocialLink.order.asc(), SocialLink.id.asc()).all()
    team = TeamMember.query.order_by(TeamMember.id.asc()).all()
    roadmap = RoadmapItem.query.order_by(RoadmapItem.sort_order.asc(), RoadmapItem.id.desc()).all()
    return render_template("home.html", socials=socials, team=team, roadmap=roadmap)


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
    db.session.add(
        FeedbackResponse(
            log_id=log.id,
            tag_id=tag.id if tag else None,
            tag_name=tag.name if tag else "",
            question_text=question,
            answer=answer,
        )
    )
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
        roadmap=RoadmapItem.query.order_by(RoadmapItem.sort_order.asc()).all(),
        environment_admin=_normalise_username(os.getenv("ADMIN_USERNAME", "")),
    )


@app.post("/admin/user/<int:user_id>/role")
@role_required("admin")
def admin_user_role(user_id: int):
    user = db.get_or_404(User, user_id)
    role = request.form.get("role", "")
    environment_admin = _normalise_username(os.getenv("ADMIN_USERNAME", ""))
    if role not in {"admin", "developer", "user"}:
        abort(400)
    if user.username == environment_admin and role != "admin":
        flash("The environment-variable administrator cannot be demoted.", "error")
    elif user.id == g.user.id and role != "admin" and User.query.filter_by(role="admin").count() == 1:
        flash("You cannot remove the only administrator", "error")
    else:
        user.role = role
        db.session.commit()
        flash(f"@{user.username} is now {role}", "success")
    return redirect(url_for("admin") + "#access")


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


@app.post("/admin/roadmap/new")
@role_required("admin")
def admin_roadmap_new():
    title = request.form.get("title", "").strip()
    status = request.form.get("status", "planned")
    if not title or status not in {"planned", "in_progress", "released"}:
        flash("A title and valid status are required.", "error")
    else:
        db.session.add(
            RoadmapItem(
                title=title[:140],
                summary=request.form.get("summary", "").strip(),
                status=status,
                target_date=_safe_date(request.form.get("target_date", "")),
                sort_order=request.form.get("sort_order", 0, type=int) or 0,
            )
        )
        db.session.commit()
        flash("Roadmap update published.", "success")
    return redirect(url_for("admin") + "#roadmap")


@app.post("/admin/roadmap/<int:item_id>/edit")
@role_required("admin")
def admin_roadmap_edit(item_id: int):
    item = db.get_or_404(RoadmapItem, item_id)
    status = request.form.get("status", "")
    title = request.form.get("title", "").strip()
    if not title or status not in {"planned", "in_progress", "released"}:
        flash("A title and valid status are required.", "error")
    else:
        item.title = title[:140]
        item.summary = request.form.get("summary", "").strip()
        item.status = status
        item.target_date = _safe_date(request.form.get("target_date", ""))
        item.sort_order = request.form.get("sort_order", 0, type=int) or 0
        db.session.commit()
        flash("Roadmap update saved.", "success")
    return redirect(url_for("admin") + "#roadmap")


@app.post("/admin/roadmap/<int:item_id>/delete")
@role_required("admin")
def admin_roadmap_delete(item_id: int):
    db.session.delete(db.get_or_404(RoadmapItem, item_id))
    db.session.commit()
    flash("Roadmap update removed.", "success")
    return redirect(url_for("admin") + "#roadmap")


@app.errorhandler(403)
def forbidden(_error):
    return render_template(
        "error.html", code=403, message="Developer or administrator access is required."
    ), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="That page could not be found."), 404


@app.errorhandler(413)
def too_large(_error):
    return render_template(
        "error.html", code=413, message="That upload is too large. Use an image under 700 KB."
    ), 413


with app.app_context():
    db.create_all()
    _ensure_environment_admin()


if __name__ == "__main__":
    app.run(debug=True)
