"""
WindyDash - Unified Telemetry Daemon & Network Probe Server
Combines Linux hardware monitoring with active network probes.
Modular, asynchronous, and designed for minimal CPU and memory footprints.
"""
import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from typing import Optional, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

from config import BASE_DIR, POLL_SECONDS, HOST, PORT
from security import verify_token, secure_api
from hardware import HardwareMonitor
from prober import NetworkMonitor
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

        if dead:
            clients.difference_update(dead)
            if not clients:
                has_active_viewers.clear()

        await asyncio.sleep(POLL_SECONDS)

async def broadcast_network_once():
    """Probe network targets once and broadcast results to clients."""
    if not clients:
        return
    data = await net_monitor.run_checks()
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    if dead:
        clients.difference_update(dead)
        if not clients:
            has_active_viewers.clear()

async def broadcast_network_loop():
    """
    Periodically probe network targets.
    Optimized: Sleeps when no clients are connected to avoid unnecessary network traffic.
    """
    while True:
        if not clients:
            await has_active_viewers.wait()  # Idle sleep when no clients are watching

        await broadcast_network_once()
        await asyncio.sleep(5.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background task lifecycles and clean shutdown."""
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
    if not verify_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
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
    targets = net_monitor.load_targets()
    new_target = {
        "id": str(uuid.uuid4())[:8],
        "name": payload.name.strip() or payload.host.strip(),
        "host": payload.host.strip(),
        "method": payload.method.lower(),
    }
    if payload.method.lower() == "tcp" and payload.port:
        new_target["port"] = int(payload.port)

    targets.append(new_target)
    net_monitor.save_targets(targets)
    asyncio.create_task(broadcast_network_once())
    return new_target

@app.delete("/api/targets/{target_id}", dependencies=secure_api)
async def delete_target(target_id: str):
    targets = net_monitor.load_targets()
    filtered = [t for t in targets if str(t.get("id")) != str(target_id)]
    net_monitor.save_targets(filtered)
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
    print(f"WindyDash Daemon running on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
