"""
test_integration.py - Integration and E2E validation script.

Initializes an isolated temporary database, populates mock accounts and sources,
mocks external API and video downloading calls, and triggers a full scheduler loop
to verify status state transitions and publisher mechanics.
"""

import os
import sys
import tempfile
import asyncio
import sqlite3
import shutil
import unittest.mock as mock
from cryptography.fernet import Fernet

# Setup isolated temporary DB
temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db_path = temp_db.name
temp_db.close()

# Force environment variables (Task 2.8: secure dynamic key generation)
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["MOCK_POSTING"] = "true"

print(f"Isolated DB initialized at: {temp_db_path}")

# Patch db.DB_PATH before importing db module contents
import db
db.DB_PATH = temp_db_path

# Setup legacy table to verify automatic migration on init_db
conn = sqlite3.connect(temp_db_path)
conn.execute("""
    CREATE TABLE x_accounts (
        label TEXT PRIMARY KEY,
        auth_mode TEXT,
        api_key_enc TEXT,
        api_secret_enc TEXT,
        access_token_enc TEXT,
        access_token_secret_enc TEXT,
        cookie_auth_token_enc TEXT,
        cookie_ct0_enc TEXT,
        added_at TEXT,
        last_used_at TEXT,
        post_count_today INTEGER DEFAULT 0,
        daily_reset_date TEXT
    );
""")
# Use db's key (since key variable is loaded on module import)
legacy_api_key_enc = db.encrypt_value("legacy_key_val")
legacy_api_secret_enc = db.encrypt_value("legacy_secret_val")
legacy_access_token_enc = db.encrypt_value("legacy_token_val")
legacy_access_token_secret_enc = db.encrypt_value("legacy_access_secret_val")
from datetime import datetime, timezone
today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
conn.execute(
    "INSERT INTO x_accounts VALUES (?, 'api', ?, ?, ?, ?, NULL, NULL, '2026-06-20', '2026-06-20', 5, ?)",
    ("@legacy_x_bot", legacy_api_key_enc, legacy_api_secret_enc, legacy_access_token_enc, legacy_access_token_secret_enc, today_str)
)
conn.commit()
conn.close()

db.init_db()

# Now import server (to set up DB logging handler), scheduler, and publisher
import server
import scheduler
from scheduler import scheduler as bot_scheduler
import publisher
import downloader

# Create a mock video file for yt-dlp / ffmpeg bypass tests
mock_media_dir = os.path.join(tempfile.gettempdir(), "mock_media")
os.makedirs(mock_media_dir, exist_ok=True)
mock_video_file = os.path.join(mock_media_dir, "test_video.mp4")
with open(mock_video_file, "wb") as f:
    f.write(b"MOCK VIDEO CONTENT")

# Mock functions
def mock_fetch_latest_video(channel_url):
    print(f"  [MOCK] fetch_latest_video called for {channel_url}")
    return {
        "video_id": "vid_xyz_123",
        "title": "Exciting Integration Test Video!",
        "description": "This is a video description for verification.",
        "duration": 60,
        "upload_date": "20260621",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "url": "https://youtube.com/watch?v=vid_xyz_123"
    }

def mock_download_video(url, output_dir=None):
    print(f"  [MOCK] download_video called for {url}")
    return mock_video_file

async def mock_transcode_for_platform(input_path, platform):
    print(f"  [MOCK] transcode_for_platform called for {input_path} on {platform}")
    return mock_video_file

def mock_cleanup(filepath):
    print(f"  [MOCK] cleanup called for {filepath}")
    pass

def mock_generate_caption(title, description="", template=None):
    print(f"  [MOCK] generate_caption called for: {title}")
    return f"AI Generated: {title} #automation #test"

# Main test runner
# Main test runner
async def run_integration_test():
    print("\n--- Starting Integration Test Suite ---")

    # 0. Verify migration of legacy account
    migrated_acct = db.get_account("@legacy_x_bot", "x")
    assert migrated_acct is not None, "Legacy account was not migrated"
    assert migrated_acct["auth_mode"] == "api"
    assert migrated_acct["api_key"] == "legacy_key_val"
    assert migrated_acct["api_secret"] == "legacy_secret_val"
    assert migrated_acct["access_token"] == "legacy_token_val"
    assert migrated_acct["access_token_secret"] == "legacy_access_secret_val"
    assert migrated_acct["post_count_today"] == 5
    
    # Confirm legacy x_accounts table is dropped
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='x_accounts'")
    assert cursor.fetchone() is None, "Legacy table x_accounts was not dropped"
    conn.close()
    print("[PASS] Database migration verified successfully")

    # 1. Setup default settings
    db.save_all_settings({
        "interval_minutes": "10",
        "use_ai_captions": "true",
        "caption_template": "Default template",
        "mock_posting": "true"
    })
    print("[PASS] DB settings initialized")

    # 2. Add content sources targeting all three platforms
    src1_id = db.add_source("https://youtube.com/@channel1", "Channel One", "youtube", "x,instagram,tiktok")
    print(f"[PASS] DB source added targeting X, Instagram, TikTok (ID: {src1_id})")

    # 3. Add accounts (X, Instagram, TikTok)
    acct1_id = db.add_account(
        label="test_api_account",
        platform="x",
        auth_mode="api",
        credentials={
            "api_key": "api_key_val",
            "api_secret": "api_secret_val",
            "access_token": "access_token_val",
            "access_token_secret": "access_secret_val"
        }
    )
    acct2_id = db.add_account(
        label="test_instagram_account",
        platform="instagram",
        auth_mode="cookie",
        credentials={
            "username": "instabot",
            "password": "instapassword"
        }
    )
    acct3_id = db.add_account(
        label="test_tiktok_account",
        platform="tiktok",
        auth_mode="api",
        credentials={
            "access_token": "tiktok_token_val",
            "open_id": "tiktok_open_id_val"
        }
    )
    print(f"[PASS] DB accounts added for X, Instagram, TikTok")

    # Retrieve accounts to verify decryption works
    accounts = db.get_accounts(decrypt=True)
    assert len(accounts) == 4, f"Expected 4 accounts, got {len(accounts)}"
    
    # Check X account details
    x_acct = next(a for a in accounts if a["label"] == "test_api_account" and a["platform"] == "x")
    assert x_acct["auth_mode"] == "api"
    assert x_acct["api_key"] == "api_key_val"
    
    # Check Instagram account details
    ig_acct = next(a for a in accounts if a["label"] == "test_instagram_account" and a["platform"] == "instagram")
    assert ig_acct["auth_mode"] == "cookie"
    assert ig_acct["username"] == "instabot"
    
    # Check TikTok account details
    tt_acct = next(a for a in accounts if a["label"] == "test_tiktok_account" and a["platform"] == "tiktok")
    assert tt_acct["auth_mode"] == "api"
    assert tt_acct["access_token"] == "tiktok_token_val"
    
    print("[PASS] DB accounts retrieval and decryption verified successfully")

    # Test Stuck State Reset
    stuck_id = db.add_post(video_id="stuck_video_xyz", title="Stuck Video Test", source_id=src1_id, platform="x")
    db.update_post_status(stuck_id, "downloading")
    
    db.reset_stuck_post_states()
    
    posts_after_reset = db.get_recent_posts(limit=10)
    reset_post = next(p for p in posts_after_reset if p["id"] == stuck_id)
    assert reset_post["status"] == "pending", f"Expected stuck post to reset to 'pending', got {reset_post['status']}"
    assert reset_post["fail_count"] == 1
    print("[PASS] Stuck post state recovery reset logic verified successfully")

    # 4. Patch operations inside scheduler module
    with mock.patch("scheduler.fetch_latest_video", mock_fetch_latest_video), \
         mock.patch("scheduler.download_video", mock_download_video), \
         mock.patch("scheduler.transcode_for_platform", mock_transcode_for_platform), \
         mock.patch("scheduler.cleanup", mock_cleanup), \
         mock.patch("scheduler.generate_caption", mock_generate_caption):
         
        print("\n--- Running Scheduler Cycle ---")
        
        # Check initial stats
        stats_before = db.get_stats()
        print(f"Stats before cycle: {stats_before}")
        
        # Manually enable scheduler running flag for cycle processing
        bot_scheduler.running = True
        try:
            # Trigger one run cycle
            await bot_scheduler.run_cycle()
        finally:
            bot_scheduler.running = False
        
        print("--- Scheduler Cycle Finished ---\n")

    # 5. Verify Results in DB for all three platforms
    posts = db.get_recent_posts(limit=10)
    assert len(posts) >= 3, "Expected at least 3 posts in history after cycle execution"
    
    # Verify X post
    px = next(p for p in posts if p["video_id"] == "vid_xyz_123" and p["platform"] == "x")
    assert px["status"] == "success"
    assert px["account_label"] == "test_api_account"
    
    # Verify Instagram post
    pig = next(p for p in posts if p["video_id"] == "vid_xyz_123" and p["platform"] == "instagram")
    assert pig["status"] == "success"
    assert pig["account_label"] == "test_instagram_account"
    
    # Verify TikTok post
    ptt = next(p for p in posts if p["video_id"] == "vid_xyz_123" and p["platform"] == "tiktok")
    assert ptt["status"] == "success"
    assert ptt["account_label"] == "test_tiktok_account"
    
    print("[PASS] E2E scheduler publishing succeeded on X, Instagram, and TikTok")

    # Verify log entries exist in DB
    logs = db.get_logs(limit=20)
    assert len(logs) > 0, "No log entries found in DB"
    print(f"[PASS] Logs generated: {len(logs)} entries found")
    
    # Verify post counts incremented
    accts_after = db.get_accounts()
    
    ax = next(a for a in accts_after if a["label"] == "test_api_account" and a["platform"] == "x")
    assert ax["post_count_today"] == 2
    
    aig = next(a for a in accts_after if a["label"] == "test_instagram_account" and a["platform"] == "instagram")
    assert aig["post_count_today"] == 1
    
    att = next(a for a in accts_after if a["label"] == "test_tiktok_account" and a["platform"] == "tiktok")
    assert att["post_count_today"] == 1
    
    print("[PASS] Accounts daily post counts successfully incremented")

    # 6. Test Quick Post Manual Flow Validation for all three platforms
    print("\n--- Running Quick Post Manual Flow Validation ---")
    
    # We patch the downloader and generator methods
    with mock.patch("downloader.download_video", mock_download_video), \
         mock.patch("downloader.transcode_for_platform", mock_transcode_for_platform), \
         mock.patch("downloader.cleanup", mock_cleanup), \
         mock.patch("caption_gen.generate_caption", mock_generate_caption), \
         mock.patch("downloader.fetch_metadata", mock_fetch_latest_video):
         
        # Import and invoke quick_post from server.py programmatically
        from fastapi.testclient import TestClient
        from server import app
        
        # Disable scheduler start in test context to avoid background conflicts
        with mock.patch("scheduler.scheduler.start") as mock_sched_start:
            with TestClient(app) as client:
                # Quick Post X
                res_x = client.post("/api/quick-post", json={
                    "url": "https://youtube.com/watch?v=quick_post_x",
                    "caption": "Manual Caption Override X!",
                    "account": "test_api_account",
                    "platform": "x"
                })
                assert res_x.status_code == 200
                qp_x_id = res_x.json()["post_id"]
                
                # Quick Post Instagram
                res_ig = client.post("/api/quick-post", json={
                    "url": "https://youtube.com/watch?v=quick_post_ig",
                    "caption": "Manual Caption Override IG!",
                    "account": "test_instagram_account",
                    "platform": "instagram"
                })
                assert res_ig.status_code == 200
                qp_ig_id = res_ig.json()["post_id"]
                
                # Quick Post TikTok
                res_tt = client.post("/api/quick-post", json={
                    "url": "https://youtube.com/watch?v=quick_post_tt",
                    "caption": "Manual Caption Override TT!",
                    "account": "test_tiktok_account",
                    "platform": "tiktok"
                })
                assert res_tt.status_code == 200
                qp_tt_id = res_tt.json()["post_id"]
                
                print(f"[PASS] Quick Post endpoints triggered")
                
                # Wait for the background asyncio tasks to complete
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    recent_posts = db.get_recent_posts(limit=20)
                    qp_posts = [p for p in recent_posts if p["id"] in (qp_x_id, qp_ig_id, qp_tt_id)]
                    if len(qp_posts) == 3 and all(p["status"] in ["success", "failed"] for p in qp_posts):
                        break
                        
                # Assert the results
                final_posts = db.get_recent_posts(limit=20)
                px = next(p for p in final_posts if p["id"] == qp_x_id)
                pig = next(p for p in final_posts if p["id"] == qp_ig_id)
                ptt = next(p for p in final_posts if p["id"] == qp_tt_id)
                
                assert px["status"] == "success", f"X Quick post failed: {px.get('error_msg')}"
                assert px["account_label"] == "test_api_account"
                
                assert pig["status"] == "success", f"Instagram Quick post failed: {pig.get('error_msg')}"
                assert pig["account_label"] == "test_instagram_account"
                
                assert ptt["status"] == "success", f"TikTok Quick post failed: {ptt.get('error_msg')}"
                assert ptt["account_label"] == "test_tiktok_account"
                
                assert px["caption"] == "Manual Caption Override X!"
                assert pig["caption"] == "Manual Caption Override IG!"
                assert ptt["caption"] == "Manual Caption Override TT!"
                print("[PASS] Quick Post manual workflow executed and verified successfully for all platforms")

    print("\n[SUCCESS] Integration Test Suite completed successfully!")


if __name__ == "__main__":
    exit_code = 1
    try:
        asyncio.run(run_integration_test())
        exit_code = 0
    except Exception as e:
        import traceback
        print(f"\n[FAIL] Integration Test Suite failed: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        # Cleanup temporary files
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
            # Remove WAL logs if they exist
            for ext in ["-wal", "-shm"]:
                wal_path = temp_db_path + ext
                if os.path.exists(wal_path):
                    os.remove(wal_path)
        if os.path.exists(mock_media_dir):
            shutil.rmtree(mock_media_dir)
        print("Isolated DB and temp mock media cleaned up.")
        sys.exit(exit_code)
