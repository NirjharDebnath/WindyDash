"""
WindyDash - Security & Rate Limiting
Provides timing-attack safe token authentication against SQLite session vault
and a self-pruning sliding-window rate limiter.
"""
import hmac
import time
from collections import defaultdict
from typing import Any, Optional
from config import AUTH_TOKEN, RATE_LIMIT, RATE_WINDOW
import database

try:
    from fastapi import Request, HTTPException, Depends
except ImportError:
    # Graceful fallback when imported outside a FastAPI environment
    class Request:  # type: ignore
        pass
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int, detail: str):
            super().__init__(f"HTTP {status_code}: {detail}")
            self.status_code = status_code
            self.detail = detail
    def Depends(dependency: Any) -> Any:  # type: ignore
        return dependency

# Track request timestamps per IP address for rate limiting
_ip_request_history = defaultdict(list)
_last_cleanup = time.monotonic()

def verify_token(token: str) -> Optional[str]:
    """
    Verify the given token against active SQLite sessions or legacy AUTH_TOKEN.
    Returns the authenticated username if valid, or None if invalid.
    """
    token_str = str(token or "").strip()
    if not token_str:
        # If the database has no registered users and no master password is set, allow guest
        if not database.has_users() and not AUTH_TOKEN:
            return "guest"
        return None

    # 1. Check against SQLite session store
    username = database.verify_session(token_str)
    if username:
        return username

    # 2. Check against legacy DASH_PASSWORD / AUTH_TOKEN (constant-time comparison)
    if AUTH_TOKEN and hmac.compare_digest(token_str, AUTH_TOKEN):
        return "admin"

    return None

def is_valid_token(token: str) -> bool:
    """Return True if token represents a valid authenticated session."""
    return verify_token(token) is not None

def check_auth(request: Request) -> str:
    """
    FastAPI dependency to verify authentication token from headers or query parameters.
    Returns the authenticated username or raises HTTP 401.
    """
    token = ""
    headers = getattr(request, "headers", {})
    if "X-Auth-Token" in headers:
        token = headers.get("X-Auth-Token", "")
    elif "authorization" in headers:
        auth_header = headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token:
        query_params = getattr(request, "query_params", {})
        token = query_params.get("token", "")

    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return username

def rate_limiter(request: Request):
    """
    Sliding-window rate limiter.
    Automatically prunes idle client IPs to prevent long-term memory leaks.
    """
    global _last_cleanup
    client = getattr(request, "client", None)
    client_ip = client.host if client else "unknown"
    now = time.monotonic()

    # Filter timestamps for this client within the sliding window
    history = [t for t in _ip_request_history[client_ip] if now - t < RATE_WINDOW]
    if len(history) >= RATE_LIMIT:
        _ip_request_history[client_ip] = history
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please slow down.")

    history.append(now)
    _ip_request_history[client_ip] = history

    # Periodic garbage collection: prune stale IPs every 60 seconds
    if now - _last_cleanup > 60.0:
        _last_cleanup = now
        stale_ips = [
            ip for ip, timestamps in _ip_request_history.items()
            if not timestamps or (now - timestamps[-1] >= RATE_WINDOW)
        ]
        for ip in stale_ips:
            _ip_request_history.pop(ip, None)

# Combined security dependency bundle for protected API routes
secure_api = [Depends(check_auth), Depends(rate_limiter)]
