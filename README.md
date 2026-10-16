# 🍁 WindyDash MK-II

> **Low Latency. Hardware Precision. Zero Bloat.**  
> A high-performance, lightweight Linux system monitor, active network diagnostic suite, and retro telemetry console.

[![Linux](https://img.shields.io/badge/Platform-Linux-orange.svg)](https://kernel.org)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/Vault-SQLite3%20scrypt-blue.svg)](https://sqlite.org)
[![Systemd](https://img.shields.io/badge/Daemon-Systemd-red.svg)](https://systemd.io)
[![Style](https://img.shields.io/badge/Theme-Amber%20Phosphor%20CRT-amber.svg)](#dual-vintage-aesthetics)

---

## Overview

WindyDash is an industrial telemetry daemon and real-time visualization dashboard designed to replace heavy, resource-hungry monitoring stacks. It pairs a **sub-6ms Linux hardware collector** with an active **concurrent network prober** and a **zero-dependency SQLite security vault**, rendered through an authentic **Amber Phosphor CRT & Technical Parchment** web console.

### Key Highlights

* **Sub-6ms Telemetry:** Collects system vitals via Linux `sysfs` and `procfs` in under 6 milliseconds with zero Python garbage collector spikes.
* **Sleep-On-Idle Zero Waste:** When no browser viewers are connected, all collection and probe loops suspend indefinitely (0% CPU usage, 0 outbound network requests).
* **Detailed `lscpu` Silicon Topology:** Full CPU microarchitecture identification, stepping, multi-level SRAM cache hierarchy (L1d, L1i, L2, L3), and hardware virtualization with zero runtime polling overhead.
* **Zero-Dependency SQLite Vault:** Multi-user operator management with memory-hard `scrypt` password hashing, timing-safe verification, and 30-day session tokens in `windydash.db` (`chmod 0600`).
* **Active TingPing Prober:** Non-blocking concurrent ICMP Ping, TCP handshake, and HTTP(S) endpoint latency monitoring with atomic SQLite target storage.
* **Interactive Diagnostic Toolkit:** Streaming hop-by-hop Traceroute, CIDR Subnet Sweeper, DNS Record Resolver, TCP Port Reachability Checker, and Cloudflare Speedtest.
* **Zero-Downtime Daemon Manager:** Single-command `service.sh` for syntax-verified systemd installations, updates, status checks, and log streaming.

---

## Architecture & Codebase

```
WindyDash/
├── sysmon.py           # FastAPI application, WebSocket stream & CLI user manager
├── database.py         # SQLite vault: scrypt hashing, sessions & target persistence
├── hardware.py         # Sub-6ms Linux hardware telemetry & lscpu silicon topology
├── prober.py           # TingPing concurrent async network probe engine
├── diagnostics.py      # Streaming Traceroute, Subnet Sweeper, DNS, Port & Speedtest
├── security.py         # Session token verification & leak-free rate limiter
├── config.py           # Centralized configuration & zero-dependency .env parser
├── targets.json        # Template/backup for network probe targets
├── index.html          # Vintage CRT dashboard & access terminal interface
├── app.js              # WebSocket visualizer, Chart.js engine & auth controller
├── styles.css          # Amber Phosphor CRT & Technical Parchment dual themes
├── service.sh          # Systemd deployment controller (alias: deploy.sh)
├── windydash.service   # Systemd unit file template
├── requirements.txt    # Python dependencies (FastAPI, Uvicorn, Psutil, Httpx)
├── .env.example        # Configuration environment template
└── docs/               # GitHub Pages documentation & interactive .env generator
    ├── index.html
    ├── styles.css
    └── script.js
```

---

## Core Capabilities

### 1. Sub-6ms Hardware Telemetry Engine
* **Processor Vitals:** Global and per-core utilization percentages, real-time core frequency distributions, load averages (1m, 5m, 15m), and kernel context switches.
* **Memory & Swap:** Physical RAM breakdown (Used, Available, Free, Cached, Buffers, Active, Inactive, Page Tables) and paging saturation dynamics.
* **Detailed `lscpu` Hardware Topology:**
  * Microarchitecture, vendor ID, CPU family, model, silicon stepping, and instruction op-modes.
  * Physical sockets, cores per socket, and execution threads per core (SMT / HyperThreading).
  * Multi-level SRAM cache hierarchy: L1 Data, L1 Instruction, L2 Unified, and L3 Shared cache.
  * Hardware virtualization status (`AMD-V` / `Intel VT-x`).
  * Filtered instruction extensions: `AVX2`, `AVX`, `FMA`, `AES-NI`, `SHA-NI`, `SSE4.2`.
  * Silicon vulnerability mitigation audit (Spectre v1/v2, Meltdown, SSBD, etc.).
* **Thermals & Peripherals:** Automatic hwmon sensor discovery (temperatures, fan RPMs, voltages), battery health (`/sys/class/power_supply`), dedicated NVIDIA GPU telemetry (`nvidia-smi`), DRM displays, USB devices, and Thunderbolt controllers.
* **Process Monitor:** Top processes sorted by CPU and memory usage with real-time interactive fuzzy searching.

### 2. Zero-Dependency SQLite Security Vault
* **Cryptographic Hashing:** Passwords derive memory-hard hashes via standard library `hashlib.scrypt` ($N=16384, r=8, p=1$) with cryptographically random 16-byte salts.
* **Timing-Attack Proof:** Credential comparisons execute in constant time using `hmac.compare_digest`.
* **File Permissions:** `windydash.db` is strictly locked to `0600` (`-rw-------`) on Linux, preventing unprivileged local users from inspecting hashes or targets.
* **First-Time Setup Wizard:** If no operator accounts exist on first boot, the web console guides the administrator through master account initialization.
* **Session Management:** Secure 30-day session tokens authenticate both WebSocket telemetry feeds and REST diagnostic endpoints. Includes an active operator badge and instant logout.

### 3. Active Network Probe Engine (TingPing)
* **Concurrent Async Testing:** Probes ICMP ping, TCP handshake, and HTTP(S) status codes in parallel using asynchronous non-blocking event loops.
* **Atomic Persistence:** Target definitions are stored in the SQLite `targets` table with zero-overhead in-memory caching during routine probe cycles.
* **Dynamic Latency Badges:** Color-coded threshold indicators for fast ($<35\text{ms}$), nominal ($<100\text{ms}$), warning ($<250\text{ms}$), and offline states.

### 4. Live Diagnostic Suite
* **Streaming Traceroute:** Live hop-by-hop latency and IP discovery streamed via NDJSON.
* **CIDR Subnet Sweeper:** Discovers active IP addresses across local subnets (e.g. `192.168.1.0/24`).
* **DNS Resolver:** Queries A, AAAA, CNAME, and PTR resource records for any domain or host.
* **TCP Port Reachability:** Verifies handshake status and response latency on custom TCP ports.
* **Bandwidth Speedtest:** Evaluates real-time downlink and uplink throughput against Cloudflare endpoints with zero-entropy upload buffers.

### 5. Dual Vintage Aesthetics
* **Amber Phosphor CRT (Dark Mode):** Inspired by DEC VT-series cathode displays, vintage laboratory oscilloscopes, and bakelite instrumentation with glowing jewel pilot lamps and cathode vignettes.
* **Technical Parchment (Light Mode):** Inspired by Olivetti typewriter typography, archival technical service manuals, and soot carbon ribbon aesthetics.

---

## Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/NirjharDebnath/WindyDash.git
cd WindyDash
```

### 2. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment (Optional)
```bash
cp .env.example .env
```
*(Default settings run locally on port `4242` with SQLite operator authentication).*

### 4. Launch the Daemon
```bash
python3 sysmon.py
```
Open **`http://127.0.0.1:4242`** in your web browser.

---

## Operator Account Management (CLI)

WindyDash provides a built-in command-line interface to manage operators directly from an SSH terminal without opening a browser:

| Action | Command Line | Description |
| :--- | :--- | :--- |
| **List Operators** | `python3 sysmon.py --listusers` | Displays all registered operator usernames and IDs in the SQLite vault. |
| **Register Operator** | `python3 sysmon.py --adduser <username>` | Interactively prompts for a password and registers a new operator. |
| **Update Passcode** | `python3 sysmon.py --passwd <username>` | Interactively updates an existing operator's password. |

*Short flags are also supported: `-l` (list), `-a` (adduser), `-p` (passwd).*

---

## Systemd Service Management

WindyDash includes an automated deployment script ([`service.sh`](service.sh)) for production Linux installations:

### Install as a System Daemon
```bash
./service.sh install
```
* Pre-checks syntax across all Python modules.
* Auto-detects your virtual environment or Python interpreter.
* Configures and registers `windydash.service` in `/etc/systemd/system/`.
* Enables automatic startup on system boot and launches the daemon.

### Zero-Downtime Updates
Whenever you modify files or pull git updates:
```bash
./service.sh update
```
*(Alias: `./deploy.sh`)*  
Validates Python syntax before restarting to guarantee zero downtime.

### Status & Log Monitoring
```bash
./service.sh status     # Check live daemon status & health
./service.sh logs       # Stream real-time journal logs (Ctrl+C to exit)
./service.sh stop       # Gracefully stop the background daemon
```

---

## Configuration Reference (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SYSMON_HOST` | `127.0.0.1` | Network interface to bind. Use `0.0.0.0` to allow access across your LAN. |
| `SYSMON_PORT` | `4242` | TCP port for the web dashboard and WebSocket server. |
| `SYSMON_INTERVAL`| `1.0` | Telemetry polling and broadcast interval in seconds. |
| `RATE_LIMIT` | `25` | Maximum allowed API requests per sliding window per client IP. |
| `RATE_WINDOW` | `5.0` | Sliding window duration in seconds for anti-DDoS rate limiting. |
| `DASH_PASSWORD` | *(Empty)* | *(Optional)* Legacy master passphrase fallback. Operator accounts in SQLite vault are used by default. |

---

## API & WebSocket Endpoints

### Live Telemetry Stream
* **`WS /ws?token=<session_token>`**  
  Bidirectional WebSocket stream broadcasting hardware vitals and network target status. Pauses automatically when no viewers are connected.

### Authentication Endpoints
* **`GET /api/auth/status`** — Checks whether users exist in the database and verifies the caller's token.
* **`POST /api/auth/setup`** — Initializes the master operator on first boot (disabled once users exist).
* **`POST /api/auth/login`** — Authenticates credentials and returns a 30-day session token.
* **`POST /api/auth/logout`** — Revokes an active session token.

### Telemetry & Network REST APIs
* **`GET /api/device-status`** — Returns local and public IP addresses, default gateway, and internet connectivity.
* **`POST /api/targets`** — Adds a new probe target to SQLite (`name`, `host`, `method`, `port`).
* **`DELETE /api/targets/{id}`** — Removes a probe target by ID.
* **`POST /api/targets/recheck`** — Manually triggers an immediate probe of all network targets.
* **`POST /api/traceroute`** — Streams hop-by-hop ICMP traceroute results (NDJSON).
* **`POST /api/discovery`** — Streams active subnet hosts for a CIDR range (NDJSON).
* **`POST /api/dns`** — Streams DNS resource records for a hostname (NDJSON).
* **`POST /api/portcheck`** — Streams TCP port reachability and latency (NDJSON).
* **`POST /api/speedtest`** — Streams real-time download and upload bandwidth benchmarks (NDJSON).

---

## License

Released under the **MIT License**. Crafted with precision for Linux power users and system administrators.
