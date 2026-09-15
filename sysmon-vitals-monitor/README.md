# SysMon

A Vitals-inspired Linux system monitor with a headless Python daemon and a Chart.js web UI.

## Groups

- Overview
- Processor
- Memory
- System
- Network
- Storage
- Sensors (temperature, fan, voltage)
- GPU
- Battery
- Processes

The design follows the broad sensor grouping used by GNOME Vitals, whose current feature set covers temperature, voltage, fan, memory, processor, system, network, storage, battery and GPU telemetry. The original Vitals project is here:
https://github.com/corecoding/Vitals

## Run

Install dependencies:

    python3 -m pip install psutil websockets

Start daemon:

    python3 sysmon.py

Open `index.html` in a browser.

The UI connects to `ws://localhost:8765`.

## Install as a daemon

Copy this directory to `/opt/sysmon`, then:

    sudo cp sysmon.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now sysmon.service
    systemctl status sysmon.service

## Notes

- NVIDIA GPU telemetry uses `nvidia-smi` when available.
- Temperature/fan/voltage sensors are discovered from `/sys/class/hwmon`.
- Battery data is discovered from `/sys/class/power_supply/BAT*`.
- Network and disk rates are calculated from kernel counters.
- The browser is only a client; the daemon performs collection.
- The daemon currently exposes WebSocket telemetry on all interfaces. For a LAN-exposed deployment, add authentication/TLS or bind to localhost/reverse-proxy it.
