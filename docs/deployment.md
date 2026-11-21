# Hosting and requirements

Reelwright runs ffmpeg on video, which needs real CPU, RAM and disk. **Shared web hosting, PHP hosting and
"Node.js hosting" plans are not suitable.** You need a VPS or a server where you can run Docker (or install ffmpeg).

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Linux x86_64 or arm64 (Ubuntu 22.04/24.04, Debian 12) | same |
| CPU | 2 vCPU | 4+ vCPU |
| RAM | 4 GB (transcription via an API) | 8 GB (local Whisper `small`) |
| Disk | 40 GB | 100+ GB SSD (sources and clips accumulate; use `reelwright cleanup`) |
| Software | Docker + Compose, **or** Python 3.11+ and ffmpeg 6+ with libass and libx264 | Docker |
| Network | Ports 80/443 open, outbound HTTPS | |
| GPU | Not required | Optional: `VIDEO_ENCODER=h264_nvenc`, `WHISPER_DEVICE=cuda` |

Rough speed on 4 vCPU: rendering is near real-time per clip; local Whisper `small` on CPU transcribes a
bit faster than real time. A one-hour source therefore takes tens of minutes. Using an API for transcription cuts that a lot.

## Where to host

- **A VPS from any provider** (Hetzner Cloud, DigitalOcean, Hostinger KVM VPS, Vultr, Linode, AWS Lightsail, etc.).
  Pick a 4 vCPU / 8 GB plan. Check current prices yourself; they change.
- **Hostinger:** use a *VPS* plan, not shared/Business/Cloud web hosting. Your existing `petalpulp.shop` and `btspl.tech`
  domains can point to the VPS with an A record (for example `clips.btspl.tech`).
- **Oracle Cloud free-tier ARM** works if you can get capacity; the Docker image builds for arm64.
- **Your own server or an office machine** works too, for example behind a Cloudflare Tunnel.
- **Not suitable:** Vercel, Netlify, Cloudflare Pages/Workers, shared hosting (execution time and CPU limits).

## Deploy with Docker (recommended)

```bash
# 1. On a fresh Ubuntu VPS
curl -fsSL https://get.docker.com | sh

# 2. Get the code
git clone https://github.com/<you>/reelwright.git && cd reelwright

# 3. Configure
cp .env.example .env        # edit values
echo "DOMAIN=clips.example.com" >> .env   # DNS A record must point at the server first

# 4. Start with automatic HTTPS
docker compose --profile https up -d --build
```

Open `https://clips.example.com`, create the first account (it becomes the instance admin), then set
`REELWRIGHT_REGISTRATION_OPEN=false` if the instance is private and run `docker compose up -d` again.

Without a domain, run `docker compose up -d` and open `http://<server-ip>:8000` through an SSH tunnel
(`ssh -L 8000:127.0.0.1:8000 user@server`), since the port is bound to localhost on purpose.

### Updating and backups
```bash
git pull && docker compose --profile https up -d --build
docker run --rm -v reelwright_reelwright-data:/data -v $PWD:/backup busybox tar czf /backup/reelwright-data.tgz /data
```
Free disk space periodically: `docker compose exec app reelwright cleanup --days 30`.

## Run without Docker
```bash
sudo apt install ffmpeg fonts-dejavu-core python3-venv
python3 -m venv .venv && . .venv/bin/activate
pip install ".[download,whisper,mcp]"
reelwright serve --host 127.0.0.1 --port 8000
```
Put Caddy or nginx in front for HTTPS, and run it with systemd so it restarts on failure.

## Scaling
Set `REELWRIGHT_EMBEDDED_WORKER=false`, use Postgres (`DATABASE_URL`), mount the same storage on every machine, and run
`reelwright worker` on one or more render machines. The queue lives in the database, so no Redis is needed.

## Legal and operational notes
- Only process videos you own or have permission to use. Downloading from video sites can violate their terms.
- Local Whisper downloads model weights from Hugging Face on first use (needs outbound HTTPS, a few hundred MB).
- If you offer this as a public service you are responsible for the content users upload, privacy notices and takedowns.
