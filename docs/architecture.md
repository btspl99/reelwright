# Architecture

```
Browser / API client / MCP
        |
   FastAPI (api/)  --- SQLAlchemy ---  SQLite or Postgres  (users, orgs, jobs, clips, usage, webhooks, audit)
        |                                   ^
   job rows (queued)                        | claim_next(): atomic UPDATE
        v                                   |
   Worker threads (worker.py, queue.py) ----+
        |
   pipeline/runner.py
     ingest      -> yt-dlp (optional) or upload; SSRF guard
     audio       -> ffmpeg
     transcribe  -> faster-whisper | OpenAI-compatible API | sidecar JSON
     select      -> sentence windows -> heuristic score -> optional LLM re-rank -> non-overlapping picks
     reframe     -> Haar face samples -> dead-zone smoothing -> simplified crop path -> ffmpeg x(t)
     captions    -> ASS subtitles (classic, pop, boxed) + hook title
     render      -> ffmpeg 1080x1920 H.264/AAC + thumbnail
```

**Queue:** jobs are rows. A worker claims one with a conditional UPDATE, heartbeats while running, and a sweeper
returns jobs with a stale heartbeat to the queue (max 3 attempts). Cancel sets a flag the runner checks between stages.

**Scoring:** hook strength, engagement, value and shareability are computed from the transcript text and pacing
(weights 0.35/0.25/0.20/0.20). With an LLM configured, its score is blended 70/30 with the heuristic.

**Tenancy:** every row carries `org_id`; roles are viewer < member < admin < owner. API keys belong to a user and org.

**Webhooks:** `job.succeeded`, `job.failed`, `job.canceled` and others are POSTed with an HMAC-SHA256 signature header,
retried with backoff, and logged in `webhook_deliveries`.
