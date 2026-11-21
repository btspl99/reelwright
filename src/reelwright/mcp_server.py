"""MCP server exposing Reelwright to AI assistants.

Configure with ``REELWRIGHT_URL`` (default http://127.0.0.1:8000) and ``REELWRIGHT_API_KEY``.
Tool bodies are plain functions that take an ``httpx.Client`` so they can be tested without MCP.
"""

from __future__ import annotations

import os
import time

import httpx


def make_client(base_url: str | None = None, api_key: str | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=(base_url or os.environ.get("REELWRIGHT_URL", "http://127.0.0.1:8000")).rstrip("/") + "/api",
        headers={"X-API-Key": api_key or os.environ.get("REELWRIGHT_API_KEY", "")},
        timeout=60,
    )


def _json(resp: httpx.Response):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"Reelwright API error {resp.status_code}: {detail}")
    return resp.json() if resp.content else None


def create_clips(client: httpx.Client, url: str, max_clips: int = 5, caption_style: str = "classic", platform: str = "shorts") -> dict:
    return _json(client.post("/jobs", json={"url": url, "options": {"max_clips": max_clips, "caption_style": caption_style, "platform": platform}}))


def get_job(client: httpx.Client, job_id: str) -> dict:
    return _json(client.get(f"/jobs/{job_id}"))


def list_jobs(client: httpx.Client, limit: int = 10) -> dict:
    return _json(client.get("/jobs", params={"limit": limit}))


def wait_for_job(client: httpx.Client, job_id: str, timeout_seconds: int = 900, poll_seconds: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = get_job(client, job_id)
        if job["status"] in ("succeeded", "failed", "canceled") or time.monotonic() > deadline:
            return job
        time.sleep(poll_seconds)


def cancel_job(client: httpx.Client, job_id: str) -> dict:
    return _json(client.post(f"/jobs/{job_id}/cancel"))


def usage(client: httpx.Client) -> dict:
    return _json(client.get("/usage"))


def main() -> None:
    import inspect

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("reelwright")
    client = make_client()
    for fn in (create_clips, get_job, list_jobs, wait_for_job, cancel_job, usage):
        server.tool()(_bind(fn, client, inspect))
    server.run()


def _bind(fn, client, inspect):
    """Return ``fn`` with its leading ``client`` argument pre-filled and hidden from the tool schema."""

    def tool(*args, **kwargs):
        return fn(client, *args, **kwargs)

    tool.__name__ = fn.__name__
    tool.__doc__ = fn.__doc__ or fn.__name__.replace("_", " ")
    sig = inspect.signature(fn)
    tool.__signature__ = sig.replace(parameters=[p for n, p in sig.parameters.items() if n != "client"])
    tool.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "client"}
    return tool


if __name__ == "__main__":
    main()
