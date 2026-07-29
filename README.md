# Content Uploader Bot

A personal-use **social media posting assistant** built with Python, FastAPI, and FFmpeg. Queues videos, photos, and text for posting to X (Twitter) and Instagram, with a human approval step before anything goes live.

> **This is a localhost tool for personal use.** It is not a production SaaS product. The security model (shared Bearer token, no session expiry) is appropriate for a single user on a trusted machine, not for multi-tenant or internet-exposed deployment.

---

## ⚠️ Risk Disclaimer

This project uses **unofficial private API libraries** (`instagrapi` for Instagram, `twikit` for X cookie mode) alongside official APIs. These carry real risks:

- **Account bans**: Meta and X actively detect and restrict automated private API usage. Session cookies expire, challenges appear, and accounts get soft-banned.
- **No guarantees**: `instagrapi` and `twikit` are community-maintained reverse-engineering projects. They break when platforms update their APIs.
- **Never use your primary personal accounts.** Use dedicated secondary accounts with proxies and conservative rate limits.

The code includes session caching, request jitter, and proxy support to reduce detection surface, but **none of these eliminate the fundamental risk**.

---

## Platform Support — What Actually Works

| Platform | What Works | What Doesn't |
|:---|:---|:---|
| **X (Twitter)** | ✅ Official API v1.1/v2 via Tweepy (chunked video upload, tweets, reply threads, photo posts). ✅ Cookie/session mode via Twikit as fallback. | Cookie mode carries ban risk. |
| **YouTube (Shorts)** | ✅ Official OAuth 2.0 API via YouTube Data API v3 (resumable video upload, auto `#Shorts` tagging, 9:16 60s transcode, analytics). | Unverified API projects upload in `private` status unless the app is verified or target channels are added as GCP test users. |
| **Instagram** | ⚠️ Cookie mode only via `instagrapi` (Reels, photos, analytics). Works but fragile. | ❌ Graph API path is stubbed — it requires videos hosted on a public URL + Facebook App Review. Not implemented. |
| **TikTok** | ❌ Pure stub. All production methods raise `NotImplementedError`. | Architecture is scaffolded but nothing works. Requires TikTok developer app approval. |

**Honest summary**: Supports X (official + cookie), YouTube Shorts (official OAuth), and experimental Instagram. TikTok is placeholder code.

---

## Features

- **Multi-format ingestion**: Upload videos (`.mp4`, `.mov`), images (`.png`, `.jpg`, `.webp`), or paste URLs for yt-dlp to download.
- **Human approval queue**: Posts go through `draft → pending_approval → approved → published`. Nothing posts without explicit approval.
- **FFmpeg video pipeline**: Auto-transcode to platform specs (H.264, AAC, resolution/duration limits, vertical padding).
- **AI captions**: Optional Gemini AI integration for platform-tuned captions with hashtags.
- **Content calendar**: Schedule posts with `scheduled_at` timestamps. The scheduler holds approved posts until their target time.
- **Bearer token auth**: All `/api/*` endpoints require `Authorization: Bearer <DASHBOARD_SECRET>`. Auto-generated on first startup.
- **Safe backups**: Export `bot.db` + non-sensitive settings. `.env` is never included.
- **Upload hardening**: 50MB size limit, UUID filenames, magic-byte validation, SSRF protection on URL inputs.

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Backend | Python 3.12, FastAPI, Uvicorn, asyncio |
| Database | SQLite (WAL mode), Fernet encryption for stored credentials |
| Media | yt-dlp, FFmpeg (subprocess) |
| AI | Google Gemini API (optional) |
| Publishers | Tweepy (X official), Twikit (X cookie), instagrapi (Instagram cookie) |
| Frontend | Vanilla JS, HTML5, CSS (glassmorphism dark theme) |
| Deployment | Docker + Docker Compose |

---

## Quickstart

### Prerequisites
- Python 3.12+
- FFmpeg on PATH

### Setup
```bash
git clone https://github.com/Alokkr00/Multi_platform_content_uploader.git
cd Multi_platform_content_uploader

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
python server.py
```

Open `http://localhost:8000` and enter the `DASHBOARD_SECRET` token printed to console (also saved in `.env`).

### Docker
```bash
docker-compose up -d --build
```

---

## Security Model

**Designed for**: Single user on localhost or a trusted LAN.

**Not designed for**: Internet exposure, multi-user access, or zero-trust environments.

| What's protected | How |
|:---|:---|
| All `/api/*` endpoints | Bearer token auth (`DASHBOARD_SECRET`) |
| File uploads | 50MB cap, UUID filenames, magic-byte validation |
| URL inputs | SSRF protection (blocks private IPs, loopback, metadata endpoints) |
| Stored credentials | Fernet symmetric encryption at rest |
| Backups | `.env` excluded; only `bot.db` + non-sensitive settings |

| Known limitations |
|:---|
| Token is a long-lived shared secret with no expiry or rotation |
| No rate limiting on auth attempts |
| No CSRF protection (relies on same-origin policy + Bearer header) |
| Static `/uploads` directory is unauthenticated (media files accessible by direct URL) |
| Dashboard XSS would expose the token stored in `localStorage` |
| `server.py` is a ~750-line monolith (functional but not modular) |

---

## API Endpoints

All require `Authorization: Bearer <DASHBOARD_SECRET>`.

| Endpoint | Method | Description |
|:---|:---:|:---|
| `/api/status` | GET | Scheduler state, queue depth, stats |
| `/api/upload` | POST | Upload media (50MB limit, magic-byte validated) |
| `/api/ingest` | POST | Queue content for approval (SSRF protected) |
| `/api/approval-queue` | GET | List posts pending approval |
| `/api/posts/{id}/approve` | POST | Approve for publishing |
| `/api/posts/{id}/retry` | POST | Retry a failed post |
| `/api/quick-post` | POST | Immediate download → transcode → publish pipeline |
| `/api/accounts/health` | GET | Account session health signals |
| `/api/system/backup` | GET | Download `.zip` backup (no secrets) |

---

## Known Issues & Honest Gaps

1. **"Multi-platform" is aspirational.** X works well. Instagram is experimental. TikTok is a stub.
2. **Unofficial API clients are the biggest risk.** `instagrapi` and `twikit` work until they don't. Platform updates can break them overnight.
3. **Auth is basic.** Single shared secret, no expiry, no MFA. Adequate for localhost, not for production exposure.
4. **Code quality is "fast personal project."** Monolithic server, broad exception handlers, happy-path tests. It works, but it's not enterprise-grade.
5. **Static uploads are public.** Files in `/uploads` are served without auth. Media URLs are unguessable (UUID filenames) but not access-controlled.

---

## License

MIT License. See [LICENSE](./LICENSE).
