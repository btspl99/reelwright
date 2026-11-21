# Third-party components

Reelwright's own code is Apache-2.0. It depends on these packages, installed separately by pip or apt.

| Component | Licence | How it is used |
|---|---|---|
| FastAPI, Starlette, Pydantic, Uvicorn | MIT / BSD-3 | Web framework |
| SQLAlchemy | MIT | Database access |
| httpx | BSD-3 | HTTP client |
| PyJWT | MIT | Session tokens |
| NumPy | BSD-3 | Numeric work |
| opencv-python-headless (4.x) | Apache-2.0 | Face detection (Haar cascades) |
| faster-whisper (optional) | MIT | Local transcription. Model weights have their own terms. |
| yt-dlp (optional) | Unlicense | Downloading from supported sites. You are responsible for respecting each site's terms. |
| mcp (optional) | MIT | MCP server |
| **FFmpeg** (system binary) | LGPL/GPL depending on build | Run as a separate process only. Not linked or bundled in the Python package. The Docker image installs Debian's build, which includes GPL components (libx264). If you redistribute that image, follow FFmpeg's and x264's licence terms. |
| DejaVu / Noto fonts (Docker image) | Bitstream Vera / OFL | Caption rendering |

Check `pip-licenses` for the exact versions you install.
