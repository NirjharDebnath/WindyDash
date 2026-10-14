"""
WindyDash - Active Network Probe Engine
Performs concurrent asynchronous ping, TCP, and HTTP availability and latency checks.
Caches targets in memory and only re-reads the target file when it changes on disk.
Includes standard library fallbacks if httpx is not installed.
"""
import asyncio
import json
import platform
import re
import time
import urllib.request
from typing import Dict, Any, List, Optional, Tuple
from config import TARGETS_FILE

try:
    import httpx
except ImportError:
    httpx = None

class NetworkMonitor:
    def __init__(self):
        # Persistent async HTTP client with connection pooling
        self.http_client = httpx.AsyncClient(verify=False, timeout=4.0, follow_redirects=True) if httpx else None
        self._cached_targets: List[Dict[str, Any]] = []
        self._last_targets_mtime: float = 0.0

    async def close(self):
        """Cleanly close client connections on daemon shutdown."""
        if self.http_client:
            await self.http_client.aclose()

    def load_targets(self) -> List[Dict[str, Any]]:
        """
        Load targets from disk with mtime caching.
        Avoids reading and parsing JSON from disk every probe cycle.
        """
        if not TARGETS_FILE.exists():
            default_targets = [
                {"id": "1", "name": "Cloudflare DNS", "host": "1.1.1.1", "method": "ping"},
                {"id": "2", "name": "Google DNS", "host": "8.8.8.8", "method": "tcp", "port": 53},
                {"id": "3", "name": "GitHub API", "host": "api.github.com", "method": "http"}
            ]
            try:
                TARGETS_FILE.write_text(json.dumps(default_targets, indent=2))
            except Exception:
                pass
            self._cached_targets = default_targets
            return default_targets

        try:
            mtime = TARGETS_FILE.stat().st_mtime
            if mtime != self._last_targets_mtime or not self._cached_targets:
                self._last_targets_mtime = mtime
                self._cached_targets = json.loads(TARGETS_FILE.read_text())
            return self._cached_targets
        except Exception:
            return self._cached_targets or []

    def save_targets(self, targets: List[Dict[str, Any]]):
        """Save updated target list to disk and update cache."""
        self._cached_targets = targets
        TARGETS_FILE.write_text(json.dumps(targets, indent=2))
        try:
            self._last_targets_mtime = TARGETS_FILE.stat().st_mtime
        except Exception:
            pass

    async def check_ping(self, host: str) -> Tuple[bool, Optional[float], str]:
        """Async non-blocking ICMP ping probe."""
        start = time.monotonic()
        is_windows = platform.system() == "Windows"
        cmd = ["ping", "-n" if is_windows else "-c", "1", "-W", "2", host]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            latency = (time.monotonic() - start) * 1000.0
            return (proc.returncode == 0, latency, "OK" if proc.returncode == 0 else "Unreachable")
        except Exception as e:
            return (False, None, str(e))

    async def check_tcp(self, host: str, port: int) -> Tuple[bool, Optional[float], str]:
        """Async TCP handshake probe."""
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
            writer.close()
            await writer.wait_closed()
            latency = (time.monotonic() - start) * 1000.0
            return (True, latency, f"Port {port} Open")
        except Exception as e:
            return (False, None, str(e))

    def _sync_http_probe(self, url: str) -> Tuple[bool, float, str]:
        """Synchronous HTTP probe using urllib as fallback."""
        start = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": "WindyDash"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            status = resp.getcode()
            latency = (time.monotonic() - start) * 1000.0
            return (status < 400, latency, f"HTTP {status}")

    async def check_http(self, host: str) -> Tuple[bool, Optional[float], str]:
        """Async HTTP(S) request probe with urllib fallback."""
        start = time.monotonic()
        url = host if host.startswith(("http://", "https://")) else f"https://{host}"
        
        if self.http_client:
            try:
                resp = await self.http_client.get(url)
                latency = (time.monotonic() - start) * 1000.0
                return (resp.status_code < 400, latency, f"HTTP {resp.status_code}")
            except Exception as e:
                if not host.startswith(("http://", "https://")):
                    try:
                        resp = await self.http_client.get(f"http://{host}")
                        latency = (time.monotonic() - start) * 1000.0
                        return (resp.status_code < 400, latency, f"HTTP {resp.status_code}")
                    except Exception:
                        pass
                return (False, None, str(e))
        else:
            try:
                return await asyncio.to_thread(self._sync_http_probe, url)
            except Exception as e:
                return (False, None, str(e))

    async def probe_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        """Probe an individual target according to its configured method."""
        host = target.get("host", "").strip()
        method = (target.get("method") or "ping").lower()
        clean_host = re.sub(r"^https?://", "", host).split("/")[0].split(":")[0]

        success, latency, msg = False, None, "Unknown"
        if method == "ping":
            success, latency, msg = await self.check_ping(clean_host)
        elif method == "tcp":
            port = int(target.get("port") or 80)
            success, latency, msg = await self.check_tcp(clean_host, port)
        elif method == "http":
            success, latency, msg = await self.check_http(host)

        return {
            "id": str(target.get("id")),
            "name": target.get("name") or host,
            "method": method.upper(),
            "success": success,
            "latency_ms": round(latency, 1) if latency is not None else None,
            "message": msg
        }

    async def run_checks(self) -> Dict[str, Any]:
        """Run all target probes concurrently."""
        targets = self.load_targets()
        if not targets:
            return {"type": "network_targets", "results": []}

        results = await asyncio.gather(*(self.probe_target(t) for t in targets), return_exceptions=False)
        return {"type": "network_targets", "results": list(results)}
