from sqlalchemy import select

from reelwright.db import session_scope
from reelwright.models import Job, Organization

from .conftest import register


def test_first_user_is_admin_second_is_not(client):
    h1, d1 = register(client, "a@example.com")
    _, d2 = register(client, "b@example.com")
    assert d1["user"]["is_admin"] is True and d2["user"]["is_admin"] is False
    assert client.get("/api/admin/stats", headers=h1).status_code == 200


def test_password_rules_and_duplicates(client):
    assert client.post("/api/auth/register", json={"email": "a@example.com", "password": "short"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "bad", "password": "correct-horse-1"}).status_code == 422
    register(client)
    assert client.post("/api/auth/register", json={"email": "A@example.com", "password": "correct-horse-1"}).status_code == 409


def test_login(client):
    register(client)
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "correct-horse-1"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "nope-nope-nope"}).status_code == 401


def test_registration_closed(client, settings):
    from reelwright import config

    register(client)
    config.set_settings_override(settings.with_overrides(registration_open=False))
    assert client.post("/api/auth/register", json={"email": "b@example.com", "password": "correct-horse-1"}).status_code == 403


def test_auth_required(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"X-API-Key": "rw_bogus"}).status_code == 401


def test_job_lifecycle_and_isolation(client):
    h1, _ = register(client, "a@example.com")
    h2, _ = register(client, "b@example.com")
    r = client.post("/api/jobs", json={"url": "https://example.com/v.mp4", "options": {"max_clips": 3}}, headers=h1)
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.get(f"/api/jobs/{jid}", headers=h1).status_code == 200
    assert client.get(f"/api/jobs/{jid}", headers=h2).status_code == 404  # other tenant
    assert client.get("/api/jobs", headers=h2).json()["total"] == 0
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h1).json()["status"] == "canceled"
    assert client.delete(f"/api/jobs/{jid}", headers=h1).status_code == 204


def test_rejects_private_url_and_bad_options(client):
    h, _ = register(client)
    assert client.post("/api/jobs", json={"url": "http://169.254.169.254/"}, headers=h).status_code == 400
    bad = {"url": "https://example.com/v.mp4", "options": {"min_seconds": 50, "max_seconds": 20}}
    assert client.post("/api/jobs", json=bad, headers=h).status_code == 422


def test_api_key_flow(client):
    h, _ = register(client)
    k = client.post("/api/keys", json={"name": "ci"}, headers=h).json()
    kh = {"X-API-Key": k["key"]}
    assert client.get("/api/jobs", headers=kh).status_code == 200
    assert client.post("/api/keys", json={"name": "x"}, headers=kh).status_code == 403  # keys cannot mint keys
    assert "key" not in client.get("/api/keys", headers=h).json()[0]
    assert client.delete(f"/api/keys/{k['id']}", headers=h).status_code == 204
    assert client.get("/api/jobs", headers=kh).status_code == 401


def test_rbac_viewer_cannot_create(client):
    h1, _ = register(client, "owner@example.com")
    h2, d2 = register(client, "viewer@example.com")
    org_id = client.get("/api/me", headers=h1).json()["active_org"]["id"]
    assert client.post("/api/orgs/current/members", json={"email": "viewer@example.com", "role": "viewer"}, headers={**h1, "X-Org-Id": org_id}).status_code == 201
    hv = {**h2, "X-Org-Id": org_id}
    assert client.get("/api/jobs", headers=hv).status_code == 200
    assert client.post("/api/jobs", json={"url": "https://example.com/v.mp4"}, headers=hv).status_code == 403
    assert client.get("/api/audit", headers=hv).status_code == 403


def test_non_member_cannot_use_org_header(client):
    h1, _ = register(client, "a@example.com")
    h2, _ = register(client, "b@example.com")
    org1 = client.get("/api/me", headers=h1).json()["active_org"]["id"]
    assert client.get("/api/jobs", headers={**h2, "X-Org-Id": org1}).status_code in (403, 404)


def test_last_owner_protected(client):
    h, d = register(client)
    r = client.patch(f"/api/orgs/current/members/{d['user']['id']}", json={"email": "", "role": "member"}, headers=h)
    assert r.status_code == 409


def test_plan_limits_return_402(client, settings):
    from reelwright import config

    h, d = register(client)
    uid = d["user"]["id"]
    config.set_settings_override(settings.with_overrides(enforce_plans=True))
    with session_scope() as db:
        org = db.scalars(select(Organization)).first()
        db.add(Job(org_id=org.id, user_id=uid, source_type="url", source_ref="u", status="running"))
        db.add(Job(org_id=org.id, user_id=uid, source_type="url", source_ref="u", status="running"))
    r = client.post("/api/jobs", json={"url": "https://example.com/v.mp4"}, headers=h)
    assert r.status_code == 402 and r.json()["detail"]["code"]


def test_webhook_validation_and_secret_once(client):
    h, _ = register(client)
    assert client.post("/api/webhooks", json={"url": "http://127.0.0.1/h"}, headers=h).status_code == 400
    assert client.post("/api/webhooks", json={"url": "https://example.com/h", "events": ["nope"]}, headers=h).status_code == 422
    r = client.post("/api/webhooks", json={"url": "https://example.com/h"}, headers=h)
    assert r.status_code == 201 and r.json()["secret"].startswith("whsec_")
    assert "secret" not in client.get("/api/webhooks", headers=h).json()[0]


def test_media_link_rejects_bad_signature(client):
    assert client.get("/media/abc/video?exp=9999999999&sig=bad").status_code == 403


def test_health_and_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/metrics").status_code == 404  # disabled without a token


def test_login_is_rate_limited(client):
    register(client)
    codes = [client.post("/api/auth/login", json={"email": "a@example.com", "password": "bad-bad-bad-1"}).status_code for _ in range(10)]
    assert 429 in codes
