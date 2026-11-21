import pytest

from reelwright import config, db


@pytest.fixture()
def settings(tmp_path):
    s = config.Settings.from_env().with_overrides(
        data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/t.db", embedded_worker=False,
        allow_private_urls=False, secret_key="test-secret", registration_open=True, enforce_plans=False,
    )
    s.ensure_dirs()
    config.set_settings_override(s)
    db.init_db()
    yield s
    config.set_settings_override(None)


@pytest.fixture()
def client(settings):
    from fastapi.testclient import TestClient

    from reelwright.api.app import create_app
    from reelwright.api.deps import login_limiter

    login_limiter.reset()

    with TestClient(create_app(start_worker=False)) as c:
        yield c


def register(client, email="a@example.com", password="correct-horse-1"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()
