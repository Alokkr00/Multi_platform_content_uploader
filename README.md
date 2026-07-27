# 🚀 Multi-Platform Content Distribution Engine

> A high-throughput, multi-channel **Content Distribution Engine** built with Python, FastAPI, and FFmpeg. Designed for low-latency fan-out of videos, photos, text, and web links across social platforms (X/Twitter, Instagram Reels) with human-in-the-loop approval, automated anti-ban protections, and real-time account health monitoring.

---

## ✨ System Features

* **Multi-Format Ingestion:** Direct drag-and-drop file uploads (`.mp4`, `.mov`, `.png`, `.jpg`, `.webp`), standalone text announcements, or web link imports.
* **Human-in-the-Loop Approval Queue:** State-driven publishing workflow (`draft` → `pending_approval` → `approved` → `published`). Posts remain safely queued until explicitly approved.
* **Automated FFmpeg Video Pipeline:** Intelligent aspect-ratio adaptation (9:16 vertical background-blur overlay, letterboxing, center crop) and dynamic duration trimming for Instagram Reels and X.
* **Platform-Aware AI Captions:** Generates destination-tuned captions using Gemini AI (`gemini-2.0-flash`), enforcing platform-specific character limits, story formatting, and SEO hashtags.
* **Content Calendar Scheduling:** ISO 8601 UTC timestamp targeting (`scheduled_at`). The scheduler holds approved posts until their scheduled time arrives.
* **Account Health & Anti-Ban Controls:** Session cookie persistence (`instagrapi`), timing jitter (±15% variance), daily rate-limit staggering, and real-time account status monitoring (Healthy / Warning / Challenged).
* **Observability & Alerts:** Real-time glassmorphic dashboard with live status polling, interactive terminal log viewer, and lightweight background alerts to Telegram & Webhooks.
* **One-Click Backup:** Instantly export `bot.db` and configuration into a downloadable `.zip` archive.

---

## 🛠️ Tech Stack & Architecture

* **Backend:** Python 3.12, FastAPI, Uvicorn, Asyncio
* **Database:** SQLite (WAL mode) with Fernet symmetric credential encryption
* **Media Processing:** `yt-dlp`, `ffmpeg` subprocess wrappers
* **AI Integration:** Google Gemini AI API (`google-generativeai`)
* **Publishers:** Tweepy (X API v1.1/v2), Twikit, Instagrapi
* **Frontend UI:** Vanilla JavaScript (ES6+), HTML5, Vanilla CSS (Glassmorphism design system)
* **Deployment:** Docker & Docker Compose

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
* Python 3.11+
* FFmpeg (installed and added to PATH)

### 1. Clone & Setup
```bash
git clone https://github.com/YOUR_USERNAME/content-distribution-engine.git
cd content-distribution-engine

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in your configuration:
```bash
cp .env.example .env
```

### 3. Run the Application
```bash
python server.py
```
Open **`http://localhost:8000`** (or configured port) in your browser to access the Web Dashboard!

---

## 🐳 Docker Setup

```bash
docker-compose up -d --build
```

---

## 📋 API Endpoints Reference

| Endpoint | Method | Description |
|:---|:---:|:---|
| `/api/status` | `GET` | System health, active tasks, and queue depth |
| `/api/upload` | `POST` | Drag-and-drop media upload (`.mp4`, `.png`, etc.) |
| `/api/ingest` | `POST` | Ingest multi-format draft to Approval Queue |
| `/api/approval-queue` | `GET` | List draft posts pending human approval |
| `/api/posts/{id}/approve` | `POST` | Approve draft for scheduled execution |
| `/api/posts/{id}/retry` | `POST` | Reset failed post for immediate retry |
| `/api/accounts/health` | `GET` | Real-time health signals & session status |
| `/api/system/backup` | `GET` | Download timestamped `.zip` backup package |

---

## 📜 License
Distributed under the MIT License. See `LICENSE` for more information.
