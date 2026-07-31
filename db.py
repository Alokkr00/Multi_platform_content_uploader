"""
db.py — SQLite Database Wrapper with WAL, Retry, and Encryption

Provides thread-safe, async-compatible database access for the X Automation Bot.
All credential fields are encrypted at rest using Fernet symmetric encryption.
"""

import sqlite3
import os
import time
import functools
import logging
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("clipflow.db")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
# ── Encryption Helpers ────────────────────────────────────────────────

def _get_fernet():
    """Get a Fernet instance using the configured encryption key."""
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise ValueError(
            "ENCRYPTION_KEY not set in environment or .env file. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt_value(value: str) -> str:
    """Encrypt a string value and return base64-encoded ciphertext."""
    if not value:
        return ""
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    """Decrypt a base64-encoded ciphertext and return plaintext."""
    if not encrypted:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        logger.error("Failed to decrypt value — wrong ENCRYPTION_KEY?")
        raise ValueError("Decryption failed. The configured ENCRYPTION_KEY is incorrect or invalid.") from e


# ── Retry Decorator ───────────────────────────────────────────────────

def retry_on_lock(max_retries=3, base_delay=0.1):
    """Retry a function if SQLite raises OperationalError (database locked)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"DB locked, retry {attempt + 1}/{max_retries} in {delay:.1f}s")
                        time.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator


# ── Database Connection ───────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema Initialization ────────────────────────────────────────────

@retry_on_lock()
def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                platform TEXT CHECK(platform IN ('youtube', 'tiktok', 'instagram', 'rss', 'atom', 'json', 'sitemap', 'api', 'other')) DEFAULT 'other',
                last_checked TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS posts_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                video_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                caption TEXT DEFAULT '',
                tweet_id TEXT,
                account_label TEXT,
                status TEXT CHECK(status IN ('draft', 'pending', 'processing', 'pending_approval', 'approved', 'scheduled', 'downloading', 'transcoding', 'uploading', 'success', 'failed')) DEFAULT 'pending',
                error_msg TEXT,
                posted_at TEXT,
                fail_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                level TEXT DEFAULT 'INFO',
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                platform TEXT NOT NULL CHECK(platform IN ('x', 'instagram', 'tiktok', 'youtube')),
                auth_mode TEXT NOT NULL CHECK(auth_mode IN ('api', 'cookie', 'oauth')),
                credentials_enc TEXT NOT NULL,
                added_at TEXT DEFAULT (datetime('now')),
                last_used_at TEXT,
                post_count_today INTEGER DEFAULT 0,
                daily_reset_date TEXT DEFAULT (date('now')),
                is_active INTEGER DEFAULT 1,
                UNIQUE(platform, label)
            );

            -- Index for quick history lookups
            CREATE INDEX IF NOT EXISTS idx_posts_video_id ON posts_history(video_id);
            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts_history(status);
            CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);
            CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);
        """)
        conn.commit()

        # Schema extensions for Sprint 5: add columns if they don't exist
        # Add target_platforms to sources
        try:
            conn.execute("ALTER TABLE sources ADD COLUMN target_platforms TEXT DEFAULT 'x'")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Already exists

        # Sprint 6: Migrate sources table to expand platform check constraints if needed
        try:
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'")
            row = cursor.fetchone()
            if row:
                sql = row[0]
                if "atom" not in sql:
                    logger.info("Migrating sources table to support new platform types (atom, json, sitemap, api)...")
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS sources_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            url TEXT NOT NULL UNIQUE,
                            name TEXT NOT NULL DEFAULT '',
                            platform TEXT CHECK(platform IN ('youtube', 'tiktok', 'instagram', 'rss', 'atom', 'json', 'sitemap', 'api', 'other')) DEFAULT 'other',
                            last_checked TEXT,
                            is_active INTEGER DEFAULT 1,
                            created_at TEXT DEFAULT (datetime('now')),
                            target_platforms TEXT DEFAULT 'x'
                        )
                    """)
                    conn.execute("""
                        INSERT INTO sources_new (id, url, name, platform, last_checked, is_active, created_at, target_platforms)
                        SELECT id, url, name, platform, last_checked, is_active, created_at, target_platforms FROM sources
                    """)
                    conn.execute("DROP TABLE sources")
                    conn.execute("ALTER TABLE sources_new RENAME TO sources")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.commit()
                    logger.info("sources table migrated successfully to expand platform CHECK constraint.")
        except Exception as ex:
            logger.error(f"Failed to migrate sources table check constraint: {ex}")

        # Migrate accounts table to expand platform and auth_mode check constraints if needed
        try:
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'")
            row = cursor.fetchone()
            if row:
                sql = row[0]
                if "youtube" not in sql or "oauth" not in sql:
                    logger.info("Migrating accounts table to support youtube and oauth...")
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS accounts_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            label TEXT NOT NULL,
                            platform TEXT NOT NULL CHECK(platform IN ('x', 'instagram', 'tiktok', 'youtube')),
                            auth_mode TEXT NOT NULL CHECK(auth_mode IN ('api', 'cookie', 'oauth')),
                            credentials_enc TEXT NOT NULL,
                            added_at TEXT DEFAULT (datetime('now')),
                            last_used_at TEXT,
                            post_count_today INTEGER DEFAULT 0,
                            daily_reset_date TEXT DEFAULT (date('now')),
                            is_active INTEGER DEFAULT 1,
                            UNIQUE(platform, label)
                        )
                    """)
                    conn.execute("""
                        INSERT INTO accounts_new (id, label, platform, auth_mode, credentials_enc, added_at, last_used_at, post_count_today, daily_reset_date, is_active)
                        SELECT id, label, platform, auth_mode, credentials_enc, added_at, last_used_at, post_count_today, daily_reset_date, is_active FROM accounts
                    """)
                    conn.execute("DROP TABLE accounts")
                    conn.execute("ALTER TABLE accounts_new RENAME TO accounts")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.commit()
                    logger.info("accounts table migrated successfully for youtube and oauth.")
        except Exception as ex:
            logger.error(f"Failed to migrate accounts table check constraint: {ex}")


        # Add platform to posts_history
        try:
            conn.execute("ALTER TABLE posts_history ADD COLUMN platform TEXT NOT NULL DEFAULT 'x'")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Already exists

        # Add external_id to posts_history
        try:
            conn.execute("ALTER TABLE posts_history ADD COLUMN external_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Already exists

        # Migration from legacy x_accounts to unified accounts
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='x_accounts'")
        if cursor.fetchone():
            logger.info("Migrating legacy x_accounts table to unified accounts table...")
            import json
            old_rows = conn.execute("SELECT * FROM x_accounts").fetchall()
            for old in old_rows:
                label = old["label"]
                auth_mode = old["auth_mode"]
                added_at = old["added_at"]
                last_used_at = old["last_used_at"]
                post_count_today = old["post_count_today"]
                daily_reset_date = old["daily_reset_date"]

                creds = {}
                if auth_mode == "api":
                    creds["api_key"] = decrypt_value(old["api_key_enc"]) if old["api_key_enc"] else ""
                    creds["api_secret"] = decrypt_value(old["api_secret_enc"]) if old["api_secret_enc"] else ""
                    creds["access_token"] = decrypt_value(old["access_token_enc"]) if old["access_token_enc"] else ""
                    creds["access_token_secret"] = decrypt_value(old["access_token_secret_enc"]) if old["access_token_secret_enc"] else ""
                elif auth_mode == "cookie":
                    creds["cookie_auth_token"] = decrypt_value(old["cookie_auth_token_enc"]) if old["cookie_auth_token_enc"] else ""
                    creds["cookie_ct0"] = decrypt_value(old["cookie_ct0_enc"]) if old["cookie_ct0_enc"] else ""

                credentials_enc = encrypt_value(json.dumps(creds))
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO accounts (
                            label, platform, auth_mode, credentials_enc, added_at, last_used_at, post_count_today, daily_reset_date
                        ) VALUES (?, 'x', ?, ?, ?, ?, ?, ?)
                        """,
                        (label, auth_mode, credentials_enc, added_at, last_used_at, post_count_today, daily_reset_date)
                    )
                except Exception as ex:
                    logger.error(f"Failed to migrate account {label}: {ex}")

            conn.commit()
            try:
                 conn.execute("DROP TABLE x_accounts")
                 conn.commit()
                 logger.info("Legacy x_accounts table dropped successfully")
            except Exception as ex:
                 logger.warning(f"Could not drop x_accounts table: {ex}")

        # Sprint 6: Add columns for analytics to posts_history if they don't exist
        for col_name in ["views", "likes", "shares", "comments"]:
            try:
                conn.execute(f"ALTER TABLE posts_history ADD COLUMN {col_name} INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Already exists
                
        try:
            conn.execute("ALTER TABLE posts_history ADD COLUMN last_analytics_updated_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Already exists

        # Sprint 6: Add proxy_url and user_agent columns to accounts if they don't exist
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN proxy_url TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Already exists

        # Migrate posts_history table to expand status check constraints if needed
        try:
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='posts_history'")
            row = cursor.fetchone()
            if row:
                sql = row[0]
                if "pending_approval" not in sql and "CHECK" in sql:
                    logger.info("Migrating posts_history table to expand status CHECK constraints...")
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS posts_history_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_id INTEGER,
                            video_id TEXT NOT NULL,
                            title TEXT DEFAULT '',
                            caption TEXT DEFAULT '',
                            tweet_id TEXT,
                            account_label TEXT,
                            status TEXT DEFAULT 'pending',
                            error_msg TEXT,
                            posted_at TEXT,
                            fail_count INTEGER DEFAULT 0,
                            created_at TEXT DEFAULT (datetime('now')),
                            platform TEXT NOT NULL DEFAULT 'x',
                            external_id TEXT,
                            views INTEGER DEFAULT 0,
                            likes INTEGER DEFAULT 0,
                            shares INTEGER DEFAULT 0,
                            comments INTEGER DEFAULT 0,
                            last_analytics_updated_at TEXT,
                            media_type TEXT DEFAULT 'video',
                            media_path TEXT,
                            requires_approval INTEGER DEFAULT 1,
                            scheduled_at TEXT,
                            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE SET NULL
                        )
                    """)
                    conn.execute("""
                        INSERT INTO posts_history_new (
                            id, source_id, video_id, title, caption, tweet_id, account_label, status, 
                            error_msg, posted_at, fail_count, created_at, platform, external_id, 
                            views, likes, shares, comments, last_analytics_updated_at
                        )
                        SELECT id, source_id, video_id, title, caption, tweet_id, account_label, status, 
                               error_msg, posted_at, fail_count, created_at, platform, external_id, 
                               views, likes, shares, comments, last_analytics_updated_at FROM posts_history
                    """)
                    conn.execute("DROP TABLE posts_history")
                    conn.execute("ALTER TABLE posts_history_new RENAME TO posts_history")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_video_id ON posts_history(video_id);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts_history(status);")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.commit()
                    logger.info("posts_history table migrated successfully for approval queue status support.")
        except Exception as ex:
            logger.error(f"Failed to migrate posts_history status constraint: {ex}")

        # Multi-Format Ingestion & Hybrid Approval Queue Migrations
        for col_name, col_type in [
            ("media_type", "TEXT DEFAULT 'video'"),
            ("media_path", "TEXT"),
            ("requires_approval", "INTEGER DEFAULT 1"),
            ("scheduled_at", "TEXT")
        ]:
            try:
                conn.execute(f"ALTER TABLE posts_history ADD COLUMN {col_name} {col_type}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Already exists

        logger.info("Database initialized successfully")
    finally:
        conn.close()


# ── Settings CRUD ─────────────────────────────────────────────────────

@retry_on_lock()
def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


@retry_on_lock()
def set_setting(key: str, value: str):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value)
        )
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def get_all_settings() -> dict:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


@retry_on_lock()
def save_all_settings(settings: dict):
    conn = get_connection()
    try:
        for key, value in settings.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, str(value), str(value))
            )
        conn.commit()
    finally:
        conn.close()


# ── Sources CRUD ──────────────────────────────────────────────────────

@retry_on_lock()
def add_source(url: str, name: str, platform: str = "other", target_platforms: str = "x") -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sources (url, name, platform, target_platforms) VALUES (?, ?, ?, ?)",
            (url, name, platform, target_platforms)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


@retry_on_lock()
def get_sources(active_only: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT * FROM sources"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id DESC"
        rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

get_all_sources = get_sources


@retry_on_lock()
def get_source(source_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@retry_on_lock()
def delete_source(source_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def toggle_source(source_id: int, active: bool):
    conn = get_connection()
    try:
        conn.execute("UPDATE sources SET is_active = ? WHERE id = ?", (1 if active else 0, source_id))
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def update_source_checked(source_id: int):
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE sources SET last_checked = ? WHERE id = ?", (now, source_id))
        conn.commit()
    finally:
        conn.close()


# ── Unified Accounts CRUD ─────────────────────────────────────────────

import json

@retry_on_lock()
def add_account(label: str, platform: str, auth_mode: str, credentials: dict, proxy_url: str = None, user_agent: str = None) -> int:
    conn = get_connection()
    try:
        creds_json = json.dumps(credentials)
        credentials_enc = encrypt_value(creds_json)
        cursor = conn.execute(
            """
            INSERT INTO accounts (label, platform, auth_mode, credentials_enc, proxy_url, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (label, platform, auth_mode, credentials_enc, proxy_url, user_agent)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


DAILY_LIMITS = {
    "x": 50,
    "instagram": 10,
    "tiktok": 20,
    "youtube": 8
}

def get_daily_limit(platform: str) -> int:
    """Get daily post limit for a platform, with configurable setting override."""
    default = DAILY_LIMITS.get(platform.lower(), 20)
    custom = get_setting(f"daily_limit_{platform.lower()}", "")
    if custom.isdigit():
        return int(custom)
    return default


@retry_on_lock()
def get_accounts(platform: str = None, decrypt: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        if platform:
            rows = conn.execute("SELECT * FROM accounts WHERE platform = ? ORDER BY id", (platform,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        
        accounts = []
        for row in rows:
            account = dict(row)
            # Reset daily counter if date changed
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if account.get("daily_reset_date") != today:
                conn.execute(
                    "UPDATE accounts SET post_count_today = 0, daily_reset_date = ? WHERE id = ?",
                    (today, account["id"])
                )
                conn.commit()
                account["post_count_today"] = 0

            if decrypt:
                creds_json = decrypt_value(account["credentials_enc"])
                try:
                    creds = json.loads(creds_json) if creds_json else {}
                except Exception:
                    creds = {}
                account.update(creds)

            # Always mask or remove credentials_enc in output
            account.pop("credentials_enc", None)
            accounts.append(account)
        return accounts
    finally:
        conn.close()


@retry_on_lock()
def get_account(label: str, platform: str = None) -> dict | None:
    """Get a single account with decrypted credentials."""
    conn = get_connection()
    try:
        if platform:
            row = conn.execute("SELECT * FROM accounts WHERE label = ? AND platform = ?", (label, platform)).fetchone()
        else:
            row = conn.execute("SELECT * FROM accounts WHERE label = ?", (label,)).fetchone()
        if not row:
            return None
        account = dict(row)

        # Reset daily counter if needed
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if account.get("daily_reset_date") != today:
            conn.execute(
                "UPDATE accounts SET post_count_today = 0, daily_reset_date = ? WHERE id = ?",
                (today, account["id"])
            )
            conn.commit()
            account["post_count_today"] = 0

        creds_json = decrypt_value(account["credentials_enc"])
        try:
            creds = json.loads(creds_json) if creds_json else {}
        except Exception:
            creds = {}
        account.update(creds)
        account.pop("credentials_enc", None)
        return account
    finally:
        conn.close()


@retry_on_lock()
def get_least_used_account(platform: str) -> dict | None:
    """Get the account with the fewest posts today."""
    conn = get_connection()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Reset all counters for accounts with stale dates
        conn.execute(
            "UPDATE accounts SET post_count_today = 0, daily_reset_date = ? WHERE daily_reset_date != ?",
            (today, today)
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM accounts WHERE platform = ? AND is_active = 1 ORDER BY post_count_today ASC LIMIT 1",
            (platform,)
        ).fetchone()
        if not row:
            return None
        account = dict(row)
        
        creds_json = decrypt_value(account["credentials_enc"])
        try:
            creds = json.loads(creds_json) if creds_json else {}
        except Exception:
            creds = {}
        account.update(creds)
        account.pop("credentials_enc", None)
        return account
    finally:
        conn.close()


@retry_on_lock()
def increment_post_count(label: str, platform: str = None):
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        if platform:
            conn.execute(
                "UPDATE accounts SET post_count_today = post_count_today + 1, last_used_at = ? WHERE label = ? AND platform = ?",
                (now, label, platform)
            )
        else:
            conn.execute(
                "UPDATE accounts SET post_count_today = post_count_today + 1, last_used_at = ? WHERE label = ?",
                (now, label)
            )
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def delete_account(label: str, platform: str = None):
    conn = get_connection()
    try:
        if platform:
            conn.execute("DELETE FROM accounts WHERE label = ? AND platform = ?", (label, platform))
        else:
            conn.execute("DELETE FROM accounts WHERE label = ?", (label,))
        conn.commit()
    finally:
        conn.close()


# ── Posts History CRUD ────────────────────────────────────────────────

@retry_on_lock()
def add_post(video_id: str, title: str = "", source_id: int | None = None, platform: str = "x", status: str = "pending") -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO posts_history (video_id, title, source_id, platform, status) VALUES (?, ?, ?, ?, ?)",
            (video_id, title, source_id, platform, status)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


@retry_on_lock()
def update_post_video_id(post_id: int, video_id: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE posts_history SET video_id = ? WHERE id = ?", (video_id, post_id))
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def update_account_credentials(label: str, platform: str, credentials_dict: dict):
    """Encrypt and update credentials settings for a specific account."""
    conn = get_connection()
    try:
        # Filter out standard DB column keys to keep credentials JSON clean
        db_columns = {
            "id", "label", "platform", "auth_mode", "added_at", 
            "last_used_at", "post_count_today", "daily_reset_date", 
            "is_active", "proxy_url", "user_agent"
        }
        filtered_creds = {k: v for k, v in credentials_dict.items() if k not in db_columns}
        
        credentials_json = json.dumps(filtered_creds)
        credentials_enc = encrypt_value(credentials_json)
        conn.execute(
            "UPDATE accounts SET credentials_enc = ? WHERE label = ? AND platform = ?",
            (credentials_enc, label, platform)
        )
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def is_video_posted(video_id: str, platform: str = "x") -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM posts_history WHERE video_id = ? AND platform = ? AND status IN ('pending', 'downloading', 'transcoding', 'uploading', 'success')",
            (video_id, platform)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


@retry_on_lock()
def update_post_status(post_id: int, status: str, tweet_id: str = None, account_label: str = None,
                       caption: str = None, error_msg: str = None, external_id: str = None):
    conn = get_connection()
    try:
        updates = ["status = ?"]
        params = [status]

        if tweet_id is not None:
            updates.append("tweet_id = ?")
            params.append(tweet_id)
            if external_id is None:
                external_id = tweet_id

        if external_id is not None:
            updates.append("external_id = ?")
            params.append(external_id)

        if account_label is not None:
            updates.append("account_label = ?")
            params.append(account_label)
        if caption is not None:
            updates.append("caption = ?")
            params.append(caption)
        if error_msg is not None:
            updates.append("error_msg = ?")
            params.append(error_msg)
        if status == "success":
            updates.append("posted_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())
        if status == "failed":
            updates.append("fail_count = fail_count + 1")

        params.append(post_id)
        conn.execute(f"UPDATE posts_history SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def reset_stuck_post_states():
    """Reset any post in stuck active states (downloading, transcoding, uploading) back to pending or failed on startup."""
    conn = get_connection()
    try:
        # Get all posts that are stuck in active states
        stuck_posts = conn.execute(
            "SELECT id, fail_count FROM posts_history WHERE status IN ('downloading', 'transcoding', 'uploading')"
        ).fetchall()
        
        for post in stuck_posts:
            post_id = post["id"]
            fail_count = post["fail_count"]
            
            if fail_count >= 2: # this attempt will be the 3rd fail
                conn.execute(
                    "UPDATE posts_history SET status = 'failed', error_msg = 'Interrupted by system shutdown (limit reached)' WHERE id = ?",
                    (post_id,)
                )
            else:
                conn.execute(
                    "UPDATE posts_history SET status = 'pending', fail_count = fail_count + 1, error_msg = 'Interrupted by system shutdown' WHERE id = ?",
                    (post_id,)
                )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to reset stuck post states: {e}")
    finally:
        conn.close()


@retry_on_lock()
def get_pending_posts(limit: int = 10) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM posts_history 
            WHERE status IN ('pending', 'approved') 
              AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
              AND fail_count < 3 
            ORDER BY id ASC LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@retry_on_lock()
def add_ingested_post(
    video_id: str,
    title: str = "",
    caption: str = "",
    media_type: str = "video",
    media_path: str = None,
    platform: str = "x",
    status: str = "pending_approval",
    requires_approval: int = 1,
    scheduled_at: str = None,
    account_label: str = None,
    source_id: int = None
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO posts_history (
                video_id, title, caption, media_type, media_path, platform, 
                status, requires_approval, scheduled_at, account_label, source_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, title, caption, media_type, media_path, platform, status, requires_approval, scheduled_at, account_label, source_id)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


@retry_on_lock()
def get_approval_queue(limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM posts_history WHERE status IN ('draft', 'pending_approval') ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@retry_on_lock()
def approve_post(post_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE posts_history SET status = 'approved', requires_approval = 0 WHERE id = ?",
            (post_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


@retry_on_lock()
def approve_all_posts() -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE posts_history SET status = 'approved', requires_approval = 0 WHERE status IN ('draft', 'pending_approval')"
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


@retry_on_lock()
def reject_post(post_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM posts_history WHERE id = ?", (post_id,))
        conn.commit()
        return True
    finally:
        conn.close()



@retry_on_lock()
def get_recent_posts(limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM posts_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@retry_on_lock()
def retry_failed_post(post_id: int) -> bool:
    """Reset a failed post back to 'approved' status with zero fail_count for immediate re-execution."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE posts_history SET status = 'approved', fail_count = 0, error_msg = NULL WHERE id = ?",
            (post_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


def cleanup_old_uploads(days: int = 7) -> int:
    """Purge published upload media files older than N days from disk."""
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
    if not os.path.exists(uploads_dir):
        return 0

    cutoff_sec = time.time() - (days * 86400)
    purged_count = 0

    conn = get_connection()
    try:
        # Find all successful post media paths
        rows = conn.execute(
            "SELECT media_path FROM posts_history WHERE status = 'success' AND media_path IS NOT NULL"
        ).fetchall()
        successful_paths = {row["media_path"] for row in rows if row["media_path"]}
    finally:
        conn.close()

    try:
        for filename in os.listdir(uploads_dir):
            file_path = os.path.join(uploads_dir, filename).replace("\\", "/")
            rel_path = f"uploads/{filename}"
            
            # If file is older than cutoff AND linked to a successful post or orphaned
            if os.path.isfile(file_path):
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff_sec or rel_path in successful_paths:
                    try:
                        os.remove(file_path)
                        purged_count += 1
                        logger.info(f"Purged old upload file: {rel_path}")
                    except Exception as pe:
                        logger.warning(f"Failed to remove upload file {rel_path}: {pe}")
    except Exception as e:
        logger.error(f"Error during upload media cleanup: {e}")

    return purged_count


@retry_on_lock()
def get_account_health_summary() -> list[dict]:
    """Evaluate health status (healthy, warning, challenged) for all configured accounts."""
    conn = get_connection()
    try:
        accounts = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        health_list = []

        for acct in accounts:
            label = acct["label"]
            platform = acct["platform"]
            post_count = acct["post_count_today"]
            is_active = acct["is_active"]
            limit = 50 if platform == "x" else (10 if platform == "instagram" else 20)

            # Check recent post failures for this account
            failed_rows = conn.execute(
                "SELECT error_msg FROM posts_history WHERE account_label = ? AND platform = ? AND status = 'failed' ORDER BY id DESC LIMIT 5",
                (label, platform)
            ).fetchall()
            
            health_status = "healthy"
            reason = "Operational"

            if not is_active:
                health_status = "warning"
                reason = "Disabled in configuration"
            elif post_count >= limit:
                health_status = "warning"
                reason = f"Daily limit reached ({post_count}/{limit})"
            elif failed_rows:
                recent_errors = " ".join([r["error_msg"] or "" for r in failed_rows]).lower()
                if "login" in recent_errors or "auth" in recent_errors or "checkpoint" in recent_errors or "challenge" in recent_errors:
                    health_status = "challenged"
                    reason = "Session expired or challenge triggered"
                elif len(failed_rows) >= 3:
                    health_status = "warning"
                    reason = f"Multiple recent failures ({len(failed_rows)})"

            health_list.append({
                "label": label,
                "platform": platform,
                "auth_mode": acct["auth_mode"],
                "post_count_today": post_count,
                "daily_limit": limit,
                "is_active": is_active,
                "health_status": health_status,
                "health_reason": reason,
                "last_used_at": acct["last_used_at"]
            })

        return health_list
    finally:
        conn.close()


# ── Logging ───────────────────────────────────────────────────────────

@retry_on_lock()
def add_log(level: str, message: str):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO logs (level, message) VALUES (?, ?)",
            (level.upper(), message)
        )
        conn.commit()
    except Exception:
        pass  # Don't let logging errors crash the app
    finally:
        conn.close()


@retry_on_lock()
def get_logs(limit: int = 100) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in reversed(rows)]  # Chronological order
    finally:
        conn.close()


# ── DB Stats ──────────────────────────────────────────────────────────

@retry_on_lock()
def get_stats() -> dict:
    conn = get_connection()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = {
            "total_sources": conn.execute("SELECT COUNT(*) FROM sources WHERE is_active = 1").fetchone()[0],
            "total_accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "pending_posts": conn.execute("SELECT COUNT(*) FROM posts_history WHERE status = 'pending'").fetchone()[0],
            "posts_today": conn.execute(
                "SELECT COUNT(*) FROM posts_history WHERE status = 'success' AND posted_at LIKE ?",
                (f"{today}%",)
            ).fetchone()[0],
            "total_posts": conn.execute("SELECT COUNT(*) FROM posts_history WHERE status = 'success'").fetchone()[0],
            "failed_posts": conn.execute("SELECT COUNT(*) FROM posts_history WHERE status = 'failed'").fetchone()[0],
        }
        return stats
    finally:
        conn.close()


@retry_on_lock()
def update_post_analytics(post_id: int, views: int, likes: int, shares: int, comments: int):
    conn = get_connection()
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE posts_history
            SET views = ?, likes = ?, shares = ?, comments = ?, last_analytics_updated_at = ?
            WHERE id = ?
            """,
            (views, likes, shares, comments, now_str, post_id)
        )
        conn.commit()
    finally:
        conn.close()


@retry_on_lock()
def get_posts_for_analytics_sync(max_age_days: int = 7) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM posts_history 
            WHERE status = 'success' 
              AND posted_at IS NOT NULL 
            ORDER BY id DESC
            """
        ).fetchall()
        
        valid_posts = []
        now = datetime.now(timezone.utc)
        for row in rows:
            posted_at_str = row["posted_at"]
            try:
                # Replace 'Z' with UTC offset
                dt_str = posted_at_str.replace("Z", "+00:00")
                posted_dt = datetime.fromisoformat(dt_str)
                age = now - posted_dt
                if age.days <= max_age_days:
                    valid_posts.append(dict(row))
            except Exception:
                valid_posts.append(dict(row))
                
        return valid_posts
    finally:
        conn.close()


# Initialize on import
if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
