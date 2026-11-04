"""
WindyDash - Optimized Linux Hardware Telemetry Collector
Engineered for minimal CPU and memory footprints.
Uses caching, sysfs path pre-resolution, and decoupled sampling intervals.
"""
import asyncio
import json
import os
import platform
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil

def read_text(path: Path) -> str:
    """Safely read text from a sysfs or procfs path."""
    try:
        return path.read_text(errors="ignore").strip()
    except Exception:
        return ""

def human_time(seconds: float) -> str:
    """Format seconds into human-readable duration string."""
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, r = divmod(r, 60)
    return (f"{d}d " if d else "") + (f"{h}h {m}m" if h else f"{m}m {r}s")


class HardwareMonitor:
    def __init__(self):
        # Warm up psutil CPU calculations
        psutil.cpu_percent(None, percpu=True)
        self.prev_net = psutil.net_io_counters(pernic=True)
        self.prev_disk = psutil.disk_io_counters(perdisk=True)
        self.session_start_net = psutil.net_io_counters()
        self.prev_time = time.monotonic()

        # GPU detection
        self.gpu_type = self._detect_gpu()

        # Decoupled cadences & caches
        self.last_proc_sample = 0.0
        self.cached_proc: List[Dict[str, Any]] = []

        self.last_gpu_sample = 0.0
        self.cached_gpu: List[Dict[str, Any]] = []

        self.last_periph_sample = 0.0
        self.cached_periph: Dict[str, Any] = {"displays": [], "usb": [], "thunderbolt": []}

        self.last_fs_sample = 0.0
        self.cached_fs: List[Dict[str, Any]] = []

        # Sensor topology cache (path & label cache to avoid re-globbing every tick)
        self.last_sensor_scan = 0.0
        self.cached_sensor_descriptors: List[Dict[str, Any]] = []
        self.last_sensor_sample = 0.0
        self.cached_sensors: Dict[str, Any] = {}

        # Permanent CPU hardware specifications cache (probed once at startup)
        self.cached_lscpu_details: Dict[str, Any] = self._collect_lscpu_details()

        # Permanent Memory & DIMM hardware specifications cache (probed once at startup)
        self.cached_memory_hardware: Dict[str, Any] = self._collect_dmi_memory_details()

        # Permanent Physical Block Devices & NVMe topology cache (probed once at startup)
        self.cached_physical_disks: List[Dict[str, Any]] = self._collect_physical_disks()

        # Permanent OS Distribution & platform specifications cache (probed once at startup)
        self.cached_os_details: Dict[str, Any] = self._collect_os_details()

    def _collect_os_details(self) -> Dict[str, Any]:
        """
        Probe Linux distribution release metadata, kernel build, and system runtime info.
        Cached permanently at startup to incur 0.0ms telemetry overhead.
        """
        info = {
            "distro_id": "linux",
            "distro_name": "Linux",
            "distro_version": "",
            "pretty_name": "Linux",
            "codename": "",
            "id_like": "",
            "logo": "",
            "system": platform.system().lower(),
            "kernel": platform.release(),
            "kernel_version": platform.version(),
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
            "libc": "glibc",
            "init_system": "systemd",
            "timezone": "UTC",
            "hardware_model": ""
        }

        try:
            os_rel = platform.freedesktop_os_release()
            info["distro_id"] = os_rel.get("ID", "linux").lower()
            info["distro_name"] = os_rel.get("NAME", "Linux")
            info["distro_version"] = os_rel.get("VERSION_ID", "")
            info["pretty_name"] = os_rel.get("PRETTY_NAME", os_rel.get("NAME", "Linux"))
            info["codename"] = os_rel.get("VERSION_CODENAME", "")
            info["id_like"] = os_rel.get("ID_LIKE", os_rel.get("ID", ""))
            info["logo"] = os_rel.get("LOGO", "")
        except Exception:
            p = Path("/etc/os-release")
            if p.exists():
                for line in p.read_text(errors="ignore").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        v = v.strip("\"'")
                        if k == "PRETTY_NAME":
                            info["pretty_name"] = v
                        elif k == "NAME" and info["distro_name"] == "Linux":
                            info["distro_name"] = v
                        elif k == "ID":
                            info["distro_id"] = v.lower()
                        elif k == "VERSION_ID":
                            info["distro_version"] = v
                        elif k == "VERSION_CODENAME":
                            info["codename"] = v
                        elif k == "ID_LIKE":
                            info["id_like"] = v
                        elif k == "LOGO":
                            info["logo"] = v

        try:
            libc_ver = platform.libc_ver()
            if libc_ver and libc_ver[0]:
                info["libc"] = f"{libc_ver[0]} {libc_ver[1]}"
        except Exception:
            pass

        try:
            tz_path = Path("/etc/timezone")
            if tz_path.exists():
                info["timezone"] = tz_path.read_text(errors="ignore").strip()
            else:
                localtime_target = os.path.realpath("/etc/localtime")
                if "zoneinfo/" in localtime_target:
                    info["timezone"] = localtime_target.split("zoneinfo/")[-1]
                else:
                    info["timezone"] = time.tzname[0]
        except Exception:
            info["timezone"] = time.tzname[0] if time.tzname else "UTC"

        for dmi_file in ["/sys/class/dmi/id/product_name", "/sys/class/dmi/id/product_version", "/sys/devices/virtual/dmi/id/product_name"]:
            p = Path(dmi_file)
            if p.exists():
                m_txt = read_text(p)
                if m_txt and m_txt.lower() not in ("none", "unknown", "to be filled by o.e.m."):
                    info["hardware_model"] = m_txt
                    break

        return info

    def _collect_lscpu_details(self) -> Dict[str, Any]:
        """
        Probe and parse detailed CPU topology, cache hierarchy, and features from lscpu.
        Cached permanently on startup so it incurs 0.0ms overhead during routine telemetry loops.
        """
        flat = {}
        flags_str = ""

        # 1. Try lscpu -J (structured JSON)
        try:
            raw = subprocess.check_output(["lscpu", "-J"], text=True, timeout=2.0)
            data = json.loads(raw)
            def walk(items):
                nonlocal flags_str
                for item in items:
                    f = item.get("field", "").rstrip(":").strip()
                    d = item.get("data")
                    if f and d is not None:
                        flat[f] = str(d).strip()
                        if f.lower() == "flags":
                            flags_str = str(d).strip()
                    if "children" in item:
                        walk(item["children"])
            walk(data.get("lscpu", []))
        except Exception:
            # 2. Fallback to standard lscpu text output
            try:
                raw = subprocess.check_output(["lscpu"], text=True, timeout=2.0)
                for line in raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        flat[k.strip()] = v.strip()
                        if k.strip().lower() == "flags":
                            flags_str = v.strip()
            except Exception:
                pass

        # 3. Fallback to /proc/cpuinfo if model name or cores missing
        if not flat.get("Model name"):
            cpuinfo = Path("/proc/cpuinfo")
            if cpuinfo.exists():
                try:
                    for line in cpuinfo.read_text(errors="ignore").splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip()
                            v = v.strip()
                            if k == "model name" and not flat.get("Model name"):
                                flat["Model name"] = v
                            elif k == "flags" and not flags_str:
                                flags_str = v
                            elif k == "cpu cores" and not flat.get("Core(s) per socket"):
                                flat["Core(s) per socket"] = v
                except Exception:
                    pass

        # Filter prominent instruction extensions
        notable_flags = ["avx512", "avx2", "avx", "fma", "aes", "sha_ni", "sse4_2", "sse4_1", "vmx", "svm", "rdrand", "bmi2"]
        found_flags = []
        lower_flags = flags_str.lower().split()
        for nf in notable_flags:
            if any(nf in f for f in lower_flags):
                found_flags.append(nf.upper().replace("_", "-"))

        # Extract vulnerability stats
        vuln_not_affected = 0
        vuln_mitigated = 0
        for k, v in flat.items():
            if "vulnerability" in k.lower():
                if "not affected" in v.lower():
                    vuln_not_affected += 1
                elif "mitigation" in v.lower():
                    vuln_mitigated += 1

        vuln_summary = f"{vuln_not_affected} immune, {vuln_mitigated} mitigated" if (vuln_not_affected or vuln_mitigated) else "Protected"

        model_name = flat.get("Model name") or flat.get("Architecture") or platform.processor() or "Generic Processor"
        freq_max = flat.get("CPU max MHz", "")
        if freq_max:
            try:
                f_val = float(freq_max)
                freq_max_str = f"{f_val / 1000.0:.2f} GHz" if f_val >= 1000 else f"{f_val:.0f} MHz"
            except Exception:
                freq_max_str = f"{freq_max} MHz"
        else:
            freq_max_str = "N/A"

        freq_min = flat.get("CPU min MHz", "")
        if freq_min:
            try:
                f_val = float(freq_min)
                freq_min_str = f"{f_val:.0f} MHz"
            except Exception:
                freq_min_str = f"{freq_min} MHz"
        else:
            freq_min_str = "N/A"

        return {
            "model_name": model_name,
            "vendor_id": flat.get("Vendor ID", ""),
            "architecture": flat.get("Architecture", platform.machine()),
            "op_modes": flat.get("CPU op-mode(s)", "64-bit"),
            "address_sizes": flat.get("Address sizes", ""),
            "byte_order": flat.get("Byte Order", "Little Endian"),
            "family": flat.get("CPU family", ""),
            "model": flat.get("Model", ""),
            "stepping": flat.get("Stepping", ""),
            "threads_per_core": flat.get("Thread(s) per core", "1"),
            "cores_per_socket": flat.get("Core(s) per socket", str(psutil.cpu_count(False) or 1)),
            "sockets": flat.get("Socket(s)", "1"),
            "total_cpus": flat.get("CPU(s)", str(psutil.cpu_count(True) or 1)),
            "freq_boost": flat.get("Frequency boost", "enabled").title(),
            "freq_max": freq_max_str,
            "freq_min": freq_min_str,
            "bogomips": flat.get("BogoMIPS", ""),
            "virtualization": flat.get("Virtualization", "AMD-V" if "svm" in lower_flags else ("VT-x" if "vmx" in lower_flags else "None")),
            "l1d": flat.get("L1d cache", flat.get("L1d", "N/A")),
            "l1i": flat.get("L1i cache", flat.get("L1i", "N/A")),
            "l2": flat.get("L2 cache", flat.get("L2", "N/A")),
            "l3": flat.get("L3 cache", flat.get("L3", "N/A")),
            "numa_nodes": flat.get("NUMA node(s)", "1"),
            "key_flags": found_flags[:8],
            "vuln_summary": vuln_summary
        }

    def _collect_dmi_memory_details(self) -> Dict[str, Any]:
        """
        Probe and parse physical memory topology, DIMM slots, clock speeds, and hardware specs.
        Attempts native dmidecode, then non-blocking sudo -n dmidecode, then lshw.
        Cached permanently at startup so it incurs 0.0ms overhead in live telemetry loops.
        """
        fallback_res = {
            "available": False,
            "requires_root": True,
            "error": "Root or sudo privileges required to inspect SMBIOS physical memory tables.",
            "source": "none",
            "array": {
                "max_capacity": "N/A",
                "total_slots": 0,
                "installed_slots": 0,
                "ecc_type": "None",
                "location": "System Board",
                "use": "System Memory"
            },
            "slots": [],
            "summary": {
                "technology": "System Memory",
                "speed": "N/A",
                "channels": "N/A",
                "total_installed_bytes": 0,
                "total_installed_human": "N/A"
            }
        }

        # 1. Attempt dmidecode directly, then via non-interactive sudo
        dmi_cmds = [
            ["dmidecode", "-t", "16,17"],
            ["sudo", "-n", "dmidecode", "-t", "16,17"]
        ]
        raw_dmi = ""
        for cmd in dmi_cmds:
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
                if p.returncode == 0 and ("DMI type 16" in p.stdout or "DMI type 17" in p.stdout or "Memory Device" in p.stdout):
                    raw_dmi = p.stdout
                    break
            except Exception:
                pass

        if raw_dmi:
            try:
                parsed = self._parse_dmi_memory(raw_dmi)
                parsed["available"] = True
                parsed["requires_root"] = False
                parsed["error"] = ""
                parsed["source"] = "dmidecode"
                return parsed
            except Exception:
                pass

        # 2. Fallback to lshw (supports -json)
        lshw_cmds = [
            ["lshw", "-C", "memory", "-json"],
            ["sudo", "-n", "lshw", "-C", "memory", "-json"]
        ]
        for cmd in lshw_cmds:
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
                if p.returncode == 0 and p.stdout.strip():
                    parsed_lshw = self._parse_lshw_memory(p.stdout)
                    if parsed_lshw and parsed_lshw.get("slots"):
                        parsed_lshw["available"] = True
                        parsed_lshw["requires_root"] = False
                        parsed_lshw["error"] = ""
                        parsed_lshw["source"] = "lshw"
                        return parsed_lshw
            except Exception:
                pass

        return fallback_res

    def _parse_dmi_memory(self, raw_text: str) -> Dict[str, Any]:
        """Parse raw text from dmidecode -t 16,17 into structured memory hardware topology."""
        array_info = {
            "max_capacity": "N/A",
            "total_slots": 0,
            "installed_slots": 0,
            "ecc_type": "None",
            "location": "System Board",
            "use": "System Memory"
        }
        slots = []

        records = raw_text.split("Handle 0x")
        for rec in records:
            if not rec.strip():
                continue
            lines = rec.strip().splitlines()
            first_line = lines[0] if lines else ""
            title = lines[1].strip() if len(lines) > 1 else ""

            kv = {}
            for line in lines[2:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    kv[k.strip()] = v.strip()

            if "DMI type 16" in first_line or title == "Physical Memory Array":
                if "Maximum Capacity" in kv:
                    array_info["max_capacity"] = kv["Maximum Capacity"]
                if "Number Of Devices" in kv:
                    try:
                        array_info["total_slots"] = int(kv["Number Of Devices"])
                    except Exception:
                        pass
                if "Error Correction Type" in kv:
                    array_info["ecc_type"] = kv["Error Correction Type"]
                if "Location" in kv:
                    array_info["location"] = kv["Location"]
                if "Use" in kv:
                    array_info["use"] = kv["Use"]

            elif "DMI type 17" in first_line or title == "Memory Device":
                size_raw = kv.get("Size", "")
                is_empty = (
                    not size_raw or
                    "no module" in size_raw.lower() or
                    size_raw.lower() in ("none", "unknown", "0 mb", "0 b")
                )

                size_bytes = 0
                if not is_empty:
                    try:
                        parts = size_raw.split()
                        val = float(parts[0])
                        unit = parts[1].upper() if len(parts) > 1 else "MB"
                        if "G" in unit:
                            size_bytes = int(val * 1024 * 1024 * 1024)
                        elif "M" in unit:
                            size_bytes = int(val * 1024 * 1024)
                        elif "K" in unit:
                            size_bytes = int(val * 1024)
                    except Exception:
                        pass

                speed_val = kv.get("Configured Memory Speed") or kv.get("Configured Clock Speed") or kv.get("Speed", "")
                if speed_val.lower() in ("unknown", "none"):
                    speed_val = kv.get("Speed", "")
                if speed_val.lower() in ("unknown", "none"):
                    speed_val = "N/A"

                mfr = kv.get("Manufacturer", "")
                if mfr.lower() in ("not specified", "unknown", "none", "0x0000"):
                    mfr = ""

                part_no = kv.get("Part Number", "")
                if part_no.lower() in ("not specified", "unknown", "none"):
                    part_no = ""

                serial_no = kv.get("Serial Number", "")
                if serial_no.lower() in ("not specified", "unknown", "none"):
                    serial_no = ""

                mem_type = kv.get("Type", "")
                if mem_type.lower() in ("unknown", "none"):
                    mem_type = "N/A"

                form_factor = kv.get("Form Factor", "")
                if form_factor.lower() in ("unknown", "none"):
                    form_factor = "DIMM"

                slot_name = kv.get("Locator") or f"Slot {len(slots)}"
                bank = kv.get("Bank Locator", "")

                slots.append({
                    "slot_name": slot_name,
                    "bank": bank,
                    "installed": not is_empty,
                    "size": "Empty" if is_empty else size_raw,
                    "size_bytes": size_bytes,
                    "type": mem_type,
                    "type_detail": kv.get("Type Detail", ""),
                    "speed": speed_val,
                    "configured_speed": kv.get("Configured Memory Speed") or kv.get("Configured Clock Speed") or speed_val,
                    "manufacturer": mfr,
                    "part_number": part_no,
                    "serial_number": serial_no,
                    "form_factor": form_factor,
                    "rank": kv.get("Rank", ""),
                    "voltage": kv.get("Configured Voltage") or kv.get("Minimum Voltage", "")
                })

        installed_count = sum(1 for s in slots if s["installed"])
        if not array_info["total_slots"] and slots:
            array_info["total_slots"] = len(slots)
        array_info["installed_slots"] = installed_count

        total_bytes = sum(s["size_bytes"] for s in slots if s["installed"])
        total_gb = total_bytes / (1024**3)
        total_human = f"{total_gb:.1f} GB".replace(".0 GB", " GB") if total_gb > 0 else "N/A"

        tech = "Standard"
        speed = "N/A"
        for s in slots:
            if s["installed"]:
                if s["type"] != "N/A":
                    tech = s["type"]
                if s["speed"] != "N/A":
                    speed = s["speed"]
                break

        if installed_count >= 4:
            channels = "Quad Channel" if any("d" in s["slot_name"].lower() for s in slots) else "Dual Channel"
        elif installed_count >= 2:
            channels = "Dual Channel"
        elif installed_count == 1:
            channels = "Single Channel"
        else:
            channels = "N/A"

        return {
            "array": array_info,
            "slots": slots,
            "summary": {
                "technology": tech,
                "speed": speed,
                "channels": channels,
                "total_installed_bytes": total_bytes,
                "total_installed_human": total_human
            }
        }

    def _parse_lshw_memory(self, raw_json: str) -> Dict[str, Any]:
        """Fallback parser for lshw -C memory -json output."""
        try:
            data = json.loads(raw_json)
        except Exception:
            return {}

        nodes = data if isinstance(data, list) else [data]
        slots = []
        total_mem_bytes = 0

        def walk(item):
            nonlocal total_mem_bytes
            cid = str(item.get("id", ""))
            cls = str(item.get("class", ""))
            size = item.get("size", 0)

            if cid.startswith("bank:") or (cls == "memory" and "slot" in item):
                installed = bool(size and size > 0)
                if installed:
                    total_mem_bytes += size
                desc = str(item.get("description", ""))
                tech = "DDR4" if "ddr4" in desc.lower() else ("DDR5" if "ddr5" in desc.lower() else ("DDR3" if "ddr3" in desc.lower() else "DIMM"))
                clk = item.get("clock", 0)
                speed_str = f"{clk // 1000000} MT/s" if clk > 1000000 else "N/A"
                size_gb = size / (1024**3)
                size_str = f"{size_gb:.1f} GB".replace(".0 GB", " GB") if size > 0 else "Empty"

                slots.append({
                    "slot_name": item.get("slot") or cid,
                    "bank": item.get("physid", ""),
                    "installed": installed,
                    "size": size_str,
                    "size_bytes": size,
                    "type": tech,
                    "type_detail": desc,
                    "speed": speed_str,
                    "configured_speed": speed_str,
                    "manufacturer": item.get("vendor", ""),
                    "part_number": item.get("product", ""),
                    "serial_number": item.get("serial", ""),
                    "form_factor": "SODIMM" if "sodimm" in desc.lower() else "DIMM",
                    "rank": "",
                    "voltage": ""
                })

            for child in item.get("children", []):
                walk(child)

        for n in nodes:
            walk(n)

        if not slots:
            return {}

        installed_count = sum(1 for s in slots if s["installed"])
        total_gb = total_mem_bytes / (1024**3)
        return {
            "array": {
                "max_capacity": f"{total_gb:.1f} GB".replace(".0 GB", " GB"),
                "total_slots": len(slots),
                "installed_slots": installed_count,
                "ecc_type": "None",
                "location": "System Board",
                "use": "System Memory"
            },
            "slots": slots,
            "summary": {
                "technology": slots[0]["type"] if slots else "Standard",
                "speed": slots[0]["speed"] if slots else "N/A",
                "channels": "Dual Channel" if installed_count >= 2 else ("Single Channel" if installed_count == 1 else "N/A"),
                "total_installed_bytes": total_mem_bytes,
                "total_installed_human": f"{total_gb:.1f} GB".replace(".0 GB", " GB")
            }
        }

    def _detect_gpu(self) -> Optional[str]:
        """Detect presence of dedicated GPU (NVIDIA)."""
        try:
            subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return "nvidia"
        except Exception:
            return None

    def _sample_gpu(self) -> List[Dict[str, Any]]:
        """Fetch NVIDIA GPU metrics via nvidia-smi."""
        if not self.gpu_type:
            return []
        try:
            q = (
                "name,driver_version,vbios_version,pci.bus_id,"
                "utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,"
                "memory.total,memory.used,memory.free,temperature.gpu,power.draw,"
                "clocks.current.graphics,clocks.current.memory"
            )
            out = subprocess.check_output(
                ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                text=True, timeout=1.0
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

    def _refresh_sensor_descriptors(self):
        """Scan /sys/class/hwmon topology and cache sensor paths and labels."""
        descriptors = []
        hwmon_dir = Path("/sys/class/hwmon")
        if not hwmon_dir.exists():
            self.cached_sensor_descriptors = []
            return

        for hw in hwmon_dir.glob("hwmon*"):
            name = read_text(hw / "name") or hw.name
            # Temperatures
            for p in hw.glob("temp*_input"):
                label_p = p.with_name(p.name.replace("_input", "_label"))
                label = read_text(label_p) if label_p.exists() else p.stem
                descriptors.append({
                    "id": f"{name}:{label}",
                    "type": "temperature",
                    "label": label,
                    "path": p,
                    "scale": 0.001
                })
            # Fans
            for p in hw.glob("fan*_input"):
                label_p = p.with_name(p.name.replace("_input", "_label"))
                label = read_text(label_p) if label_p.exists() else p.stem
                descriptors.append({
                    "id": f"{name}:{label}",
                    "type": "fan",
                    "label": label,
                    "path": p,
                    "scale": 1.0
                })
            # Voltages
            for p in hw.glob("in*_input"):
                label_p = p.with_name(p.name.replace("_input", "_label"))
                label = read_text(label_p) if label_p.exists() else p.stem
                descriptors.append({
                    "id": f"{name}:{label}",
                    "type": "voltage",
                    "label": label,
                    "path": p,
                    "scale": 0.001
                })

        self.cached_sensor_descriptors = descriptors

    def sample_sensors(self) -> Dict[str, Dict[str, Any]]:
        """Fast sensor reading: reads values from cached paths without globbing."""
        now = time.monotonic()
        if now - self.last_sensor_sample < 2.0 and self.cached_sensors:
            return self.cached_sensors

        self.last_sensor_sample = now
        if now - self.last_sensor_scan >= 60.0 or not self.cached_sensor_descriptors:
            self.last_sensor_scan = now
            self._refresh_sensor_descriptors()

        results = {}
        for s in self.cached_sensor_descriptors:
            try:
                raw = s["path"].read_text().strip()
                val = float(raw) * s["scale"]
                results[s["id"]] = {
                    "type": s["type"],
                    "label": s["label"],
                    "value": round(val, 1) if s["type"] == "temperature" else (int(val) if s["type"] == "fan" else round(val, 3))
                }
            except Exception:
                pass
        self.cached_sensors = results
        return results

    def sample_batteries(self) -> List[Dict[str, Any]]:
        """Sample batteries from /sys/class/power_supply/BAT*."""
        out = []
        ps_dir = Path("/sys/class/power_supply")
        if not ps_dir.exists():
            return out

        for b in ps_dir.glob("BAT*"):
            def val(k: str) -> Optional[float]:
                try:
                    return float(read_text(b / k))
                except Exception:
                    return None

            e_design = val("energy_full_design") or val("charge_full_design")
            e_full = val("energy_full") or val("charge_full")
            e_now = val("energy_now") or val("charge_now")
            voltage = val("voltage_now")
            power = val("power_now") or val("current_now")

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

    def sample_filesystems(self) -> List[Dict[str, Any]]:
        """Sample mounted disk partitions at an optimized 8-second interval."""
        now = time.monotonic()
        if now - self.last_fs_sample < 8.0 and self.cached_fs:
            return self.cached_fs

        self.last_fs_sample = now
        result = []
        seen_devices = {}
        for part in psutil.disk_partitions(all=False):
            dev = part.device
            mp = part.mountpoint
            fstype = (part.fstype or "").lower()

            # Filter out virtual loopback devices (e.g. Ubuntu snaps, squashfs images)
            if dev.startswith("/dev/loop") or dev.startswith("loop") or fstype == "squashfs" or mp.startswith("/snap"):
                continue

            # Filter out pseudo filesystems
            if fstype in ("overlay", "iso9660", "ramfs"):
                continue

            # Filter out nested container/package manager bind-mounts
            if any(x in mp for x in ("/.cache", "/.config", "/.docker", "/.npm", "/.nvm", "/docker/", "/containerd/")):
                continue

            # If device already seen (e.g. identical partition bind-mounted multiple times), keep the root or shortest canonical mount
            if dev in seen_devices:
                old_idx = seen_devices[dev]
                old_mp = result[old_idx]["mountpoint"]
                if mp == "/":
                    result[old_idx]["mountpoint"] = mp
                elif old_mp != "/" and len(mp.split("/")) < len(old_mp.split("/")):
                    result[old_idx]["mountpoint"] = mp
                continue

            try:
                u = psutil.disk_usage(mp)
                seen_devices[dev] = len(result)
                result.append({
                    "device": part.device,
                    "mountpoint": mp,
                    "fstype": part.fstype,
                    "total": u.total,
                    "used": u.used,
                    "free": u.free,
                    "percent": u.percent
                })
            except Exception:
                pass
        self.cached_fs = result
        return result

    def _collect_physical_disks(self) -> List[Dict[str, Any]]:
        """
        Probe physical storage drives, NVMe solid state controllers, and PCIe transport bus.
        Cached permanently at startup; temperatures are refreshed dynamically in sample_physical_disks.
        """
        disks = []
        try:
            res = subprocess.run(
                ["lsblk", "-J", "-b", "-o", "NAME,PATH,MODEL,SERIAL,SIZE,TYPE,TRAN,ROTA,FSTYPE,MOUNTPOINT,HOTPLUG"],
                capture_output=True, text=True, timeout=2.0
            )
            raw_devices = json.loads(res.stdout).get("blockdevices", []) if res.returncode == 0 else []
        except Exception:
            raw_devices = []

        nvme_sysfs = {}
        nvme_dir = Path("/sys/class/nvme")
        if nvme_dir.exists():
            for n in nvme_dir.glob("nvme*"):
                ctrl_name = n.name
                dev = n / "device"
                link_speed = read_text(dev / "current_link_speed")
                link_width = read_text(dev / "current_link_width")
                pcie_bus = f"{link_speed} x{link_width}".strip() if link_speed or link_width else ""
                hwmon_paths = list((n / "device").glob("hwmon*")) + list(n.glob("hwmon*"))

                nvme_sysfs[ctrl_name] = {
                    "model": read_text(n / "model"),
                    "serial": read_text(n / "serial"),
                    "firmware": read_text(n / "firmware_rev"),
                    "transport": read_text(n / "transport") or "pcie",
                    "pcie_link": pcie_bus,
                    "hwmon_paths": [str(p) for p in hwmon_paths]
                }

        for b in raw_devices:
            b_type = b.get("type", "")
            name = b.get("name", "")
            if b_type != "disk" and not name.startswith("nvme") and not name.startswith("sd") and not name.startswith("hd"):
                continue
            if name.startswith("loop") or name.startswith("ram") or name.startswith("zram"):
                continue

            tran = (b.get("tran") or "").lower()
            if not tran and name.startswith("nvme"):
                tran = "nvme"
            elif not tran and name.startswith("sd"):
                tran = "sata"

            rota = b.get("rota", False)
            if tran == "nvme":
                media_type = "NVMe Solid State (PCIe)"
            elif not rota:
                media_type = "SATA Solid State (SSD)"
            else:
                media_type = "Mechanical Hard Drive (HDD)"

            size_bytes = b.get("size") or 0
            try:
                size_bytes = int(size_bytes)
            except Exception:
                size_bytes = 0

            # Match NVMe controller (e.g. nvme0 from nvme0n1)
            m = re.match(r"(nvme\d+)", name)
            ctrl_id = m.group(1) if m else ""
            n_info = nvme_sysfs.get(ctrl_id, {})

            model = (b.get("model") or n_info.get("model") or "Generic Drive").strip()
            serial = (b.get("serial") or n_info.get("serial") or "").strip()
            firmware = n_info.get("firmware", "")
            pcie_link = n_info.get("pcie_link", "")

            # If SATA SSD/HDD, check for firmware revision in sysfs
            if not firmware and name.startswith("sd"):
                firmware = read_text(Path(f"/sys/block/{name}/device/rev"))
            if not model or model == "Generic Drive":
                sysfs_model = read_text(Path(f"/sys/block/{name}/device/model"))
                if sysfs_model:
                    model = sysfs_model

            partitions = []
            for child in b.get("children", []):
                c_name = child.get("name", "")
                c_size = child.get("size", 0)
                try:
                    c_size = int(c_size)
                except Exception:
                    c_size = 0
                partitions.append({
                    "name": c_name,
                    "path": child.get("path") or f"/dev/{c_name}",
                    "size_bytes": c_size,
                    "fstype": child.get("fstype") or "unknown",
                    "mountpoint": child.get("mountpoint") or ""
                })

            disks.append({
                "name": name,
                "path": b.get("path") or f"/dev/{name}",
                "model": model,
                "serial": serial,
                "firmware": firmware,
                "transport": tran.upper() or "STORAGE",
                "media_type": media_type,
                "is_ssd": not rota,
                "is_nvme": tran == "nvme",
                "pcie_link": pcie_link,
                "size_bytes": size_bytes,
                "hwmon_paths": n_info.get("hwmon_paths", []),
                "partitions": partitions
            })

        return disks

    def sample_physical_disks(self) -> List[Dict[str, Any]]:
        """Sample dynamic temperatures and live partition attachments for physical storage drives."""
        results = []
        for disk in self.cached_physical_disks:
            item = dict(disk)
            temps = []
            for hw_str in item.get("hwmon_paths", []):
                hw = Path(hw_str)
                if hw.exists():
                    for tf in sorted(hw.glob("temp*_input")):
                        try:
                            t_val = int(read_text(tf)) / 1000.0
                            label_file = tf.with_name(tf.name.replace("_input", "_label"))
                            lbl = read_text(label_file) if label_file.exists() else tf.stem
                            temps.append({"label": lbl, "temp_c": round(t_val, 1)})
                        except Exception:
                            pass
            item["temperatures"] = temps
            item["temp_c"] = temps[0]["temp_c"] if temps else None
            results.append(item)
        return results

    def sample_peripherals(self) -> Dict[str, Any]:
        """Sample displays, USB, and Thunderbolt devices at an optimized 15s interval."""
        now = time.monotonic()
        if now - self.last_periph_sample < 15.0 and self.cached_periph.get("displays") is not None:
            return self.cached_periph

        self.last_periph_sample = now

        # 1. Connected Displays via DRM
        displays = []
        for status_path in Path("/sys/class/drm").glob("card*-*/status"):
            try:
                if read_text(status_path) == "connected":
                    conn_name = status_path.parent.name.split("-", 1)[-1]
                    modes_file = status_path.parent / "modes"
                    modes = read_text(modes_file).splitlines() if modes_file.exists() else []
                    displays.append({
                        "connector": conn_name,
                        "resolution": modes[0] if modes else "Unknown",
                        "available_modes": modes[:4]
                    })
            except Exception:
                pass

        # 2. USB Devices
        usb_devices = []
        for dev_path in Path("/sys/bus/usb/devices").glob("*"):
            try:
                prod = read_text(dev_path / "product")
                manuf = read_text(dev_path / "manufacturer")
                if prod:
                    usb_devices.append(f"{manuf} {prod}".strip())
            except Exception:
                pass

        # 3. Thunderbolt Devices
        tb_devices = []
        for tb in Path("/sys/bus/thunderbolt/devices").glob("domain*"):
            try:
                name = read_text(tb / "device_name") or tb.name
                authorized = read_text(tb / "authorized")
                tb_devices.append({"device": name, "authorized": authorized == "1"})
            except Exception:
                pass

        self.cached_periph = {
            "displays": displays,
            "usb": sorted(list(set(usb_devices))),
            "thunderbolt": tb_devices
        }
        return self.cached_periph

    def sample_network_ext(self) -> Dict[str, Any]:
        """Collect WiFi link metrics and cumulative session transfer counters."""
        wifi_info = {}
        try:
            lines = read_text(Path("/proc/net/wireless")).splitlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 4:
                    iface = parts[0].replace(":", "")
                    link_qual = float(parts[2].replace(".", ""))
                    sig_level = float(parts[3].replace(".", ""))
                    wifi_info[iface] = {
                        "link_quality_percent": round((link_qual / 70.0) * 100, 1),
                        "signal_dbm": sig_level
                    }
        except Exception:
            pass

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

    def _sample_processes(self) -> List[Dict[str, Any]]:
        """
        Sample top processes ordered by CPU usage.
        Executes on a 3-4s decoupled interval to save up to 80% CPU.
        """
        now = time.monotonic()
        if now - self.last_proc_sample < 3.5 and self.cached_proc:
            return self.cached_proc

        self.last_proc_sample = now
        proc = []
        # Query only the necessary fields
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "status", "num_threads"]):
            try:
                info = p.info
                info["cpu_percent"] = info.get("cpu_percent") or 0.0
                info["memory_percent"] = info.get("memory_percent") or 0.0
                proc.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        # Sort and take top 50 processes
        self.cached_proc = sorted(proc, key=lambda x: x["cpu_percent"], reverse=True)[:50]
        return self.cached_proc

    def _get_system_threads_count(self) -> int:
        """
        Get accurate total system threads from /proc/loadavg instantly with 0 CPU cost.
        Format of field 4: '<running_tasks>/<total_threads>'
        """
        try:
            loadavg = read_text(Path("/proc/loadavg"))
            parts = loadavg.split()
            if len(parts) >= 4 and "/" in parts[3]:
                return int(parts[3].split("/")[1])
        except Exception:
            pass
        # Fallback
        return sum((x.get("num_threads") or 0) for x in self.cached_proc)

    def meminfo(self) -> Dict[str, int]:
        """Fast memory parsing of /proc/meminfo."""
        out = {}
        try:
            content = read_text(Path("/proc/meminfo"))
            for line in content.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    # Extract first integer without regex overhead
                    num = v.strip().split(" ", 1)[0]
                    if num.isdigit():
                        out[k] = int(num) * 1024
        except Exception:
            pass
        return out

    def collect(self) -> Dict[str, Any]:
        """
        Execute synchronous hardware collection.
        Optimized to complete in under 5ms (compared to 150-300ms previously).
        """
        now = time.monotonic()
        dt = max(now - self.prev_time, 0.001)

        # GPU sampled every 4 seconds
        if now - self.last_gpu_sample >= 4.0:
            self.last_gpu_sample = now
            self.cached_gpu = self._sample_gpu()

        # Processes sampled every 3.5 seconds
        processes = self._sample_processes()

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

        read_bytes = sum(x.read_bytes for x in d.values())
        write_bytes = sum(x.write_bytes for x in d.values())
        oldread = sum(x.read_bytes for x in self.prev_disk.values())
        oldwrite = sum(x.write_bytes for x in self.prev_disk.values())

        net_ext = self.sample_network_ext()

        # Per-interface transfer rates
        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()
        interfaces = {}
        for name, x in n.items():
            addrs = if_addrs.get(name, [])
            addr = next((a.address for a in addrs if a.family == socket.AF_INET), "")
            o = self.prev_net.get(name)
            rx_sec = max(0.0, (x.bytes_recv - o.bytes_recv) / dt) if o else 0.0
            tx_sec = max(0.0, (x.bytes_sent - o.bytes_sent) / dt) if o else 0.0
            
            # RFC2863 operstate for loopback is kernel-defined as 'unknown', but psutil checks IFF_UP flag
            stat_obj = if_stats.get(name)
            if stat_obj:
                state = "up" if stat_obj.isup else "down"
                mtu = str(stat_obj.mtu or 1500)
                speed_mbps = stat_obj.speed
            else:
                state = "up" if name == "lo" else "unknown"
                mtu = "65536" if name == "lo" else "1500"
                speed_mbps = 0
            
            if name == "lo":
                state = "up"
            
            interfaces[name] = {
                "rx_bytes": x.bytes_recv,
                "tx_bytes": x.bytes_sent,
                "rx_bytes_per_sec": rx_sec,
                "tx_bytes_per_sec": tx_sec,
                "address": addr or ("127.0.0.1" if name == "lo" else ""),
                "state": state,
                "mtu": mtu,
                "speed_mbps": speed_mbps,
                "drop_in": x.dropin,
                "err_in": x.errin
            }

        self.prev_net = n
        self.prev_disk = d
        self.prev_time = now

        load = os.getloadavg()
        boot = psutil.boot_time()
        thread_count = self._get_system_threads_count()

        try:
            open_files = len(psutil.Process().open_files())
        except Exception:
            open_files = 0

        return {
            "type": "hardware",
            "timestamp": time.time(),
            "cpu": {
                "global_percent": sum(cpu) / len(cpu) if cpu else 0.0,
                "per_core_percent": cpu,
                "per_core_freq_mhz": cfreq,
                "logical_cores": psutil.cpu_count(True) or 1,
                "physical_cores": psutil.cpu_count(False) or 1,
                "freq_current_mhz": sum(cfreq) / len(cfreq) if cfreq else 0.0,
                "ctx_switches": psutil.cpu_stats().ctx_switches,
                "model": self.cached_lscpu_details.get("model_name") or platform.processor(),
                "details": self.cached_lscpu_details
            },
            "memory": {
                "total": vm.total,
                "used": vm.used,
                "available": vm.available,
                "percent": vm.percent,
                "free": mi.get("MemFree", vm.free),
                "cached_bytes": mi.get("Cached", 0),
                "buffers_bytes": mi.get("Buffers", 0),
                "active_bytes": mi.get("Active", 0),
                "inactive_bytes": mi.get("Inactive", 0),
                "page_tables_bytes": mi.get("PageTables", 0),
                "shmem_bytes": mi.get("Shmem", 0),
                "slab_bytes": mi.get("Slab", 0),
                "s_reclaimable_bytes": mi.get("SReclaimable", 0),
                "s_unreclaim_bytes": mi.get("SUnreclaim", 0),
                "dirty_bytes": mi.get("Dirty", 0),
                "writeback_bytes": mi.get("Writeback", 0),
                "mapped_bytes": mi.get("Mapped", 0),
                "committed_as_bytes": mi.get("Committed_AS", 0),
                "commit_limit_bytes": mi.get("CommitLimit", 0),
                "swap_total": sw.total,
                "swap_free": sw.free,
                "swap_used": sw.used,
                "swap_percent": sw.percent,
                "hardware": self.cached_memory_hardware
            },
            "system": {
                "hostname": socket.gethostname(),
                "os": self.cached_os_details.get("pretty_name") or "Linux",
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
                "thread_count": thread_count,
                "open_files": open_files,
                "details": self.cached_os_details
            },
            "network": {
                "bytes_sent": tx,
                "bytes_recv": rx,
                "packets_sent": sum(x.packets_sent for x in n.values()),
                "packets_recv": sum(x.packets_recv for x in n.values()),
                "rx_bytes_per_sec": max(0.0, (rx - oldrx) / dt),
                "tx_bytes_per_sec": max(0.0, (tx - oldtx) / dt),
                "interfaces": interfaces
            },
            "disk": {
                "read_bytes": read_bytes,
                "write_bytes": write_bytes,
                "read_count": sum(x.read_count for x in d.values()),
                "write_count": sum(x.write_count for x in d.values()),
                "read_bytes_per_sec": max(0.0, (read_bytes - oldread) / dt),
                "write_bytes_per_sec": max(0.0, (write_bytes - oldwrite) / dt)
            },
            "filesystems": self.sample_filesystems(),
            "physical_disks": self.sample_physical_disks(),
            "sensors": self.sample_sensors(),
            "battery": self.sample_batteries(),
            "gpu": self.cached_gpu,
            "processes": processes,
            "peripherals": self.sample_peripherals(),
            "network_ext": net_ext
        }

    async def collect_async(self) -> Dict[str, Any]:
        """Run hardware collection off the main event loop to eliminate event loop latency."""
        return await asyncio.to_thread(self.collect)
