"""
WindyDash - Unified Telemetry Daemon & Network Probe Server
Combines Linux hardware monitoring with active network probes and SQLite vault.
Modular, asynchronous, and designed for minimal CPU and memory footprints.
"""
import asyncio
import hmac
import re
import sys
from contextlib import asynccontextmanager
from typing import Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel
import uvicorn

from config import BASE_DIR, POLL_SECONDS, HOST, PORT, AUTH_TOKEN
from security import verify_token, secure_api, rate_limiter
from hardware import HardwareMonitor
from prober import NetworkMonitor
import database
from diagnostics import (
    compute_device_status,
    stream_traceroute,
    stream_discovery,
    stream_dns_lookup,
    stream_port_check,
    stream_speedtest
)

# Active connected WebSocket clients
clients: Set[WebSocket] = set()

# Wakes up loops when at least one viewer is connected; pauses when idle
has_active_viewers = asyncio.Event()

hw_monitor = HardwareMonitor()
net_monitor = NetworkMonitor()

async def broadcast_hardware_loop():
    """Continuously sample and broadcast hardware vitals to connected clients."""
    while True:
        if not clients:
            has_active_viewers.clear()
            await has_active_viewers.wait()  # Sleep indefinitely with 0% CPU usage

        # Run collection off the event loop thread
        data = await hw_monitor.collect_async()

        # Broadcast to all connected clients
        dead = set()
        for ws in list(clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)

        for ws in dead:
            clients.discard(ws)

        await asyncio.sleep(POLL_SECONDS)

async def broadcast_network_once():
    """Probe network targets and broadcast status to all active clients once."""
    if not clients:
        return
    results = await net_monitor.run_checks()
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_json(results)
        except Exception:
            dead.add(ws)
    for ws in dead:
        clients.discard(ws)

async def broadcast_network_loop():
    """Periodically probe targets in the background only when viewers are connected."""
    while True:
        if not clients:
            has_active_viewers.clear()
            await has_active_viewers.wait()

        await broadcast_network_once()
        # Probe network targets every 4.0 seconds
        await asyncio.sleep(4.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background task lifecycles and clean shutdown."""
    database.clean_expired_sessions()
    hw_task = asyncio.create_task(broadcast_hardware_loop())
    net_task = asyncio.create_task(broadcast_network_loop())
    yield
    hw_task.cancel()
    net_task.cancel()
    await net_monitor.close()

app = FastAPI(title="WindyDash Daemon", lifespan=lifespan)

# --- WebSocket Telemetry Stream ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    username = verify_token(token)
    if not username:
        # Accept handshake and immediately close with WS_1008_POLICY_VIOLATION.
        # This guarantees standard browser clients receive code 1008 instead of 1006.
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await websocket.accept()
    clients.add(websocket)
    has_active_viewers.set()  # Wake up collection loops

    # Send an immediate snapshot of targets on connection
    asyncio.create_task(broadcast_network_once())

    try:
        while True:
            # Keep connection open; clients can send keepalive pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        if not clients:
            has_active_viewers.clear()

# --- Static Frontend Serving ---

@app.get("/")
async def get_index():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/styles.css")
async def get_css():
    return FileResponse(BASE_DIR / "styles.css")

@app.get("/app.js")
async def get_js():
    return FileResponse(BASE_DIR / "app.js")

@app.get("/favicon.ico")
async def get_favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🍁</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")

# --- Authentication & Session APIs ---

class LoginRequest(BaseModel):
    username: str
    password: str

class SetupRequest(BaseModel):
    username: str
    password: str

@app.get("/api/auth/status")
async def get_auth_status(request: Request):
    """Check if the system has users and whether the caller's token is valid."""
    token = ""
    headers = getattr(request, "headers", {})
    if "x-auth-token" in headers:
        token = headers["x-auth-token"]
    elif "authorization" in headers:
        auth_header = headers["authorization"]
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token:
        token = request.query_params.get("token", "")

    username = verify_token(token)
    return {
        "authenticated": username is not None,
        "has_users": database.has_users(),
        "username": username
    }

@app.post("/api/auth/setup")
async def setup_master_user(payload: SetupRequest):
    """Initialize the master administrator account if no users exist yet."""
    if database.has_users():
        raise HTTPException(status_code=400, detail="Master operator account already configured")

    username = payload.username.strip()
    password = payload.password
    if not username or len(password) < 4:
        raise HTTPException(status_code=400, detail="Username required and password must be at least 4 characters")

    success = database.create_user(username, password)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to initialize master operator")

    token = database.create_session(username)
    return {"status": "ok", "token": token, "username": username}

@app.post("/api/auth/login")
async def login_user(payload: LoginRequest):
    """Authenticate operator credentials and return a signed session token."""
    username = payload.username.strip()
    password = payload.password

    # 1. Authenticate against SQLite user database
    if database.verify_user(username, password):
        token = database.create_session(username)
        return {"status": "ok", "token": token, "username": username}

    # 2. Check fallback DASH_PASSWORD if configured
    if AUTH_TOKEN and hmac.compare_digest(password, AUTH_TOKEN):
        token = database.create_session("admin")
        return {"status": "ok", "token": token, "username": "admin"}

    raise HTTPException(status_code=401, detail="Invalid operator username or password")

@app.post("/api/auth/logout")
async def logout_user(request: Request):
    """Revoke active session token."""
    token = request.headers.get("x-auth-token", "")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if token:
        database.delete_session(token)
    return {"status": "logged_out"}

# --- REST APIs ---

class TargetRequest(BaseModel):
    name: str = ""
    host: str
    method: str
    port: Optional[int] = None

@app.get("/api/device-status", dependencies=secure_api)
async def get_device_status():
    return await compute_device_status(net_monitor.http_client)

@app.post("/api/targets", dependencies=secure_api)
async def add_target(payload: TargetRequest):
    new_target = database.add_target(
        name=payload.name.strip(),
        host=payload.host.strip(),
        method=payload.method.lower(),
        port=payload.port
    )
    net_monitor.refresh_targets()
    asyncio.create_task(broadcast_network_once())
    return new_target

@app.delete("/api/targets/{target_id}", dependencies=secure_api)
async def delete_target(target_id: str):
    database.delete_target(target_id)
    net_monitor.refresh_targets()
    asyncio.create_task(broadcast_network_once())
    return {"status": "deleted"}

@app.post("/api/targets/recheck", dependencies=secure_api)
async def trigger_target_recheck():
    """Manually trigger immediate probe of all network targets."""
    asyncio.create_task(broadcast_network_once())
    return {"status": "triggered"}

@app.post("/api/traceroute", dependencies=secure_api)
async def run_traceroute(payload: dict):
    host = payload.get("host", "").strip()
    clean_host = re.sub(r"^https?://", "", host).split("/")[0].split(":")[0]
    return StreamingResponse(stream_traceroute(clean_host), media_type="application/x-ndjson")

@app.post("/api/discovery", dependencies=secure_api)
async def run_discovery(payload: dict):
    subnet = payload.get("subnet", "").strip()
    return StreamingResponse(stream_discovery(subnet), media_type="application/x-ndjson")

@app.post("/api/dns", dependencies=secure_api)
async def run_dns(payload: dict):
    domain = payload.get("domain", "").strip()
    return StreamingResponse(stream_dns_lookup(domain), media_type="application/x-ndjson")

@app.post("/api/portcheck", dependencies=secure_api)
async def run_portcheck(payload: dict):
    host = payload.get("host", "").strip()
    port = int(payload.get("port", 80))
    return StreamingResponse(stream_port_check(host, port), media_type="application/x-ndjson")

@app.post("/api/speedtest", dependencies=secure_api)
async def run_speedtest():
    return StreamingResponse(stream_speedtest(net_monitor.http_client), media_type="application/x-ndjson")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd in ("--adduser", "-a") and len(sys.argv) >= 3:
            user = sys.argv[2]
            import getpass
            pwd = getpass.getpass(f"Enter password for operator '{user}': ")
            if database.create_user(user, pwd):
                print(f"[OK] Operator '{user}' registered in SQLite vault.")
            else:
                print(f"[ERROR] Failed to register operator '{user}' (already exists?).")
            sys.exit(0)
        elif cmd in ("--passwd", "-p") and len(sys.argv) >= 3:
            user = sys.argv[2]
            import getpass
            pwd = getpass.getpass(f"Enter new password for operator '{user}': ")
            if database.change_password(user, pwd):
                print(f"[OK] Password updated for operator '{user}'.")
            else:
                print(f"[ERROR] Operator '{user}' not found.")
            sys.exit(0)
        elif cmd in ("--listusers", "-l"):
            users = database.list_users()
            print("Registered Operators in SQLite Vault:")
            for u in users:
                print(f" - {u['username']} (ID: {u['id']})")
            sys.exit(0)

    print(f"WindyDash Daemon running on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
