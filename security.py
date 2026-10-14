"""
WindyDash - Security & Rate Limiting
Provides timing-attack safe token authentication and a self-pruning sliding-window rate limiter.
Includes graceful fallbacks if FastAPI is not yet installed in the current environment.
"""
import hmac
import time
from collections import defaultdict
from typing import Any
from config import AUTH_TOKEN, RATE_LIMIT, RATE_WINDOW

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

# Track request timestamps per IP address
_ip_request_history = defaultdict(list)
_last_cleanup = time.monotonic()

def verify_token(token: str) -> bool:
    """Safely compare the given token against the configured password using constant-time comparison."""
    if not AUTH_TOKEN:
        # If no password is set, authentication is bypassed
        return True
    return hmac.compare_digest(str(token or ""), AUTH_TOKEN)

def check_auth(request: Request):
    """FastAPI dependency to verify the X-Auth-Token header."""
    token = getattr(request, "headers", {}).get("X-Auth-Token", "")
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

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

    # Periodic garbage collection: prune keys every 60 seconds
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
