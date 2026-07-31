"""
migrate_db.py — One-time utility to migrate data from SQLite (bot.db) to PostgreSQL.
"""

import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bot_user:bot_password_9988@localhost:5432/clipflow_db")

def get_postgres_connection():
    return psycopg2.connect(DATABASE_URL)

def get_sqlite_connection():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def create_postgres_tables(pg_conn):
    with pg_conn.cursor() as cur:
        # Create settings table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key VARCHAR(255) PRIMARY KEY,
                value TEXT
            );
        """)

        # Create sources table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL DEFAULT '',
                platform VARCHAR(50) DEFAULT 'other',
                last_checked VARCHAR(50),
                is_active INTEGER DEFAULT 1,
                created_at VARCHAR(50) DEFAULT CURRENT_TIMESTAMP::text,
                target_platforms VARCHAR(255) DEFAULT 'x'
            );
        """)

        # Create posts_history table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts_history (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
                video_id VARCHAR(255) NOT NULL,
                title TEXT DEFAULT '',
                caption TEXT DEFAULT '',
                tweet_id VARCHAR(255),
                account_label VARCHAR(255),
                status VARCHAR(50) DEFAULT 'pending',
                error_msg TEXT,
                posted_at VARCHAR(50),
                fail_count INTEGER DEFAULT 0,
                created_at VARCHAR(50) DEFAULT CURRENT_TIMESTAMP::text,
                platform VARCHAR(50) NOT NULL DEFAULT 'x',
                external_id TEXT,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                last_analytics_updated_at VARCHAR(50)
            );
        """)

        # Create logs table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                timestamp VARCHAR(50) DEFAULT CURRENT_TIMESTAMP::text,
                level VARCHAR(50) DEFAULT 'INFO',
                message TEXT
            );
        """)

        # Create accounts table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                label VARCHAR(255) NOT NULL,
                platform VARCHAR(50) NOT NULL,
                auth_mode VARCHAR(50) NOT NULL,
                credentials_enc TEXT NOT NULL,
                added_at VARCHAR(50) DEFAULT CURRENT_TIMESTAMP::text,
                last_used_at VARCHAR(50),
                post_count_today INTEGER DEFAULT 0,
                daily_reset_date VARCHAR(50) DEFAULT CURRENT_DATE::text,
                is_active INTEGER DEFAULT 1,
                proxy_url TEXT,
                user_agent TEXT,
                UNIQUE(platform, label)
            );
        """)

        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_video_id ON posts_history(video_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts_history(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);")
        
        pg_conn.commit()

def migrate_table(sqlite_conn, pg_conn, table_name, columns, conflict_col=None):
    sql_cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    
    # Check if target table is empty
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        if cur.fetchone()[0] > 0:
            print(f"Target table '{table_name}' already contains records. Skipping migration for this table.")
            return

    sqlite_cur = sqlite_conn.cursor()
    try:
        sqlite_cur.execute(f"SELECT {sql_cols} FROM {table_name}")
        rows = sqlite_cur.fetchall()
    except sqlite3.OperationalError:
        print(f"Table '{table_name}' does not exist in SQLite database. Skipping.")
        return

    if not rows:
        print(f"No records found in SQLite table '{table_name}'.")
        return

    print(f"Migrating {len(rows)} records for '{table_name}'...")
    with pg_conn.cursor() as cur:
        for row in rows:
            values = [row[col] for col in columns]
            
            if conflict_col:
                # Handle unique/primary conflicts gracefully
                updates = ", ".join([f"{col} = EXCLUDED.{col}" for col in columns if col != conflict_col])
                query = f"""
                    INSERT INTO {table_name} ({sql_cols}) 
                    VALUES ({placeholders}) 
                    ON CONFLICT ({conflict_col}) 
                    DO UPDATE SET {updates}
                """
            elif table_name == "accounts":
                query = f"""
                    INSERT INTO {table_name} ({sql_cols}) 
                    VALUES ({placeholders}) 
                    ON CONFLICT (platform, label) 
                    DO UPDATE SET credentials_enc = EXCLUDED.credentials_enc
                """
            else:
                query = f"INSERT INTO {table_name} ({sql_cols}) VALUES ({placeholders})"
                
            cur.execute(query, values)
    pg_conn.commit()
    print(f"Successfully migrated '{table_name}'.")

def main():
    if not os.path.exists(SQLITE_PATH):
        print(f"SQLite database not found at {SQLITE_PATH}. Nothing to migrate.")
        return

    print("Connecting to databases...")
    try:
        sqlite_conn = get_sqlite_connection()
        pg_conn = get_postgres_connection()
    except Exception as e:
        print(f"Database connection failed: {e}")
        return

    try:
        print("Creating PostgreSQL tables...")
        create_postgres_tables(pg_conn)

        # Migrate Settings
        migrate_table(
            sqlite_conn, pg_conn, "settings", 
            ["key", "value"], 
            conflict_col="key"
        )

        # Migrate Sources
        migrate_table(
            sqlite_conn, pg_conn, "sources", 
            ["id", "url", "name", "platform", "last_checked", "is_active", "created_at", "target_platforms"], 
            conflict_col="id"
        )

        # Migrate Posts History
        migrate_table(
            sqlite_conn, pg_conn, "posts_history", 
            [
                "id", "source_id", "video_id", "title", "caption", "tweet_id", 
                "account_label", "status", "error_msg", "posted_at", "fail_count", 
                "created_at", "platform", "external_id", "views", "likes", 
                "shares", "comments", "last_analytics_updated_at"
            ], 
            conflict_col="id"
        )

        # Migrate Logs
        migrate_table(
            sqlite_conn, pg_conn, "logs", 
            ["id", "timestamp", "level", "message"], 
            conflict_col="id"
        )

        # Migrate Accounts
        migrate_table(
            sqlite_conn, pg_conn, "accounts", 
            [
                "id", "label", "platform", "auth_mode", "credentials_enc", 
                "added_at", "last_used_at", "post_count_today", "daily_reset_date", 
                "is_active", "proxy_url", "user_agent"
            ], 
            conflict_col="id"
        )

        # Reset serial sequences for auto-increment keys
        with pg_conn.cursor() as cur:
            for seq_table in ["sources", "posts_history", "logs", "accounts"]:
                cur.execute(f"SELECT setval('{seq_table}_id_seq', COALESCE((SELECT MAX(id)+1 FROM {seq_table}), 1), false)")
        pg_conn.commit()

        print("Migration complete!")
    finally:
        sqlite_conn.close()
        pg_conn.close()

if __name__ == "__main__":
    main()
