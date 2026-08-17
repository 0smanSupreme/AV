import os
import tempfile


database_file = tempfile.NamedTemporaryFile(prefix="admin-vote-test-", suffix=".sqlite3", delete=False)
database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"
os.environ["SECRET_KEY"] = "test-secret-key"

from app import FeedbackLog, FeedbackTag, SocialLink, TeamMember, User, app, db  # noqa: E402


def csrf(client):
    client.get("/login")
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_full_admin_flow():
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()

    client = app.test_client()
    token = csrf(client)
    response = client.post(
        "/register",
        data={
            "csrf_token": token,
            "full_name": "Admin User",
            "email": "admin@example.com",
            "password": "strong-pass-123",
            "confirm": "strong-pass-123",
            "return_to": "/",
        },
    )
    assert response.status_code == 302

    home = client.get("/")
    assert home.status_code == 200
    assert b"ADMIN" in home.data and b"VOTE" in home.data

    admin = client.get("/admin")
    assert admin.status_code == 200
    assert b"Administration" in admin.data

    with client.session_transaction() as session:
        token = session["csrf_token"]
    tag = client.post(
        "/tags/new",
        data={
            "csrf_token": token,
            "name": "Game Clarity",
            "description": "How clear the game feels",
            "options": "Confusing\nClear",
        },
    )
    assert tag.status_code == 302

    log = client.post(
        "/feedback/new",
        data={"csrf_token": token, "title": "First interview", "interviewee_name": "BuilderOne"},
    )
    assert log.status_code == 302

    client.post(
        "/admin/team/new",
        data={"csrf_token": token, "name": "Omair", "team_role": "Management", "title": "Game Director"},
    )
    client.post(
        "/admin/social/new",
        data={
            "csrf_token": token,
            "platform": "roblox",
            "label": "Play Admin Vote",
            "url": "https://www.roblox.com/",
            "order": 1,
        },
    )

    feedback_page = client.get("/feedback")
    assert feedback_page.status_code == 200
    assert b"First interview" in feedback_page.data
    detail = client.get(log.headers["Location"])
    assert detail.status_code == 200
    assert b"Questions & Responses" in detail.data
    updated_home = client.get("/")
    assert b"Omair" in updated_home.data and b"Play Admin Vote" in updated_home.data

    with app.app_context():
        assert User.query.one().role == "admin"
        assert FeedbackTag.query.count() == 1
        assert FeedbackLog.query.count() == 1
        assert TeamMember.query.count() == 1
        assert SocialLink.query.count() == 1


def teardown_module():
    try:
        os.unlink(database_file.name)
    except FileNotFoundError:
        pass
