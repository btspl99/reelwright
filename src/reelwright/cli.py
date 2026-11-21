"""Command line entry point: ``reelwright serve | worker | cleanup``."""

from __future__ import annotations

import argparse
import shutil
import threading
from datetime import timedelta

from sqlalchemy import select

from .config import get_settings
from .db import init_db, session_scope, utcnow
from .models import Clip, Job


def serve(host: str, port: int) -> None:
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*" if host != "127.0.0.1" else "127.0.0.1")


def worker() -> None:
    from .worker import run_loop

    init_db()
    stop = threading.Event()
    threads = [threading.Thread(target=run_loop, args=(stop,), daemon=True) for _ in range(get_settings().worker_concurrency)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()


def cleanup(days: int, dry_run: bool) -> int:
    """Delete finished jobs (and their files) older than ``days`` days."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=days)
    removed = 0
    with session_scope() as db:
        old = db.scalars(select(Job).where(Job.finished_at < cutoff, Job.status.in_(("succeeded", "failed", "canceled")), Job.kind == "pipeline")).all()
        for job in old:
            removed += 1
            if dry_run:
                continue
            shutil.rmtree(settings.clips_dir / job.id, ignore_errors=True)
            shutil.rmtree(settings.uploads_dir / job.id, ignore_errors=True)
            for clip in db.scalars(select(Clip).where(Clip.job_id == job.id)):
                db.delete(clip)
            db.delete(job)
    return removed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reelwright")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="Run the API, web UI and (by default) an embedded worker")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="Run only a worker process")
    p_clean = sub.add_parser("cleanup", help="Delete old finished jobs and their files")
    p_clean.add_argument("--days", type=int, default=30)
    p_clean.add_argument("--dry-run", action="store_true")
    sub.add_parser("init-db", help="Create database tables")
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.host, args.port)
    elif args.command == "worker":
        worker()
    elif args.command == "cleanup":
        init_db()
        print(f"{'Would delete' if args.dry_run else 'Deleted'} {cleanup(args.days, args.dry_run)} job(s)")
    elif args.command == "init-db":
        init_db()
        print("Database ready.")


if __name__ == "__main__":
    main()
