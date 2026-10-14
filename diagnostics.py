"""
WindyDash - Network Diagnostics & Benchmarks
Implements live streaming NDJSON diagnostics:
- Streaming Traceroute
- CIDR Subnet Ping Sweep
- DNS Record Resolver (A, AAAA, CNAME, PTR)
- Port Reachability Checker (TCP handshake)
- Cloudflare Bandwidth & Latency Benchmark
- Host Network Snapshot
"""
import asyncio
import ipaddress
import json
import platform
import re
import shutil
import socket
import time
import urllib.request
from typing import AsyncGenerator, Dict, Any, Tuple

try:
    import httpx
except ImportError:
    httpx = None

# Pre-allocated 64KB static payload buffer to eliminate CPU entropy burn during speedtests
STATIC_CHUNK_64K = b"0" * 65536

def get_default_gateway_and_iface() -> Tuple[str, str]:
    """Extract default IPv4 gateway and interface from /proc/net/route."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    iface = fields[0]
                    gw_hex = fields[2]
                    gw_ip = socket.inet_ntoa(bytes.fromhex(gw_hex)[::-1])
                    return gw_ip, iface
    except Exception:
        pass
    return "Unknown", "Unknown"

def get_dns_resolvers() -> str:
    """Read primary DNS nameservers from /etc/resolv.conf."""
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

async def compute_device_status(http_client: Any) -> Dict[str, Any]:
    """Compute local IP, gateway, DNS, and external public IP."""
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
        if http_client:
            resp = await http_client.get("https://api.ipify.org", timeout=2.5)
            if resp.status_code == 200:
                public_ip = resp.text.strip()
        else:
            def fetch_ip():
                req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "WindyDash"})
                with urllib.request.urlopen(req, timeout=2.5) as r:
                    return r.read().decode("utf-8").strip()
            public_ip = await asyncio.to_thread(fetch_ip)
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

async def stream_traceroute(clean_host: str) -> AsyncGenerator[str, None]:
    """Execute traceroute and stream output line by line."""
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

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                yield json.dumps({"type": "line", "text": text}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"
    except asyncio.CancelledError:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        raise
    except Exception as e:
        yield json.dumps({"type": "error", "text": str(e)}) + "\n"

async def stream_discovery(subnet: str) -> AsyncGenerator[str, None]:
    """Scan a subnet (up to /24) concurrently with bounded semaphore."""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
        if net.num_addresses > 256:
            yield json.dumps({"type": "error", "text": "Subnet too large (max 256 hosts). Please provide a /24 or smaller."}) + "\n"
            return
    except Exception as e:
        yield json.dumps({"type": "error", "text": f"Invalid CIDR subnet: {e}"}) + "\n"
        return

    yield json.dumps({"type": "line", "text": f"Sweeping {subnet} ({net.num_addresses} hosts)..."}) + "\n"
    sem = asyncio.Semaphore(20)
    is_windows = platform.system() == "Windows"

    async def ping_host(ip):
        async with sem:
            cmd = ["ping", "-n" if is_windows else "-c", "1", "-W", "1", str(ip)]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                return str(ip) if proc.returncode == 0 else None
            except Exception:
                return None

    tasks = [asyncio.create_task(ping_host(ip)) for ip in net.hosts()]
    try:
        for f in asyncio.as_completed(tasks):
            res = await f
            if res:
                yield json.dumps({"type": "host", "ip": res}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise

async def stream_dns_lookup(domain: str) -> AsyncGenerator[str, None]:
    """Live DNS record resolver without external dependencies."""
    clean_domain = domain.strip().lower()
    clean_domain = re.sub(r"^https?://", "", clean_domain).split("/")[0].split(":")[0]

    if not clean_domain:
        yield json.dumps({"type": "error", "text": "Domain name or hostname is required."}) + "\n"
        return

    yield json.dumps({"type": "line", "text": f"Querying DNS records for '{clean_domain}'..."}) + "\n"

    def resolve():
        records = []
        try:
            addr_info = socket.getaddrinfo(clean_domain, None)
            for r in addr_info:
                fam, _, _, _, sockaddr = r
                ip = sockaddr[0]
                rtype = "AAAA (IPv6)" if fam == socket.AF_INET6 else "A (IPv4)"
                records.append((rtype, ip))
        except Exception:
            pass

        try:
            fqdn = socket.getfqdn(clean_domain)
            if fqdn and fqdn != clean_domain:
                records.append(("FQDN / Alias", fqdn))
        except Exception:
            pass

        try:
            rev = socket.gethostbyaddr(clean_domain)
            if rev and rev[0]:
                records.append(("PTR (Reverse)", rev[0]))
        except Exception:
            pass

        return list(dict.fromkeys(records))

    try:
        results = await asyncio.to_thread(resolve)
        if not results:
            yield json.dumps({"type": "error", "text": f"Could not resolve any DNS records for '{clean_domain}'."}) + "\n"
        else:
            for rtype, val in results:
                yield json.dumps({"type": "dns_record", "rtype": rtype, "value": val, "text": f"[{rtype}] {val}"}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"
    except Exception as e:
        yield json.dumps({"type": "error", "text": str(e)}) + "\n"

async def stream_port_check(host: str, port: int) -> AsyncGenerator[str, None]:
    """Non-blocking TCP port availability test with response latency."""
    clean_host = host.strip()
    clean_host = re.sub(r"^https?://", "", clean_host).split("/")[0].split(":")[0]

    if not clean_host or not port or port < 1 or port > 65535:
        yield json.dumps({"type": "error", "text": "Valid Host and Port (1-65535) are required."}) + "\n"
        return

    yield json.dumps({"type": "line", "text": f"Checking TCP port {port} on {clean_host}..."}) + "\n"
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(clean_host, port), timeout=3.5)
        latency = (time.monotonic() - start) * 1000.0
        writer.close()
        await writer.wait_closed()
        yield json.dumps({
            "type": "port_result",
            "open": True,
            "port": port,
            "host": clean_host,
            "latency_ms": round(latency, 1),
            "text": f"Port {port} is OPEN (Handshake: {latency:.1f} ms)"
        }) + "\n"
    except asyncio.TimeoutError:
        yield json.dumps({
            "type": "port_result",
            "open": False,
            "port": port,
            "host": clean_host,
            "text": f"Port {port} TIMED OUT after 3.5s (filtered or firewall blocked)"
        }) + "\n"
    except Exception as e:
        yield json.dumps({
            "type": "port_result",
            "open": False,
            "port": port,
            "host": clean_host,
            "text": f"Port {port} is CLOSED or UNREACHABLE: {e}"
        }) + "\n"

    yield json.dumps({"type": "done"}) + "\n"

async def stream_speedtest(http_client: Any) -> AsyncGenerator[str, None]:
    """Benchmark network latency, download, and upload speeds against Cloudflare."""
    host = "speed.cloudflare.com"
    yield json.dumps({"type": "line", "text": f"Connecting to {host}..."}) + "\n"

    try:
        # 1. Latency Measurement (TCP handshake)
        yield json.dumps({"type": "line", "text": "Measuring latency..."}) + "\n"
        pings = []
        for _ in range(4):
            start = time.monotonic()
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=2.0)
                pings.append((time.monotonic() - start) * 1000.0)
                w.close()
                await w.wait_closed()
            except Exception:
                pass

        ping_ms = sum(pings) / len(pings) if pings else 0.0
        yield json.dumps({"type": "ping", "ping_ms": ping_ms, "text": f"Ping: {ping_ms:.1f} ms"}) + "\n"

        # 2. Download Throughput (50MB payload)
        yield json.dumps({"type": "line", "text": "Testing download throughput..."}) + "\n"
        dl_bytes = 50_000_000
        start = time.monotonic()
        received = 0

        if http_client:
            async with http_client.stream("GET", f"https://{host}/__down?bytes={dl_bytes}") as resp:
                async for chunk in resp.aiter_bytes(65536):
                    received += len(chunk)
                    elapsed = time.monotonic() - start
                    if elapsed > 0.15:
                        mbps = (received * 8.0) / elapsed / 1_000_000.0
                        yield json.dumps({"type": "progress", "dir": "down", "mbps": mbps}) + "\n"
        else:
            def fetch_stream():
                nonlocal received
                req = urllib.request.Request(f"https://{host}/__down?bytes={dl_bytes}")
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        received += len(chunk)

            await asyncio.to_thread(fetch_stream)

        dl_time = time.monotonic() - start
        dl_mbps = (received * 8.0) / dl_time / 1_000_000.0 if dl_time > 0 else 0.0
        yield json.dumps({"type": "result", "dir": "down", "mbps": dl_mbps, "text": f"Download: {dl_mbps:.1f} Mbps"}) + "\n"

        # 3. Upload Throughput (20MB payload using zero-CPU static chunks)
        yield json.dumps({"type": "line", "text": "Testing upload throughput..."}) + "\n"
        ul_bytes = 20_000_000
        start = time.monotonic()
        sent = 0

        async def ul_gen():
            nonlocal sent
            chunk_len = len(STATIC_CHUNK_64K)
            while sent < ul_bytes:
                yield STATIC_CHUNK_64K
                sent += chunk_len

        if http_client:
            await http_client.post(f"https://{host}/__up", content=ul_gen())
        else:
            def upload_post():
                req = urllib.request.Request(
                    f"https://{host}/__up",
                    data=STATIC_CHUNK_64K * (ul_bytes // len(STATIC_CHUNK_64K)),
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    pass
            await asyncio.to_thread(upload_post)

        ul_time = time.monotonic() - start
        ul_mbps = (ul_bytes * 8.0) / ul_time / 1_000_000.0 if ul_time > 0 else 0.0
        yield json.dumps({"type": "result", "dir": "up", "mbps": ul_mbps, "text": f"Upload: {ul_mbps:.1f} Mbps"}) + "\n"

        yield json.dumps({"type": "done", "text": "Speedtest complete."}) + "\n"

    except Exception as e:
        yield json.dumps({"type": "error", "text": str(e)}) + "\n"
