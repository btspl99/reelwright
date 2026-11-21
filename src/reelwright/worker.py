"""Worker loop: claims queued jobs and runs them. Run standalone with ``reelwright worker``."""

from __future__ import annotations

import logging
import threading
import time

from .config import Settings, get_settings
from .pipeline.runner import process_job
from .queue import claim_next, requeue_stale

log = logging.getLogger("reelwright.worker")


def process_one(settings: Settings | None = None) -> str | None:
    """Claim and process a single job. Returns its final status, or ``None`` when the queue is empty."""
    settings = settings or get_settings()
    job_id = claim_next(settings)
    if job_id is None:
        return None
    return process_job(job_id, settings)


def run_loop(stop: threading.Event, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    last_sweep = 0.0
    while not stop.is_set():
        now = time.monotonic()
        if now - last_sweep > 60:
            try:
                requeue_stale(settings)
            except Exception:  # keep the loop alive on transient DB errors
                log.exception("Stale-job sweep failed")
            last_sweep = now
        try:
            handled = process_one(settings)
        except Exception:
            log.exception("Worker iteration failed")
            handled = None
        if handled is None:
            stop.wait(settings.worker_poll_seconds)


def start_threads(settings: Settings | None = None) -> tuple[threading.Event, list[threading.Thread]]:
    settings = settings or get_settings()
    stop = threading.Event()
    threads = [threading.Thread(target=run_loop, args=(stop, settings), name=f"rw-worker-{i}", daemon=True) for i in range(settings.worker_concurrency)]
    for t in threads:
        t.start()
    return stop, threads
