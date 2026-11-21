"""Outgoing signed webhooks."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx
from sqlalchemy import select

from . import __version__
from .config import Settings, get_settings
from .db import session_scope
from .models import Webhook, WebhookDelivery
from .pipeline.urls import UnsafeURLError, validate_source_url
from .security import sign_webhook

EVENTS = ("job.succeeded", "job.failed", "job.canceled")


def dispatch(
    org_id: str,
    event: str,
    payload: dict,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 3,
) -> int:
    """Deliver ``event`` to every active webhook of the organization. Returns the number of successful deliveries."""
    settings = settings or get_settings()
    body = json.dumps({"event": event, "data": payload}, separators=(",", ":"), default=str).encode()
    http = client or httpx.Client(timeout=10, follow_redirects=False)
    delivered = 0
    with session_scope() as db:
        hooks = db.scalars(select(Webhook).where(Webhook.org_id == org_id, Webhook.active.is_(True))).all()
        for hook in hooks:
            if hook.events and event not in hook.events and "*" not in hook.events:
                continue
            ok, status, error = False, 0, ""
            for attempt in range(1, attempts + 1):
                try:
                    validate_source_url(hook.url, allow_private=settings.allow_private_urls)
                    response = http.post(
                        hook.url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": f"Reelwright/{__version__}",
                            "X-Reelwright-Event": event,
                            "X-Reelwright-Signature": sign_webhook(hook.secret, body),
                        },
                    )
                    status = response.status_code
                    ok = 200 <= status < 300
                    error = "" if ok else f"HTTP {status}"
                except (httpx.HTTPError, UnsafeURLError) as exc:
                    error = str(exc)[:300]
                db.add(WebhookDelivery(webhook_id=hook.id, event=event, ok=ok, status_code=status, attempt=attempt, error=error))
                if ok:
                    delivered += 1
                    break
                if isinstance(error, str) and "private or reserved" in error:
                    break
                if attempt < attempts:
                    sleep(min(2 ** attempt, 20))
    return delivered
