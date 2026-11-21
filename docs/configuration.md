# Configuration

All settings are environment variables prefixed `REELWRIGHT_`. A `.env` file next to `docker-compose.yml` is read by Compose.

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | Database, uploads, clips and the auto-generated secret key. Back this up. |
| `DATABASE_URL` | SQLite in `DATA_DIR` | Use `postgresql+psycopg://user:pass@host/db` for Postgres. |
| `SECRET_KEY` | generated | Signs sessions and media links. Set it explicitly when running several instances. |
| `PUBLIC_URL` | `http://localhost:8000` | Public address of the site. |
| `REGISTRATION_OPEN` | `true` | The first account is always allowed and becomes instance admin. |
| `ENFORCE_PLANS` | `false` | When true, plan quotas (minutes, seats, concurrency, upload size) are enforced. |
| `EMBEDDED_WORKER` | `true` | Run a worker inside the web process. Set false and run `reelwright worker` separately to scale. |
| `WORKER_CONCURRENCY` | `1` | Jobs processed in parallel per worker process. ffmpeg is CPU heavy; keep this low. |
| `MAX_UPLOAD_BYTES` | 4 GB | Upload limit. |
| `MAX_SOURCE_MINUTES` | `240` | Longest source video accepted. |
| `ALLOW_PRIVATE_URLS` | `false` | Allow URLs pointing at private networks. Leave off on public servers. |
| `TRANSCRIPTION_PROVIDER` | `faster-whisper` | `faster-whisper` (local), `openai` (any OpenAI-compatible API), or `sidecar` (JSON next to the file). |
| `WHISPER_MODEL` / `WHISPER_DEVICE` | `small` / `auto` | Local model size (`base`, `small`, `medium`) and `cpu`/`cuda`. |
| `TRANSCRIPTION_BASE_URL` / `_API_KEY` / `_MODEL` | OpenAI defaults | For the `openai` provider. |
| `LLM_BASE_URL` / `_API_KEY` / `_MODEL` | empty | Optional LLM re-ranking of clip candidates. Without it, a built-in heuristic is used. |
| `VIDEO_ENCODER` | `libx264` | Also `h264_nvenc` (NVIDIA) or `h264_videotoolbox` (macOS). |
| `CAPTION_FONT` | `DejaVu Sans` | Must be installed on the server. |
| `TOKEN_TTL_HOURS` | `168` | Login session length. |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API from a browser. |
| `METRICS_TOKEN` | empty | Enables `/api/metrics` (Prometheus text) behind `Authorization: Bearer <token>`. |

## Plans
`free`, `pro`, `team`, `enterprise` are defined in `src/reelwright/plans.py`. They only matter when `ENFORCE_PLANS=true`.
Instance admins change an organization's plan with `PATCH /api/admin/orgs/{id}`. There is no payment integration:
this is an access-control layer you can connect to your own billing.
