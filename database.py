"""
WindyDash - SQLite Database & Cryptographic Vault
Provides secure user credential management, session token tracking,
and network probe target persistence using standard library sqlite3 and hashlib.
Zero external pip dependencies.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import BASE_DIR, AUTH_TOKEN

DB_FILE = BASE_DIR / "windydash.db"
TARGETS_JSON_FILE = BASE_DIR / "targets.json"

# Cryptographic parameters for scrypt (RFC 7914)
SCRYPT_N = 16384  # CPU/memory cost
SCRYPT_R = 8      # Block size
SCRYPT_P = 1      # Parallelization factor
SALT_BYTES = 16
SESSION_TTL_SECONDS = 30 * 86400  # 30-day session lifetime

def get_connection() -> sqlite3.Connection:
    """Obtain a SQLite connection configured for high performance and integrity."""
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def hash_password(password: str, salt: bytes) -> str:
    """Derive a memory-hard scrypt hash using standard library hashlib."""
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P
    )
    return derived.hex()

def init_db():
    """
    Initialize SQLite schema, secure file permissions, and migrate existing targets.json.
    """
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_login REAL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                method TEXT NOT NULL,
                port INTEGER,
                sort_order INTEGER DEFAULT 0
            );
        """)
        conn.commit()

        # Secure file permissions (rw-------) for credential safety on Linux
        try:
            os.chmod(DB_FILE, 0o600)
        except Exception:
            pass

        # Migrate targets.json if the targets table is currently empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM targets;")
        count = cur.fetchone()[0]

        if count == 0:
            migrated = False
            if TARGETS_JSON_FILE.exists():
                try:
                    raw = TARGETS_JSON_FILE.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    if isinstance(data, list):
                        for idx, item in enumerate(data):
                            t_id = str(item.get("id") or secrets.token_hex(4))
                            name = str(item.get("name") or item.get("host") or "Target")
                            host = str(item.get("host") or "").strip()
                            method = str(item.get("method") or "ping").lower()
                            port = item.get("port")
                            if host:
                                cur.execute(
                                    "INSERT OR REPLACE INTO targets (id, name, host, method, port, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                                    (t_id, name, host, method, int(port) if port else None, idx)
                                )
                        conn.commit()
                        migrated = True
                except Exception as e:
                    print(f"Warning: Failed to migrate targets.json to SQLite: {e}")

            if not migrated:
                # Insert default bootstrap targets
                default_targets = [
                    ("1", "Cloudflare DNS", "1.1.1.1", "ping", None, 0),
                    ("2", "Google DNS", "8.8.8.8", "tcp", 53, 1),
                    ("3", "GitHub API", "api.github.com", "http", None, 2),
                ]
                cur.executemany(
                    "INSERT OR REPLACE INTO targets (id, name, host, method, port, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                    default_targets
                )
                conn.commit()

        # Bootstrap admin user from DASH_PASSWORD environment variable if users table is empty
        cur.execute("SELECT COUNT(*) FROM users;")
        user_count = cur.fetchone()[0]
        if user_count == 0 and AUTH_TOKEN:
            salt = secrets.token_bytes(SALT_BYTES)
            p_hash = hash_password(AUTH_TOKEN, salt)
            cur.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                ("admin", p_hash, salt.hex(), time.time())
            )
            conn.commit()

# --- User Management ---

def has_users() -> bool:
    """Return True if at least one operator account exists."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users LIMIT 1;")
        return cur.fetchone() is not None

def create_user(username: str, password: str) -> bool:
    """Register a new user with scrypt password hashing."""
    username = username.strip()
    if not username or not password:
        return False

    salt = secrets.token_bytes(SALT_BYTES)
    p_hash = hash_password(password, salt)
    now = time.time()

    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (username, p_hash, salt.hex(), now)
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False  # Username already exists

def verify_user(username: str, password: str) -> bool:
    """Authenticate credentials using constant-time hash comparison."""
    username = username.strip()
    if not username or not password:
        return False

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, password_hash, salt FROM users WHERE username = ?",
            (username,)
        )
        row = cur.fetchone()
        if not row:
            return False

        salt = bytes.fromhex(row["salt"])
        expected_hash = hash_password(password, salt)

        if hmac.compare_digest(expected_hash, row["password_hash"]):
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (time.time(), row["id"])
            )
            conn.commit()
            return True

    return False

def list_users() -> List[Dict[str, Any]]:
    """List registered users with metadata (passwords excluded)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, created_at, last_login FROM users ORDER BY id ASC")
        return [dict(r) for r in cur.fetchall()]

def change_password(username: str, new_password: str) -> bool:
    """Update an existing user's password."""
    salt = secrets.token_bytes(SALT_BYTES)
    p_hash = hash_password(new_password, salt)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
            (p_hash, salt.hex(), username.strip())
        )
        conn.commit()
        return cur.rowcount > 0

# --- Session Management ---

def create_session(username: str) -> str:
    """Generate and store a cryptographically random session token."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + SESSION_TTL_SECONDS

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, username.strip(), now, expires_at)
        )
        conn.commit()
    return token

def verify_session(token: str) -> Optional[str]:
    """Verify an active session token and return the username if valid."""
    if not token:
        return None

    now = time.time()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, expires_at FROM sessions WHERE token = ?",
            (token.strip(),)
        )
        row = cur.fetchone()
        if not row:
            return None

        if row["expires_at"] < now:
            # Stale session
            conn.execute("DELETE FROM sessions WHERE token = ?", (token.strip(),))
            conn.commit()
            return None

        return str(row["username"])

def delete_session(token: str) -> bool:
    """Revoke a session token on logout."""
    if not token:
        return False
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE token = ?", (token.strip(),))
        conn.commit()
        return cur.rowcount > 0

def clean_expired_sessions() -> int:
    """Remove expired sessions to keep the sessions table small."""
    now = time.time()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.commit()
        return cur.rowcount

# --- Probe Targets Management ---

def get_targets() -> List[Dict[str, Any]]:
    """Retrieve all network probe targets ordered by sort_order."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, host, method, port FROM targets ORDER BY sort_order ASC, id ASC")
        return [dict(r) for r in cur.fetchall()]

def add_target(name: str, host: str, method: str, port: Optional[int] = None, target_id: Optional[str] = None) -> Dict[str, Any]:
    """Insert or update a network probe target."""
    t_id = target_id or secrets.token_hex(4)
    name = (name or host).strip()
    host = host.strip()
    method = (method or "ping").lower().strip()
    port_val = int(port) if port else None

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM targets;")
        next_order = cur.fetchone()[0]
        cur.execute(
            "INSERT OR REPLACE INTO targets (id, name, host, method, port, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
            (t_id, name, host, method, port_val, next_order)
        )
        conn.commit()

    return {
        "id": t_id,
        "name": name,
        "host": host,
        "method": method,
        "port": port_val
    }

def delete_target(target_id: str) -> bool:
    """Remove a target by ID."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM targets WHERE id = ?", (str(target_id),))
        conn.commit()
        return cur.rowcount > 0

# Self-initialize schema on import
init_db()
