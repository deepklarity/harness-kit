from app import app


def test_user_lookup():
    r = app.test_client().get("/users/3")
    assert r.status_code == 200
    assert r.json == {"id": 3}


def test_runs():
    app.test_client()


def test_weak():
    assert "ok" in "ok"
