import io
import os
import tempfile


database_file = tempfile.NamedTemporaryFile(prefix="admin-vote-test-", suffix=".sqlite3", delete=False)
database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USERNAME"] = "site_admin"
os.environ["ADMIN_PASSWORD"] = "very-secure-test-password"
os.environ["ADMIN_DISPLAY_NAME"] = "Site Administrator"

from app import (  # noqa: E402
    FeedbackLog,
    RoadmapItem,
    SocialLink,
    TeamMember,
    User,
    UserAvatar,
    _ensure_environment_admin,
    app,
    db,
)


def csrf(client, path="/login"):
    client.get(path)
    with client.session_transaction() as current_session:
        return current_session["csrf_token"]


def login(client, username, password):
    token = csrf(client)
    return client.post(
        "/login",
        data={"csrf_token": token, "username": username, "password": password},
    )


def test_public_accounts_profiles_permissions_and_roadmap():
    app.config.update(TESTING=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        _ensure_environment_admin()

    public_home = app.test_client().get("/")
    assert public_home.status_code == 200
    assert b"What We're Building Next" in public_home.data
    assert b"Log in" in public_home.data

    member_client = app.test_client()
    assert member_client.get("/feedback").status_code == 302
    token = csrf(member_client, "/register")
    fake_png = b"\x89PNG\r\n\x1a\n" + b"test-image-content"
    registered = member_client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "Builder_One",
            "display_name": "Builder One",
            "password": "member-password",
            "confirm_password": "member-password",
            "avatar": (io.BytesIO(fake_png), "avatar.png"),
        },
        content_type="multipart/form-data",
    )
    assert registered.status_code == 302
    assert registered.headers["Location"].endswith("/profile")
    assert member_client.get("/feedback").status_code == 403

    with app.app_context():
        member = User.query.filter_by(username="builder_one").one()
        member_id = member.id
        assert member.display_name == "Builder One"
        assert member.role == "user"
        assert UserAvatar.query.filter_by(user_id=member.id).count() == 1
    picture = member_client.get(f"/profile-picture/{member_id}")
    assert picture.status_code == 200
    assert picture.headers["Content-Type"] == "image/png"

    admin_client = app.test_client()
    logged_in = login(admin_client, "site_admin", "very-secure-test-password")
    assert logged_in.status_code == 302
    assert admin_client.get("/admin").status_code == 200
    with admin_client.session_transaction() as current_session:
        admin_token = current_session["csrf_token"]

    promoted = admin_client.post(
        f"/admin/user/{member_id}/role",
        data={"csrf_token": admin_token, "role": "developer"},
    )
    assert promoted.status_code == 302
    roadmap = admin_client.post(
        "/admin/roadmap/new",
        data={
            "csrf_token": admin_token,
            "title": "Political party system",
            "summary": "Create parties, campaign, and contest elections.",
            "status": "in_progress",
            "target_date": "2026-12-01",
            "sort_order": "1",
        },
    )
    assert roadmap.status_code == 302

    for platform, label, url in (
        ("youtube", "Admin Vote YouTube", "https://youtube.com/"),
        ("discord", "Admin Vote Discord", "https://discord.com/"),
        ("roblox", "Play Admin Vote", "https://www.roblox.com/"),
    ):
        assert admin_client.post(
            "/admin/social/new",
            data={"csrf_token": admin_token, "platform": platform, "label": label, "url": url},
        ).status_code == 302

    updated_home = app.test_client().get("/")
    assert b"Political party system" in updated_home.data
    assert b"In progress" in updated_home.data
    assert b"/social-youtube.png" in updated_home.data
    assert b"/social-discord.png" in updated_home.data
    assert b"/social-roblox.jpg" in updated_home.data

    with member_client.session_transaction() as current_session:
        member_token = current_session["csrf_token"]
    member_client.post("/logout", data={"csrf_token": member_token})
    assert login(member_client, "builder_one", "member-password").status_code == 302
    assert member_client.get("/feedback").status_code == 200
    assert member_client.get("/admin").status_code == 403

    with app.app_context():
        assert User.query.filter_by(username="site_admin", role="admin").count() == 1
        assert User.query.filter_by(username="builder_one", role="developer").count() == 1
        assert RoadmapItem.query.count() == 1
        assert SocialLink.query.count() == 3
        assert FeedbackLog.query.count() == 0
        assert TeamMember.query.count() == 0


def teardown_module():
    try:
        os.unlink(database_file.name)
    except FileNotFoundError:
        pass
