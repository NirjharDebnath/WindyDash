# WindyDash

A high-performance, lightweight Linux system monitor and active network probe engine with a FastAPI backend and a clean Chart.js web dashboard.

## Features

- **Live System Vitals**: CPU per-core utilization & frequencies, memory composition, load averages, context switches, disk I/O rates, and network interface counters.
- **Hardware Telemetry**: Thermal sensors, fan speeds, voltage levels (`/sys/class/hwmon`), batteries (`/sys/class/power_supply`), NVIDIA GPU metrics (`nvidia-smi`), and DRM displays / USB / Thunderbolt peripherals.
- **TingPing Network Probing**: Concurrent async ICMP ping, TCP port, and HTTP(S) endpoint latency monitoring with configurable targets.
- **Diagnostics Toolkit**: Streaming live traceroute, CIDR subnet sweep, and zero-entropy Cloudflare bandwidth benchmarks.
- **Engineered for Low Overhead**:
  - Decoupled sampling intervals with sysfs path pre-resolution.
  - Zero CPU & network waste: background probing automatically idles when no clients are connected.
  - Memory-leak free rate limiter and timing-safe authentication.
  - Offloaded async hardware collection so WebSocket streams never block.

## Modular Codebase

```
WindyDash/
├── sysmon.py           # Daemon entry point & FastAPI application routes
├── hardware.py         # Low-overhead Linux hardware telemetry collector
├── prober.py           # Concurrent async network probe engine
├── diagnostics.py      # Traceroute, CIDR sweep, and speedtest streaming
├── security.py         # Timing-safe token auth & self-pruning rate limiter
├── config.py           # Centralized configuration & environment loader
├── targets.json        # Network probe target definitions
├── index.html          # Web dashboard interface
├── app.js              # Client-side WebSocket visualizer & Chart.js logic
├── styles.css          # Vintage analog CRT / technical manual styling
├── requirements.txt    # Python dependencies
├── service.sh          # One-click installation & update script (alias: deploy.sh)
└── windydash.service   # Systemd service unit definition
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)

Create or edit `.env`:

```env
SYSMON_INTERVAL=1.0
SYSMON_HOST=127.0.0.1
SYSMON_PORT=4040
DASH_PASSWORD=YourSecurePassword
```

### 3. Run Manually

```bash
python3 sysmon.py
```

Open `http://127.0.0.1:4040` in your web browser.

---

## Systemd Service Management

Use the included [`service.sh`](service.sh) script to install, update, or reload WindyDash with one command:

### Initial Setup & Installation
```bash
./service.sh install
```
* Automatically verifies Python module syntax.
* Auto-detects the virtualenv/Python interpreter.
* Installs `windydash.service` to `/etc/systemd/system/`.
* Reloads `systemd` and starts the service.

### Updating After Any Code Changes
Whenever you edit Python, HTML, CSS, or JS files:
```bash
./service.sh update
```
*(Or simply `./deploy.sh`)*
* Runs pre-flight syntax checks to guarantee zero downtime.
* Reloads `systemd` daemon configuration.
* Restarts `windydash.service` and prints active status and dashboard URL.

### Other Useful Commands
```bash
./service.sh status     # Check live status & health
./service.sh logs       # Stream real-time daemon logs
./service.sh stop       # Gracefully stop the daemon
```
