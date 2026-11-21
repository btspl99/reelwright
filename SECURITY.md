# Security policy

Report vulnerabilities privately to the maintainers (use GitHub's "Report a vulnerability" on the repository)
rather than opening a public issue. Please include steps to reproduce. We aim to acknowledge within 7 days.

## Built-in protections
- Passwords hashed with scrypt; JWT sessions; API keys stored only as hashes and shown once.
- Source URLs are checked against private, loopback and link-local addresses (SSRF) before download; so are webhook URLs.
- Per-organization data isolation and role checks (viewer < member < admin < owner).
- Signed, expiring media links; HMAC-signed webhooks with timestamps; login rate limiting.
- Strict Content-Security-Policy; the UI never injects untrusted text as HTML.

## Operator checklist
- Run behind HTTPS (the bundled Caddy does this automatically).
- Set `REELWRIGHT_REGISTRATION_OPEN=false` after creating your accounts if the instance is private.
- Keep `REELWRIGHT_ALLOW_PRIVATE_URLS=false` on any internet-facing instance.
- Back up the data directory (it contains the SQLite database and the secret key).
- Processing untrusted videos runs ffmpeg on them. Run the container as non-root (the image does) and keep ffmpeg updated.
