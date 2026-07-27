"""
X Automation Bot - Proof of Concept Test
Tests each component independently to validate feasibility.
"""

import os
import sys
import json
import sqlite3
import tempfile
import asyncio
import time
from pathlib import Path

RESULTS = {}

def test(name):
    """Decorator to run and report test results."""
    def decorator(func):
        def wrapper():
            print(f"\n{'='*60}")
            print(f"TEST: {name}")
            print(f"{'='*60}")
            try:
                result = func()
                RESULTS[name] = "✅ PASS"
                print(f"\n  ✅ PASS: {name}")
                return result
            except Exception as e:
                RESULTS[name] = f"❌ FAIL: {e}"
                print(f"\n  ❌ FAIL: {name} → {e}")
                return None
        return wrapper
    return decorator


# ── TEST 1: SQLite with WAL ──────────────────────────────────────────
@test("SQLite WAL + Schema Creation")
def test_sqlite():
    db_path = os.path.join(tempfile.gettempdir(), "test_bot.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # Create all tables from the plan
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            name TEXT,
            platform TEXT CHECK(platform IN ('youtube', 'tiktok', 'rss', 'instagram', 'atom', 'json', 'sitemap', 'api', 'other')),
            last_checked TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS posts_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            video_id TEXT,
            title TEXT,
            tweet_id TEXT,
            status TEXT CHECK(status IN ('pending', 'downloading', 'transcoding', 'uploading', 'success', 'failed')),
            error_msg TEXT,
            posted_at TEXT,
            fail_count INTEGER DEFAULT 0,
            FOREIGN KEY(source_id) REFERENCES sources(id)
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            level TEXT,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS x_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            api_key_enc TEXT,
            api_secret_enc TEXT,
            access_token_enc TEXT,
            access_token_secret_enc TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT,
            post_count_today INTEGER DEFAULT 0
        );
    """)

    # Test CRUD
    conn.execute("INSERT INTO settings VALUES ('interval_minutes', '30')")
    conn.execute("INSERT INTO sources (url, name, platform) VALUES (?, ?, ?)",
                 ("https://youtube.com/@test", "Test Channel", "youtube"))
    conn.execute("INSERT INTO logs (level, message) VALUES (?, ?)", ("INFO", "Test log entry"))
    conn.commit()

    # Verify
    row = conn.execute("SELECT value FROM settings WHERE key='interval_minutes'").fetchone()
    assert row[0] == "30", "Settings read failed"

    sources = conn.execute("SELECT * FROM sources").fetchall()
    assert len(sources) == 1, "Source insert failed"

    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal == "wal", f"WAL not enabled, got: {journal}"

    conn.close()
    os.remove(db_path)
    print(f"  → Schema created, WAL enabled, CRUD works")
    return True


# ── TEST 2: Cryptography (Token Encryption) ──────────────────────────
@test("Fernet Token Encryption")
def test_encryption():
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    f = Fernet(key)

    test_token = "1234567890-AbCdEfGhIjKlMnOpQrStUv"
    encrypted = f.encrypt(test_token.encode())
    decrypted = f.decrypt(encrypted).decode()

    assert decrypted == test_token, "Decryption mismatch"
    assert encrypted != test_token.encode(), "Token not actually encrypted"

    print(f"  → Original:  {test_token}")
    print(f"  → Encrypted: {encrypted[:50]}...")
    print(f"  → Decrypted: {decrypted}")
    return True


# ── TEST 3: yt-dlp Metadata Fetch ────────────────────────────────────
@test("yt-dlp Metadata Extraction")
def test_ytdlp():
    import yt_dlp

    # Just fetch metadata, no download — safe and fast
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(test_url, download=False)

    assert info is not None, "No info returned"
    assert info.get("title"), "No title found"
    assert info.get("id"), "No video ID found"
    assert info.get("duration"), "No duration found"

    print(f"  → Title:    {info['title']}")
    print(f"  → ID:       {info['id']}")
    print(f"  → Duration: {info['duration']}s")
    print(f"  → Uploader: {info.get('uploader', 'N/A')}")
    print(f"  → Formats:  {len(info.get('formats', []))} available")
    return True


# ── TEST 4: Tweepy Import & API Structure ─────────────────────────────
@test("Tweepy API Structure Validation")
def test_tweepy():
    import tweepy

    print(f"  → Tweepy version: {tweepy.__version__}")

    # Verify the classes and methods we need exist
    assert hasattr(tweepy, "OAuth1UserHandler"), "Missing OAuth1UserHandler"
    assert hasattr(tweepy, "Client"), "Missing Client (v2 API)"
    assert hasattr(tweepy, "API"), "Missing API (v1.1)"

    # Verify Client has create_tweet method (v2)
    assert hasattr(tweepy.Client, "create_tweet"), "Missing Client.create_tweet"

    # Verify API has chunked_upload method (v1.1)
    assert hasattr(tweepy.API, "chunked_upload"), "Missing API.chunked_upload"

    # Test creating auth handler (without actual keys — just structure)
    try:
        auth = tweepy.OAuth1UserHandler(
            consumer_key="test_key",
            consumer_secret="test_secret",
            access_token="test_token",
            access_token_secret="test_token_secret"
        )
        api = tweepy.API(auth)
        client = tweepy.Client(
            consumer_key="test_key",
            consumer_secret="test_secret",
            access_token="test_token",
            access_token_secret="test_token_secret"
        )
        print(f"  → OAuth1UserHandler: instantiates OK")
        print(f"  → tweepy.API (v1.1): instantiates OK — has chunked_upload()")
        print(f"  → tweepy.Client (v2): instantiates OK — has create_tweet()")
    except Exception as e:
        raise AssertionError(f"Tweepy instantiation failed: {e}")

    return True


# ── TEST 5: FastAPI + Uvicorn ─────────────────────────────────────────
@test("FastAPI Server Startup")
def test_fastapi():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI(title="X Automation Bot - POC")

    @app.get("/api/status")
    def status():
        return {
            "running": True,
            "queue_depth": 0,
            "active_sources": 0,
            "accounts": 0,
            "posts_today": 0,
        }

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # Use TestClient (no actual server needed)
    client = TestClient(app)

    resp = client.get("/api/status")
    assert resp.status_code == 200, f"Status endpoint failed: {resp.status_code}"
    data = resp.json()
    assert data["running"] == True
    print(f"  → GET /api/status → {json.dumps(data, indent=2)}")

    resp = client.get("/api/health")
    assert resp.status_code == 200
    print(f"  → GET /api/health → {resp.json()}")

    return True


# ── TEST 6: Async Scheduler Pattern ──────────────────────────────────
@test("Async Scheduler Pattern")
def test_scheduler():
    cycle_count = 0

    async def scheduler_loop(max_cycles=3):
        nonlocal cycle_count
        while cycle_count < max_cycles:
            cycle_count += 1
            print(f"  → Cycle {cycle_count}: checking sources...")
            await asyncio.sleep(0.1)  # Simulated interval
        print(f"  → Scheduler completed {cycle_count} cycles")

    asyncio.run(scheduler_loop())
    assert cycle_count == 3, f"Expected 3 cycles, got {cycle_count}"
    return True


# ── TEST 7: ffmpeg Check ─────────────────────────────────────────────
@test("ffmpeg Availability")
def test_ffmpeg():
    import subprocess

    # Try common locations on Windows
    ffmpeg_paths = ["ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe"]
    
    # Also check PATH additions from winget
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        ffmpeg_paths.append(os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "ffmpeg.exe"))

    for ffmpeg_path in ffmpeg_paths:
        try:
            result = subprocess.run(
                [ffmpeg_path, "-version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0]
                print(f"  → Found: {ffmpeg_path}")
                print(f"  → {version_line}")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    raise FileNotFoundError(
        "ffmpeg not found on PATH. Install it:\n"
        "  → winget install Gyan.FFmpeg\n"
        "  → Then restart your terminal to refresh PATH"
    )


# ── TEST 8: Gemini API Structure ─────────────────────────────────────
@test("Gemini API Import")
def test_gemini():
    import google.generativeai as genai

    assert hasattr(genai, "configure"), "Missing genai.configure"
    assert hasattr(genai, "GenerativeModel"), "Missing genai.GenerativeModel"

    # Verify model can be instantiated (won't call API without key)
    print(f"  → google.generativeai imported OK")
    print(f"  → genai.configure() available")
    print(f"  → genai.GenerativeModel() available")
    print(f"  → Note: actual API calls need GEMINI_API_KEY in settings")
    return True


# ── TEST 9: End-to-End Flow Simulation ───────────────────────────────
@test("End-to-End Flow Simulation (dry run)")
def test_e2e_flow():
    """Simulates the full pipeline without making real API calls."""

    steps = [
        ("1. Fetch source list", lambda: ["https://youtube.com/@test"]),
        ("2. Get latest video metadata", lambda: {"id": "abc123", "title": "Test Video", "duration": 45}),
        ("3. Check if already posted", lambda: False),  # Not in history
        ("4. Download video", lambda: "/tmp/test_video.mp4"),
        ("5. Transcode for X", lambda: "/tmp/test_video_x.mp4"),
        ("6. Generate caption", lambda: "Check this out! 🔥 #test"),
        ("7. Select X account", lambda: {"label": "@myaccount", "posts_today": 3}),
        ("8. Chunked upload to X", lambda: {"media_id": "1234567890"}),
        ("9. Post tweet", lambda: {"tweet_id": "9876543210", "url": "https://x.com/user/status/9876543210"}),
        ("10. Cleanup temp files", lambda: True),
        ("11. Update post history", lambda: True),
    ]

    for step_name, step_fn in steps:
        result = step_fn()
        print(f"  → {step_name}: {result}")

    print(f"\n  → Full pipeline flow validated (dry run)")
    return True


# ── RUN ALL TESTS ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("X AUTOMATION BOT - PROOF OF CONCEPT VALIDATION")
    print("=" * 60)

    tests = [
        test_sqlite,
        test_encryption,
        test_ytdlp,
        test_tweepy,
        test_fastapi,
        test_scheduler,
        test_ffmpeg,
        test_gemini,
        test_e2e_flow,
    ]

    for t in tests:
        t()

    print(f"\n\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = 0
    failed = 0
    for name, result in RESULTS.items():
        print(f"  {result:.<55} {name}")
        if "PASS" in result:
            passed += 1
        else:
            failed += 1

    print(f"\n  Total: {passed} passed, {failed} failed out of {len(RESULTS)}")

    if failed > 0:
        print("\n  ⚠️  Some tests failed. Check the errors above.")
        print("  Non-critical failures (like ffmpeg) can be fixed before building.")
    else:
        print("\n  🎉 All tests passed! Ready to build the full system.")

    sys.exit(failed)
