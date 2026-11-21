# API

Interactive docs are served at `/api/docs`. Authenticate with `Authorization: Bearer <jwt>` (from login) or
`X-API-Key: rw_...` (created under `POST /api/keys`). Use `X-Org-Id` to pick an organization you belong to.

```bash
KEY=rw_...
curl -X POST https://clips.example.com/api/jobs -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/talk.mp4","options":{"max_clips":5,"caption_style":"pop","platform":"shorts"}}'
curl -H "X-API-Key: $KEY" https://clips.example.com/api/jobs/<id>          # status + clips with signed URLs
curl -N -H "X-API-Key: $KEY" https://clips.example.com/api/jobs/<id>/events   # server-sent progress
```

Main groups: `/api/auth/*`, `/api/me`, `/api/orgs/*`, `/api/keys`, `/api/jobs*`, `/api/clips/*`,
`/api/webhooks*`, `/api/usage`, `/api/audit`, `/api/admin/*`, `/api/health`, `/api/ready`, `/api/metrics`.

Plan-limit failures return HTTP 402 with `{"detail":{"code":...,"message":...}}`.

## Webhook verification
Each delivery carries `X-Reelwright-Event` and an `X-Reelwright-Signature` header of the form `t=<unix>,v1=<hex>` over the raw body; verify with
`reelwright.security.verify_webhook(secret, body, header)` or the same HMAC-SHA256 in your language.

## MCP
`pip install "reelwright[mcp]"`, then register the command `reelwright-mcp` with env `REELWRIGHT_URL` and
`REELWRIGHT_API_KEY`. Tools: `create_clips`, `get_job`, `list_jobs`, `wait_for_job`, `cancel_job`, `usage`.
