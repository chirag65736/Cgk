import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor
from datetime import datetime
import threading
import time
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RAILWAY-COMPATIBLE DATABASE CONFIG
# Railway provides DATABASE_URL automatically when you add PostgreSQL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_db_config():
    """Auto-detect Railway PostgreSQL env vars or use manual config."""
    # Priority 1: DATABASE_URL (Railway default)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return {"dsn": database_url}
    
    # Priority 2: Railway PG* env vars
    if os.getenv("PGHOST"):
        return {
            "host": os.getenv("PGHOST"),
            "database": os.getenv("PGDATABASE", "railway"),
            "user": os.getenv("PGUSER", "postgres"),
            "password": os.getenv("PGPASSWORD", ""),
            "port": int(os.getenv("PGPORT", 5432))
        }
    
    # Priority 3: Manual config
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "database": os.getenv("DB_NAME", "telegram_bot"),
        "user": os.getenv("DB_USER", "botuser"),
        "password": os.getenv("DB_PASSWORD", "STRONG_PASSWORD"),
        "port": int(os.getenv("DB_PORT", 5432))
    }

DB_CONFIG = _get_db_config()

_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            if "dsn" in DB_CONFIG:
                _pool = psycopg2.pool.ThreadedConnectionPool(2, 20, DB_CONFIG["dsn"])
            else:
                _pool = psycopg2.pool.ThreadedConnectionPool(2, 20, **DB_CONFIG)
            logging.info("[DB] Pool created")
    return _pool

def get_db_connection():
    return _get_pool().getconn()

def _release(conn):
    try:
        _get_pool().putconn(conn)
    except Exception as e:
        logging.warning(f"putconn error: {e}")

class PooledConn:
    def __init__(self):
        self.conn = None
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn
    def __exit__(self, exc_type, *_):
        if self.conn:
            if exc_type:
                try: self.conn.rollback()
                except Exception: pass
            _release(self.conn)
        return False

_gate_cache = {}
_gate_cache_ttl = 30
_credits_cache = {}
_credits_cache_ttl = 5
_premium_cache = {}
_premium_cache_ttl = 60

def ensure_all_tables():
    try:
        with PooledConn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    credits INTEGER DEFAULT 150,
                    is_premium INTEGER DEFAULT 0,
                    premium_expiry TIMESTAMP,
                    cc_checked INTEGER DEFAULT 0,
                    cc_charged INTEGER DEFAULT 0,
                    joined_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    proxy TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gate_status (
                    gate TEXT PRIMARY KEY,
                    is_enabled BOOLEAN DEFAULT TRUE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS receipts (
                    id SERIAL PRIMARY KEY,
                    receipt_id TEXT UNIQUE,
                    user_id BIGINT,
                    plan TEXT,
                    amount_paid REAL,
                    purchased_on TIMESTAMP,
                    expires_on TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS codes (
                    code TEXT PRIMARY KEY,
                    credits INTEGER,
                    claimed_by BIGINT,
                    claimed_at TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        print("[DB] All tables ready.")
    except Exception as e:
        print(f"[DB] Error creating tables: {e}")

def get_user(user_id):
    with PooledConn() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()

def create_user(user_id, username):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, username, credits, joined_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, username, 150, datetime.now()))
        conn.commit()

def get_user_credits(user_id):
    now = time.monotonic()
    cached = _credits_cache.get(user_id)
    if cached and now < cached[1]:
        return cached[0]
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT credits FROM users WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        credits = result[0] if result else 0
    _credits_cache[user_id] = (credits, now + _credits_cache_ttl)
    return credits

def update_credits(user_id, new_credits):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET credits = %s WHERE user_id = %s", (new_credits, user_id))
        conn.commit()
    _credits_cache[user_id] = (new_credits, time.monotonic() + _credits_cache_ttl)

def update_user_stats(user_id, is_charged):
    with PooledConn() as conn:
        cur = conn.cursor()
        if is_charged:
            cur.execute("UPDATE users SET cc_checked = cc_checked + 1, cc_charged = cc_charged + 1 WHERE user_id = %s", (user_id,))
        else:
            cur.execute("UPDATE users SET cc_checked = cc_checked + 1 WHERE user_id = %s", (user_id,))
        conn.commit()

def is_gate_enabled(gate):
    now = time.monotonic()
    cached = _gate_cache.get(gate)
    if cached and now < cached[1]:
        return cached[0]
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO gate_status (gate, is_enabled) VALUES (%s, TRUE) ON CONFLICT (gate) DO NOTHING", (gate,))
        cur.execute("SELECT is_enabled FROM gate_status WHERE gate = %s", (gate,))
        row = cur.fetchone()
        conn.commit()
        status = bool(row[0]) if row else True
    _gate_cache[gate] = (status, now + _gate_cache_ttl)
    return status

def set_gate_status(gate, enabled):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO gate_status (gate, is_enabled) VALUES (%s, %s)
            ON CONFLICT (gate) DO UPDATE SET is_enabled = EXCLUDED.is_enabled
        """, (gate, enabled))
        conn.commit()
    _gate_cache[gate] = (enabled, time.monotonic() + _gate_cache_ttl)

def is_banned(user_id):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (user_id,))
        return cur.fetchone() is not None

def ban_user(user_id):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        conn.commit()

def unban_user(user_id):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM banned_users WHERE user_id = %s RETURNING user_id", (user_id,))
        result = cur.fetchone()
        conn.commit()
        return result is not None

def get_all_user_ids():
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        return [r[0] for r in cur.fetchall()]

def get_user_proxies(user_id):
    with PooledConn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT proxy FROM proxies WHERE user_id = %s", (user_id,))
        return [r[0] for r in cur.fetchall()]

# Initialize tables
try:
    ensure_all_tables()
except Exception as e:
    logging.error(f"[DB] Init failed: {e}")