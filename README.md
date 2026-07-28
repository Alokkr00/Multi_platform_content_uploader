# 🚀 Multi-Platform Content Distribution Engine

> A modular **Content Distribution Engine** built with Python 3.12, FastAPI, and FFmpeg. Supports fan-out of videos, photos, text, and web links across social platforms (X/Twitter, Instagram Reels) featuring human-in-the-loop approval, rate-limit controls, Bearer token API authentication, and account health monitoring.

---

## ⚠️ Important Risk Disclaimer

This project integrates both **Official APIs** and **Unofficial Browser/Cookie-based Clients**.
- Using unofficial private API libraries (e.g., `instagrapi`, `twikit`) carries an inherent risk of platform detection, soft-bans, or account challenges (`challenge_required`).
- Use dedicated proxy servers, account staggering, and reasonable daily posting limits to mitigate risk.
- **Never use your primary personal social media accounts with unofficial scraping tools.**

---

## ✨ System Features & Platform Matrix

| Platform | Authentication Modes | Status / Capability |
|:---|:---:|:---|
| **X (Twitter)** | Official API v1.1/v2 (Tweepy)<br>Session Cookie (Twikit) | ✅ **Full Support**: Chunked video uploads, auto-reply threads, photo posts. |
| **Instagram** | Session Cookie (`instagrapi`) | ⚠️ **Supported (Cookie Mode)**: Video Reels, Photos, and analytics. *(Graph API path is stubbed for public URL requirements).* |
| **TikTok** | Official API / Cookie | 🚧 **Stub / Developer Preview**: Architecture implemented; production methods raise `NotImplementedError`. |

### Core Infrastructure Features:
* **Multi-Format Ingestion:** Direct drag-and-drop file uploads (`.mp4`, `.mov`, `.png`, `.jpg`, `.webp`), standalone text announcements, or web link imports.
* **Human-in-the-Loop Approval Queue:** State-driven publishing workflow (`draft` → `pending_approval` → `approved` → `published`). Posts remain safely queued until explicitly approved.
* **Automated FFmpeg Video Pipeline:** Aspect-ratio adaptation (9:16 vertical background-blur overlay, letterboxing, center crop) and dynamic duration trimming.
* **Platform-Aware AI Captions:** Generates destination-tuned captions using Gemini AI (`gemini-2.0-flash`), enforcing platform-specific character limits, story formatting, and SEO hashtags.
* **Content Calendar Scheduling:** ISO 8601 UTC timestamp targeting (`scheduled_at`). The scheduler holds approved posts until their scheduled target time.
* **API Security & Auth:** Bearer token authorization on all REST endpoints (`DASHBOARD_SECRET`), SSRF protection against private IP access, file upload magic-byte verification, and 50MB size caps.
* **One-Click Safe Backup:** Instantly export `bot.db` and non-sensitive configuration to a `.zip` package (`.env` credentials are **never** included).

---

## 🛠️ Tech Stack & Architecture

* **Backend:** Python 3.12, FastAPI, Uvicorn, Asyncio
* **Database:** SQLite (WAL mode) with Fernet symmetric credential encryption
* **Media Processing:** `yt-dlp`, `ffmpeg` subprocess wrappers
* **AI Integration:** Google Gemini AI API (`google-generativeai`)
* **Publishers:** Tweepy (X API v1.1/v2), Twikit, Instagrapi
* **Frontend UI:** Vanilla JavaScript (ES6+), HTML5, Vanilla CSS (Glassmorphism design system)
* **Deployment:** Docker & Docker Compose (Python 3.12 container)

```
┌─────────────────────────────────────────────────────────────┐
│                       INPUT SOURCES                         │
│   Drag & Drop Media Upload · Text/Images · Web Links · API  │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                    HUMAN APPROVAL QUEUE                     │
│    Draft Review · Time-Targeted Calendar (`scheduled_at`)   │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                  PLATFORM ADAPTER ENGINE                    │
│   FFmpeg Transcoder · Platform Captions · Format Resizing   │
└──────────────────────────────┬──────────────────────────────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        ▼                                             ▼
┌───────────────┐                             ┌───────────────┐
│   X (Twitter) │                             │   Instagram   │
└───────┬───────┘                             └───────┬───────┘
        │                                             │
        └──────────────────────┬──────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                  HEALTH & ALERT SENTINEL                    │
│     Session Persistence · Telegram Alerts · Health UI       │
└──────────────────────────────┘
```

---

## 🚀 Quickstart Guide

### Prerequisites
* Python 3.12+
* FFmpeg (installed and added to system PATH)

### 1. Clone & Setup
```bash
git clone https://github.com/Alokkr00/Multi_platform_content_uploader.git
cd Multi_platform_content_uploader

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Upon initial launch, `server.py` will auto-generate a secure `DASHBOARD_SECRET` token in `.env` if not already set.

### 3. Run the Application
```bash
python server.py
```
Open **`http://localhost:8000`** in your browser and log in with your `DASHBOARD_SECRET` token to access the Web Dashboard!

---

## 🐳 Docker Deployment

```bash
docker-compose up -d --build
```

---

## 🔒 API Security & Endpoints Reference

All API routes require `Authorization: Bearer <DASHBOARD_SECRET>` token header.

| Endpoint | Method | Description |
|:---|:---:|:---|
| `/api/status` | `GET` | System health, active tasks, and queue depth |
| `/api/upload` | `POST` | Drag-and-drop media upload with 50MB limit & magic-byte validation |
| `/api/ingest` | `POST` | Ingest multi-format draft to Approval Queue (SSRF protected) |
| `/api/approval-queue` | `GET` | List draft posts pending human approval |
| `/api/posts/{id}/approve` | `POST` | Approve draft for scheduled execution |
| `/api/posts/{id}/retry` | `POST` | Reset failed post for immediate retry |
| `/api/accounts/health` | `GET` | Real-time health signals & session status |
| `/api/system/backup` | `GET` | Download timestamped `.zip` backup package (database & non-sensitive settings) |

---

## 📜 License
Distributed under the MIT License. See [`LICENSE`](file:///d:/Projects/x_automation/LICENSE) for more information.
