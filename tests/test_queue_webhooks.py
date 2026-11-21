from datetime import timedelta

import httpx
from sqlalchemy import select

from reelwright import queue, security, webhooks
from reelwright.db import session_scope, utcnow
from reelwright.models import Job, Membership, Organization, User, Webhook, WebhookDelivery


def seed(settings):
    with session_scope() as db:
        u = User(email="q@example.com", password_hash="x")
        o = Organization(name="o", plan="free", personal=True)
        db.add_all([u, o]); db.flush()
        db.add(Membership(user_id=u.id, org_id=o.id, role="owner"))
        return u.id, o.id


def add_job(uid, oid, **kw):
    with session_scope() as db:
        j = Job(org_id=oid, user_id=uid, source_type="url", source_ref="https://example.com/v.mp4", **kw)
        db.add(j); db.flush()
        return j.id


def test_claim_is_exclusive_and_ordered(settings):
    uid, oid = seed(settings)
    a, b = add_job(uid, oid), add_job(uid, oid)
    first, second = queue.claim_next(settings), queue.claim_next(settings)
    assert {first, second} == {a, b} and first != second
    assert queue.claim_next(settings) is None


def test_stale_jobs_requeued_then_failed(settings):
    uid, oid = seed(settings)
    jid = add_job(uid, oid, status="running", heartbeat_at=utcnow() - timedelta(hours=2), attempts=1)
    assert queue.requeue_stale(settings) == 1
    with session_scope() as db:
        assert db.get(Job, jid).status == "queued"
    with session_scope() as db:
        j = db.get(Job, jid); j.status, j.attempts, j.heartbeat_at = "running", 3, utcnow() - timedelta(hours=2)
    queue.requeue_stale(settings)
    with session_scope() as db:
        assert db.get(Job, jid).status == "failed"


def test_webhook_delivery_signed_and_retried(settings):
    uid, oid = seed(settings)
    with session_scope() as db:
        db.add(Webhook(org_id=oid, url="https://example.com/h", secret="whsec_x", events=["*"]))
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(500 if len(seen) == 1 else 200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    webhooks.dispatch(oid, "job.succeeded", {"job_id": "j"}, settings=settings, client=client, sleep=lambda _: None)
    assert len(seen) == 2  # one retry after the 500
    req = seen[-1]
    header = next(v for k, v in req.headers.items() if "signature" in k.lower())
    assert security.verify_webhook("whsec_x", req.content, header)
    with session_scope() as db:
        rows = db.scalars(select(WebhookDelivery)).all()
        assert [r.ok for r in sorted(rows, key=lambda r: r.attempt)] == [False, True]


def test_webhook_event_filter(settings):
    uid, oid = seed(settings)
    with session_scope() as db:
        db.add(Webhook(org_id=oid, url="https://example.com/h", secret="s", events=["job.failed"]))
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    webhooks.dispatch(oid, "job.succeeded", {}, settings=settings, client=client, sleep=lambda _: None)
    assert seen == []
