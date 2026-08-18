import os
import tempfile


database_file = tempfile.NamedTemporaryFile(prefix="admin-vote-test-", suffix=".sqlite3", delete=False)
database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"
os.environ["SECRET_KEY"] = "test-secret-key"

from app import (  # noqa: E402
    FeedbackLog,
    FeedbackTag,
    SocialLink,
    TeamMember,
    User,
    VerificationCode,
    app,
    db,
)


def csrf(client, path="/login"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_public_home_and_passwordless_admin_flow():
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()

    client = app.test_client()

    public_home = client.get("/")
    assert public_home.status_code == 200
    assert b"ADMIN" in public_home.data and b"VOTE" in public_home.data
    assert b"Log in" in public_home.data

    login_page = client.get("/login")
    assert b"Continue with Google" not in login_page.data
    assert b'type="password"' not in login_page.data
    assert b"Email me a verification code" in login_page.data

    protected = client.get("/feedback")
    assert protected.status_code == 302
    assert "/login" in protected.headers["Location"]

    token = csrf(client)
    requested = client.post(
        "/login",
        data={"csrf_token": token, "email": "admin@example.com", "return_to": "/feedback"},
    )
    assert requested.status_code == 302
    assert requested.headers["Location"].endswith("/verify")
    sent = app.extensions["last_verification_code"]
    assert sent["email"] == "admin@example.com"
    assert len(sent["code"]) == 6

    verified = client.post(
        "/verify",
        data={"csrf_token": token, "code": sent["code"]},
    )
    assert verified.status_code == 302
    assert verified.headers["Location"] == "/feedback"

    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert b"Administration" in admin_page.data

    with client.session_transaction() as current_session:
        token = current_session["csrf_token"]

    client.post(
        "/tags/new",
        data={
            "csrf_token": token,
            "name": "Game Clarity",
            "description": "How clear the game feels",
            "options": "Confusing\nClear",
        },
    )
    log = client.post(
        "/feedback/new",
        data={"csrf_token": token, "title": "First interview", "interviewee_name": "BuilderOne"},
    )
    assert log.status_code == 302

    client.post(
        "/admin/team/new",
        data={"csrf_token": token, "name": "Omair", "team_role": "Management", "title": "Game Director"},
    )
    for platform, label, url in (
        ("youtube", "Admin Vote YouTube", "https://youtube.com/"),
        ("discord", "Admin Vote Discord", "https://discord.com/"),
        ("roblox", "Play Admin Vote", "https://www.roblox.com/"),
    ):
        result = client.post(
            "/admin/social/new",
            data={"csrf_token": token, "platform": platform, "label": label, "url": url, "order": 1},
        )
        assert result.status_code == 302

    updated_home = client.get("/")
    assert b"Omair" in updated_home.data
    assert b"/social-youtube.png" in updated_home.data
    assert b"/social-discord.png" in updated_home.data
    assert b"/social-roblox.jpg" in updated_home.data

    detail = client.get(log.headers["Location"])
    assert detail.status_code == 200
    assert b"Questions & Responses" in detail.data

    with app.app_context():
        assert User.query.one().role == "admin"
        assert VerificationCode.query.one().consumed_at is not None
        assert FeedbackTag.query.count() == 1
        assert FeedbackLog.query.count() == 1
        assert TeamMember.query.count() == 1
        assert SocialLink.query.count() == 3

    logged_out = client.post("/logout", data={"csrf_token": token})
    assert logged_out.status_code == 302
    assert logged_out.headers["Location"] == "/"
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 302


def teardown_module():
    try:
        os.unlink(database_file.name)
    except FileNotFoundError:
        pass
