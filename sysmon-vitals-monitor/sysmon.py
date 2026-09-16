"""
WindyDash + TingPing Unified Daemon
Full Linux telemetry collector + active network probe engine.
"""
import asyncio
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
import httpx
import psutil
import uvicorn
import hmac
from collections import defaultdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# Anchor file lookups to the script's actual directory
BASE_DIR = Path(__file__).resolve().parent
TARGETS_FILE = BASE_DIR / "targets.json"

POLL_SECONDS = float(os.getenv("SYSMON_INTERVAL"))
HOST = os.getenv("SYSMON_HOST")
PORT = int(os.getenv("SYSMON_PORT"))
AUTH_TOKEN = os.getenv("DASH_PASSWORD")

# Anti-DDoS Rate Limiter state
RATE_LIMIT = 20  # Max 20 requests
RATE_WINDOW = 5  # Per 5 seconds
ip_request_history = defaultdict(list)
has_active_viewers = asyncio.Event()

def check_auth(request: Request):
    token = request.headers.get("X-Auth-Token", "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

def rate_limiter(request: Request):
    client_ip = request.client.host
    now = time.monotonic()
    ip_request_history[client_ip] = [t for t in ip_request_history[client_ip] if now - t < RATE_WINDOW]
    if len(ip_request_history[client_ip]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    ip_request_history[client_ip].append(now)

secure_api = [Depends(check_auth), Depends(rate_limiter)]

def read_text(path):
    try: return Path(path).read_text(errors="ignore").strip()
    except Exception: return ""

def human_time(seconds):
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, r = divmod(r, 60)
    return (f"{d}d " if d else "") + (f"{h}h {m}m" if h else f"{m}m {r}s")

# --- Comprehensive Linux Hardware Telemetry ---
class HardwareMonitor:
    def __init__(self):
        psutil.cpu_percent(None, percpu=True)
        self.prev_net = psutil.net_io_counters(pernic=True)
        self.prev_disk = psutil.disk_io_counters(perdisk=True)
        self.session_start_net = psutil.net_io_counters() # Tracks session rx/tx
        self.prev_time = time.monotonic()
        self.gpu = self.detect_gpu()
        self.last_heavy_check = 0
        self.cached_proc = []
        self.cached_gpu = []

    def meminfo(self):
        out = {}
        for line in read_text("/proc/meminfo").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                m = re.search(r"([0-9]+)", v)
                if m: out[k] = int(m.group(1)) * 1024
        return out

    def detect_gpu(self):
        try:
            subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return "nvidia"
        except Exception:
            return None

    def gpu_data(self):
        if not self.gpu: return []
        try:
            q = (
                "name,driver_version,vbios_version,pci.bus_id,"
                "utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,"
                "memory.total,memory.used,memory.free,temperature.gpu,power.draw,"
                "clocks.current.graphics,clocks.current.memory"
            )
            out = subprocess.check_output(
                ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                text=True, timeout=1
            )
            result = []
            for line in out.splitlines():
                p = [x.strip() for x in line.split(",")]
                if len(p) >= 15:
                    result.append({
                        "name": p[0],
                        "driver_version": p[1],
                        "vbios": p[2],
                        "pci_bus": p[3],
                        "utilization_percent": float(p[4]),
                        "memory_utilization": float(p[5]),
                        "encoder_utilization": float(p[6]),
                        "decoder_utilization": float(p[7]),
                        "memory_total_mb": float(p[8]),
                        "memory_used_mb": float(p[9]),
                        "memory_free_mb": float(p[10]),
                        "temperature_c": float(p[11]),
                        "power_watts": float(p[12]),
                        "clock_core_mhz": float(p[13]),
                        "clock_mem_mhz": float(p[14])
                    })
            return result
        except Exception:
            return []

    def sensors(self):
        result = {}
        for hw in Path("/sys/class/hwmon").glob("hwmon*"):
            name = read_text(hw / "name") or hw.name
            for p in hw.glob("temp*_input"):
                try:
                    label = read_text(p.with_name(p.name.replace("_input", "_label"))) or p.stem
                    result[f"{name}:{label}"] = {"type": "temperature", "label": label, "value": int(p.read_text()) / 1000}
                except Exception: pass
            for p in hw.glob("fan*_input"):
                try:
                    label = read_text(p.with_name(p.name.replace("_input", "_label"))) or p.stem
                    result[f"{name}:{label}"] = {"type": "fan", "label": label, "value": int(p.read_text())}
                except Exception: pass
            for p in hw.glob("in*_input"):
                try:
                    label = read_text(p.with_name(p.name.replace("_input", "_label"))) or p.stem
                    result[f"{name}:{label}"] = {"type": "voltage", "label": label, "value": int(p.read_text()) / 1000}
                except Exception: pass
        return result

    def batteries(self):
        out = []
        for b in Path("/sys/class/power_supply").glob("BAT*"):
            def val(k):
                try: return float(read_text(b / k))
                except Exception: return None

            e_design = val("energy_full_design") or val("charge_full_design")
            e_full = val("energy_full") or val("charge_full")
            e_now = val("energy_now") or val("charge_now")
            voltage = val("voltage_now")
            power = val("power_now") or val("current_now")

            # Health vs Capacity
            health = round((e_full / e_design) * 100, 1) if (e_full and e_design) else None
            percent = val("capacity") or (round((e_now / e_full) * 100, 1) if (e_now and e_full) else 0)

            out.append({
                "name": b.name,
                "percentage": percent,
                "health_capacity": health,
                "cycles": val("cycle_count"),
                "energy_design_wh": round(e_design / 1e6, 1) if e_design else None,
                "energy_full_wh": round(e_full / 1e6, 1) if e_full else None,
                "energy_now_wh": round(e_now / 1e6, 1) if e_now else None,
                "power_rate_w": round(power / 1e6, 1) if power else 0.0,
                "voltage_v": round(voltage / 1e6, 2) if voltage else None,
                "status": read_text(b / "status") or "Unknown"
            })
        return out

    def filesystems(self):
        result = []
        seen = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen: continue
            seen.add(part.mountpoint)
            try:
                u = psutil.disk_usage(part.mountpoint)
                result.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": u.total,
                    "used": u.used,
                    "free": u.free,
                    "percent": u.percent
                })
            except Exception: pass
        return result
    
    def wifi_and_session_network(self):
        wifi_info = {}
        try:
            lines = read_text("/proc/net/wireless").splitlines()
            for line in lines[2:]:  # Skip headers
                parts = line.split()
                iface = parts[0].replace(":", "")
                link_qual = float(parts[2].replace(".", ""))
                sig_level = float(parts[3].replace(".", ""))
                wifi_info[iface] = {
                    "link_quality_percent": round((link_qual / 70.0) * 100, 1),
                    "signal_dbm": sig_level
                }
        except Exception: pass

        cur_net = psutil.net_io_counters()
        session_rx = cur_net.bytes_recv - self.session_start_net.bytes_recv
        session_tx = cur_net.bytes_sent - self.session_start_net.bytes_sent
        return {
            "wifi": wifi_info,
            "boot_rx_bytes": cur_net.bytes_recv,
            "boot_tx_bytes": cur_net.bytes_sent,
            "session_rx_bytes": session_rx,
            "session_tx_bytes": session_tx
        }

    # --- 4. Displays, USB, and Thunderbolt Collector ---
    def peripherals(self):
        # Connected Displays via DRM
        displays = []
        for status_path in Path("/sys/class/drm").glob("card*-*/status"):
            if read_text(status_path) == "connected":
                conn_name = status_path.parent.name.split("-", 1)[-1]
                modes_file = status_path.parent / "modes"
                modes = read_text(modes_file).splitlines() if modes_file.exists() else []
                displays.append({
                    "connector": conn_name,
                    "resolution": modes[0] if modes else "Unknown",
                    "available_modes": modes[:4]
                })

        # USB Devices
        usb_devices = []
        for dev_path in Path("/sys/bus/usb/devices").glob("*"):
            prod = read_text(dev_path / "product")
            manuf = read_text(dev_path / "manufacturer")
            if prod:
                usb_devices.append(f"{manuf} {prod}".strip())

        # Thunderbolt Devices
        tb_devices = []
        for tb in Path("/sys/bus/thunderbolt/devices").glob("domain*"):
            name = read_text(tb / "device_name") or tb.name
            authorized = read_text(tb / "authorized")
            tb_devices.append({"device": name, "authorized": authorized == "1"})

        return {
            "displays": displays,
            "usb": sorted(list(set(usb_devices))),
            "thunderbolt": tb_devices
        }

    def collect(self):
        now = time.monotonic()
        dt = max(now - self.prev_time, 0.001)
        
        # Heavy tasks only run every 4.0 seconds
        if now - self.last_heavy_check >= 4.0:
            self.last_heavy_check = now
            self.cached_gpu = self.gpu_data()
            
            proc = []
            for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "status", "num_threads"]):
                try:
                    z = p.info
                    z["cpu_percent"] = z.get("cpu_percent") or 0.0
                    z["memory_percent"] = z.get("memory_percent") or 0.0
                    proc.append(z)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess): pass
            self.cached_proc = sorted(proc, key=lambda x: x["cpu_percent"], reverse=True)[:100]

        mi = self.meminfo()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        freq = psutil.cpu_freq(percpu=True) or []
        cpu = psutil.cpu_percent(None, percpu=True)
        cfreq = [x.current for x in freq] if freq else []
        n = psutil.net_io_counters(pernic=True)
        d = psutil.disk_io_counters(perdisk=True)
        rx = sum(x.bytes_recv for x in n.values())
        tx = sum(x.bytes_sent for x in n.values())
        oldrx = sum(x.bytes_recv for x in self.prev_net.values())
        oldtx = sum(x.bytes_sent for x in self.prev_net.values())
        read = sum(x.read_bytes for x in d.values())
        write = sum(x.write_bytes for x in d.values())
        oldread = sum(x.read_bytes for x in self.prev_disk.values())
        oldwrite = sum(x.write_bytes for x in self.prev_disk.values())
        net_stats = self.wifi_and_session_network()

        interfaces = {}
        for name, x in n.items():
            addrs = psutil.net_if_addrs().get(name, [])
            addr = next((a.address for a in addrs if a.family == socket.AF_INET), "")
            o = self.prev_net.get(name)
            rx_sec = max(0, (x.bytes_recv - o.bytes_recv) / dt) if o else 0
            tx_sec = max(0, (x.bytes_sent - o.bytes_sent) / dt) if o else 0
            interfaces[name] = {
                "rx_bytes": x.bytes_recv,
                "tx_bytes": x.bytes_sent,
                "rx_bytes_per_sec": rx_sec,
                "tx_bytes_per_sec": tx_sec,
                "address": addr
            }

        self.prev_net = n
        self.prev_disk = d
        self.prev_time = now
        load = os.getloadavg()
        boot = psutil.boot_time()

        proc = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "status", "num_threads"]):
            try:
                z = p.info
                z["cpu_percent"] = z.get("cpu_percent") or 0.0
                z["memory_percent"] = z.get("memory_percent") or 0.0
                proc.append(z)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess): pass
        proc = sorted(proc, key=lambda x: x["cpu_percent"], reverse=True)[:100]
        threads = sum((x.get("num_threads") or 0) for x in proc)
        try: open_files = len(psutil.Process().open_files())
        except Exception: open_files = 0

        return {
            "type": "hardware",
            "timestamp": time.time(),
            "cpu": {
                "global_percent": sum(cpu) / len(cpu) if cpu else 0,
                "per_core_percent": cpu,
                "per_core_freq_mhz": cfreq,
                "logical_cores": psutil.cpu_count(True),
                "physical_cores": psutil.cpu_count(False),
                "freq_current_mhz": sum(cfreq) / len(cfreq) if cfreq else 0,
                "ctx_switches": psutil.cpu_stats().ctx_switches,
                "model": platform.processor()
            },
            "memory": {
                "total": vm.total,
                "used": vm.used,
                "available": vm.available,
                "percent": vm.percent,
                "free": mi.get("MemFree", 0),
                "cached_bytes": mi.get("Cached", 0),
                "buffers_bytes": mi.get("Buffers", 0),
                "active_bytes": mi.get("Active", 0),
                "inactive_bytes": mi.get("Inactive", 0),
                "page_tables_bytes": mi.get("PageTables", 0),
                "swap_total": sw.total,
                "swap_free": sw.free,
                "swap_used": sw.used,
                "swap_percent": sw.percent
            },
            "system": {
                "hostname": socket.gethostname(),
                "os": platform.platform(),
                "kernel": platform.release(),
                "arch": platform.machine(),
                "python": platform.python_version(),
                "load1": load[0],
                "load5": load[1],
                "load15": load[2],
                "uptime": time.time() - boot,
                "uptime_human": human_time(time.time() - boot),
                "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
                "process_count": len(psutil.pids()),
                "thread_count": threads,
                "open_files": open_files
            },
            "network": {
                "bytes_sent": tx,
                "bytes_recv": rx,
                "packets_sent": sum(x.packets_sent for x in n.values()),
                "packets_recv": sum(x.packets_recv for x in n.values()),
                "rx_bytes_per_sec": max(0, (rx - oldrx) / dt),
                "tx_bytes_per_sec": max(0, (tx - oldtx) / dt),
                "interfaces": interfaces
            },
            "disk": {
                "read_bytes": read,
                "write_bytes": write,
                "read_count": sum(x.read_count for x in d.values()),
                "write_count": sum(x.write_count for x in d.values()),
                "read_bytes_per_sec": max(0, (read - oldread) / dt),
                "write_bytes_per_sec": max(0, (write - oldwrite) / dt)
            },
            "filesystems": self.filesystems(),
            "sensors": self.sensors(),
            "battery": self.batteries(),
            "gpu": self.cached_gpu,
            "processes": self.cached_proc,
            "peripherals": self.peripherals(),
            "network_ext": net_stats
        }
        
# --- Active Network Prober Engine ---
class NetworkMonitor:
    def __init__(self):
        self.http_client = httpx.AsyncClient(verify=False, timeout=4.0, follow_redirects=True)

    def load_targets(self):
        if not TARGETS_FILE.exists():
            default_targets = [
                {"id": "1", "name": "Cloudflare DNS", "host": "1.1.1.1", "method": "ping"},
                {"id": "2", "name": "Google DNS", "host": "8.8.8.8", "method": "tcp", "port": 53},
                {"id": "3", "name": "GitHub API", "host": "api.github.com", "method": "http"}
            ]
            TARGETS_FILE.write_text(json.dumps(default_targets, indent=2))
        try:
            return json.loads(TARGETS_FILE.read_text())
        except Exception:
            return []

    async def check_ping(self, host):
        start = time.monotonic()
        cmd = ["ping", "-n" if platform.system() == "Windows" else "-c", "1", "-W", "2", host]
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            latency = (time.monotonic() - start) * 1000
            return proc.returncode == 0, latency, "OK" if proc.returncode == 0 else "Unreachable"
        except Exception as e:
            return False, None, str(e)

    async def check_tcp(self, host, port):
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
            writer.close()
            await writer.wait_closed()
            return True, (time.monotonic() - start) * 1000, f"Port {port} Open"
        except Exception as e:
            return False, None, str(e)

    async def check_http(self, host):
        start = time.monotonic()
        url = host if host.startswith(("http://", "https://")) else f"https://{host}"
        try:
            resp = await self.http_client.get(url)
            latency = (time.monotonic() - start) * 1000
            return resp.status_code < 400, latency, f"HTTP {resp.status_code}"
        except Exception as e:
            if not host.startswith(("http://", "https://")):
                try:
                    resp = await self.http_client.get(f"http://{host}")
                    latency = (time.monotonic() - start) * 1000
                    return resp.status_code < 400, latency, f"HTTP {resp.status_code}"
                except Exception:
                    pass
            return False, None, str(e)

    async def probe_target(self, t):
        host = t.get("host", "").strip()
        method = (t.get("method") or "ping").lower()
        clean_host = re.sub(r"^https?://", "", host).split("/")[0].split(":")[0]
        
        success, latency, msg = False, None, "Unknown"
        if method == "ping":
            success, latency, msg = await self.check_ping(clean_host)
        elif method == "tcp":
            port = int(t.get("port") or 80)
            success, latency, msg = await self.check_tcp(clean_host, port)
        elif method == "http":
            success, latency, msg = await self.check_http(host)

        return {
            "id": str(t.get("id")),
            "name": t.get("name") or host,
            "method": method.upper(),
            "success": success,
            "latency_ms": round(latency, 1) if latency is not None else None,
            "message": msg
        }

    async def run_checks(self):
        targets = self.load_targets()
        if not targets:
            return {"type": "network_targets", "results": []}
        # Run all network probes concurrently
        results = await asyncio.gather(*(self.probe_target(t) for t in targets), return_exceptions=False)
        return {"type": "network_targets", "results": list(results)}

# --- FastAPI Lifespan & Service Setup ---
clients = set()
hw_monitor = HardwareMonitor()
net_monitor = NetworkMonitor()

async def broadcast_hardware():
    while True:
        if not clients:
            has_active_viewers.clear()
            await has_active_viewers.wait() # Sleeps indefinitely (0% CPU)

        data = hw_monitor.collect()
        dead = set()
        for ws in list(clients):
            try: await ws.send_json(data)
            except Exception: dead.add(ws)
        clients.difference_update(dead)
        await asyncio.sleep(POLL_SECONDS)

async def broadcast_network_once():
    data = await net_monitor.run_checks()
    dead = set()
    for ws in list(clients):
        try: await ws.send_json(data)
        except Exception: dead.add(ws)
    clients.difference_update(dead)

async def broadcast_network_loop():
    while True:
        await broadcast_network_once()
        await asyncio.sleep(5.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    hw_task = asyncio.create_task(broadcast_hardware())
    net_task = asyncio.create_task(broadcast_network_loop())
    yield
    hw_task.cancel()
    net_task.cancel()
    await net_monitor.http_client.aclose()

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    clients.add(websocket)
    has_active_viewers.set() # Wake up the collector loop

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)

@app.get("/")
async def get_index():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/styles.css")
async def get_css():
    return FileResponse(BASE_DIR / "styles.css")

@app.get("/app.js")
async def get_js():
    return FileResponse(BASE_DIR / "app.js")

# --- Device Status & Target Management API ---

class TargetRequest(BaseModel):
    name: str = ""
    host: str
    method: str
    port: Optional[int] = None

def get_default_gateway_and_iface():
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if fields[1] == "00000000":
                    iface = fields[0]
                    gw_hex = fields[2]
                    gw_ip = socket.inet_ntoa(bytes.fromhex(gw_hex)[::-1])
                    return gw_ip, iface
    except Exception:
        pass
    return "Unknown", "Unknown"

def get_dns_resolvers():
    resolvers = []
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) > 1:
                        resolvers.append(parts[1])
    except Exception:
        pass
    return ", ".join(resolvers[:2]) if resolvers else "Unknown"

async def compute_device_status():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "Unknown"
    finally:
        s.close()

    gateway, active_iface = get_default_gateway_and_iface()
    dns_servers = get_dns_resolvers()

    public_ip = "Unknown"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("https://api.ipify.org")
            if resp.status_code == 200:
                public_ip = resp.text.strip()
    except Exception:
        pass

    return {
        "hostname": socket.gethostname(),
        "local_ip": local_ip,
        "public_ip": public_ip,
        "gateway": gateway,
        "interface": active_iface,
        "dns": dns_servers
    }

@app.get("/api/device-status", dependencies=secure_api)
async def get_device_status():
    return await compute_device_status()

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
    TARGETS_FILE.write_text(json.dumps(targets, indent=2))
    asyncio.create_task(broadcast_network_once())
    return new_target

@app.delete("/api/targets/{target_id}")
async def delete_target(target_id: str):
    targets = net_monitor.load_targets()
    filtered = [t for t in targets if str(t.get("id")) != str(target_id)]
    TARGETS_FILE.write_text(json.dumps(filtered, indent=2))
    asyncio.create_task(broadcast_network_once())
    return {"status": "deleted"}

# --- Diagnostics Streaming APIs (Traceroute & Discovery) ---

@app.post("/api/traceroute", dependencies=secure_api)
async def run_traceroute(payload: dict):
    host = payload.get("host", "").strip()
    clean_host = re.sub(r"^https?://", "", host).split("/")[0].split(":")[0]

    async def stream():
        if not clean_host:
            yield json.dumps({"type": "error", "text": "Host or IP address is required."}) + "\n"
            return
            
        if platform.system() == "Windows":
            cmd = ["tracert", "-d", "-h", "15", clean_host]
        elif shutil.which("traceroute"):
            cmd = ["traceroute", "-n", "-m", "15", clean_host]
        elif shutil.which("tracepath"):
            cmd = ["tracepath", "-n", "-m", "15", clean_host]
        else:
            yield json.dumps({"type": "error", "text": "Neither 'traceroute' nor 'tracepath' is installed. Run: sudo apt install traceroute"}) + "\n"
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            while True:
                line = await proc.stdout.readline()
                if not line: break
                text = line.decode('utf-8', errors='ignore').strip()
                if text:
                    yield json.dumps({"type": "line", "text": text}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")

@app.post("/api/discovery", dependencies=secure_api)
async def run_discovery(payload: dict):
    subnet = payload.get("subnet", "").strip()
    
    async def stream():
        try:
            net = ipaddress.ip_network(subnet, strict=False)
            if net.num_addresses > 256:
                yield json.dumps({"type": "error", "text": "Subnet too large (max 256 hosts). Please provide a /24 or smaller."}) + "\n"
                return
        except Exception as e:
            yield json.dumps({"type": "error", "text": f"Invalid CIDR subnet: {e}"}) + "\n"
            return

        yield json.dumps({"type": "line", "text": f"Sweeping {subnet} ({net.num_addresses} hosts)..."}) + "\n"
        sem = asyncio.Semaphore(25)
        
        async def ping_host(ip):
            async with sem:
                cmd = ["ping", "-n" if platform.system() == "Windows" else "-c", "1", "-W", "1", str(ip)]
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await proc.wait()
                return str(ip) if proc.returncode == 0 else None

        tasks = [asyncio.create_task(ping_host(ip)) for ip in net.hosts()]
        for f in asyncio.as_completed(tasks):
            res = await f
            if res:
                yield json.dumps({"type": "host", "ip": res}) + "\n"
                
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")

@app.post("/api/speedtest", dependencies=secure_api)
async def run_speedtest():
    async def stream():
        host = "speed.cloudflare.com"
        yield json.dumps({"type": "line", "text": f"Connecting to {host}..."}) + "\n"

        try:
            # 1. TCP Ping (Latency)
            yield json.dumps({"type": "line", "text": "Measuring latency..."}) + "\n"
            pings = []
            for _ in range(4):
                start = time.monotonic()
                try:
                    _, w = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=2.0)
                    pings.append((time.monotonic() - start) * 1000)
                    w.close()
                    await w.wait_closed()
                except Exception: 
                    pass
            
            ping_ms = sum(pings) / len(pings) if pings else 0
            yield json.dumps({"type": "ping", "ping_ms": ping_ms, "text": f"Ping: {ping_ms:.1f} ms"}) + "\n"

            # 2. Download (50MB payload)
            yield json.dumps({"type": "line", "text": "Testing download throughput..."}) + "\n"
            dl_bytes = 50_000_000
            start = time.monotonic()
            received = 0
            async with httpx.AsyncClient(verify=False) as client:
                async with client.stream("GET", f"https://{host}/__down?bytes={dl_bytes}") as resp:
                    async for chunk in resp.aiter_bytes(65536):
                        received += len(chunk)
                        elapsed = time.monotonic() - start
                        if elapsed > 0.1:  # Prevent divide-by-zero
                            mbps = (received * 8) / elapsed / 1_000_000
                            yield json.dumps({"type": "progress", "dir": "down", "mbps": mbps}) + "\n"
            
            dl_time = time.monotonic() - start
            dl_mbps = (received * 8) / dl_time / 1_000_000 if dl_time > 0 else 0
            yield json.dumps({"type": "result", "dir": "down", "mbps": dl_mbps, "text": f"Download: {dl_mbps:.1f} Mbps"}) + "\n"

            # 3. Upload (20MB payload)
            yield json.dumps({"type": "line", "text": "Testing upload throughput..."}) + "\n"
            ul_bytes = 20_000_000
            start = time.monotonic()
            sent = 0
            
            async def ul_gen():
                nonlocal sent
                chunk = os.urandom(65536)
                while sent < ul_bytes:
                    yield chunk
                    sent += len(chunk)
            
            async with httpx.AsyncClient(verify=False) as client:
                await client.post(f"https://{host}/__up", content=ul_gen())
            
            ul_time = time.monotonic() - start
            ul_mbps = (ul_bytes * 8) / ul_time / 1_000_000 if ul_time > 0 else 0
            yield json.dumps({"type": "result", "dir": "up", "mbps": ul_mbps, "text": f"Upload: {ul_mbps:.1f} Mbps"}) + "\n"

            yield json.dumps({"type": "done", "text": "Speedtest complete."}) + "\n"

        except Exception as e:
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"
    
    return StreamingResponse(stream(), media_type="application/x-ndjson")

if __name__ == "__main__":
    print(f"WindyDash Daemon running on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")