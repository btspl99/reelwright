# Reelwright

Self-hostable AI video clipping. Give it a long video (URL or upload) and it returns captioned, vertical
9:16 clips with face-tracked framing, ready for Shorts, TikTok and Reels.

Licensed under **Apache-2.0**. Independent implementation; see [docs/clean-room.md](docs/clean-room.md).

## Quick start
```bash
docker compose up -d --build        # then open http://127.0.0.1:8000
# or
pip install ".[download,whisper,mcp]" && reelwright serve
```
Needs ffmpeg (included in the Docker image). Hosting and hardware: [docs/deployment.md](docs/deployment.md).

## What works today

| Area | Status |
|---|---|
| URL or file in, vertical clips out (1080x1920 H.264/AAC) | Implemented and tested end to end |
| Transcription: local faster-whisper, OpenAI-compatible API, sidecar JSON | Implemented (local Whisper not exercised in CI) |
| Clip selection: heuristic scoring, optional LLM re-rank | Implemented |
| Face-tracked reframing, fill / blurred-fit layouts | Implemented (Haar detector; basic) |
| Captions: classic, pop, boxed, none; hook title | Implemented |
| Re-render a clip with new trim/style/layout | Implemented |
| Web UI with live progress | Implemented (basic) |
| REST API, API keys, SSE progress, signed media links | Implemented |
| Organizations, roles (viewer/member/admin/owner), audit log | Implemented |
| Plans and quotas (free/pro/team/enterprise), usage tracking | Implemented; off by default; no payment integration |
| Signed webhooks with retries and delivery log | Implemented |
| Instance admin (users, plans, stats), health and metrics | Implemented |
| MCP server for AI assistants | Implemented (tool functions tested; not run against a live MCP client) |
| SQLite or Postgres, horizontal workers | Implemented (Postgres path not exercised in CI) |

## Roadmap (not built)
Direct publishing to YouTube/TikTok/Instagram, B-roll insertion, SSO/OIDC/SAML, billing integration,
a timeline editor, GPU autoscaling, better face/speaker tracking, translations of the UI.

## Develop
```bash
pip install -e ".[dev,download,whisper,mcp]"
make check        # ruff + pytest (needs ffmpeg)
```

## Responsible use
Only process content you have rights to. Respect the terms of any site you download from.

See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/configuration.md](docs/configuration.md),
[docs/api.md](docs/api.md), [docs/architecture.md](docs/architecture.md).
