#!/usr/bin/env bash
# ==============================================================================
# WindyDash - Systemd Service & Deployment Manager
# Handles installation, auto-detection, syntax verification, reload, and updates.
# ==============================================================================

set -e

# Terminal Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_AMBER="\033[38;5;214m"
C_GREEN="\033[38;5;34m"
C_RED="\033[38;5;196m"
C_CYAN="\033[38;5;75m"
C_DIM="\033[2m"

# Project Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="windydash.service"
SYSTEMD_DEST="/etc/systemd/system/${SERVICE_NAME}"

print_banner() {
    echo -e "${C_AMBER}${C_BOLD}"
    echo "  ╔═════════════════════════════════════════════════════════════╗"
    echo "  ║         WINDYDASH SERVICE & DEPLOYMENT CONTROLLER           ║"
    echo "  ╚═════════════════════════════════════════════════════════════╝"
    echo -e "${C_RESET}"
}

# --- 1. Python Interpreter Auto-Detection ---
detect_python() {
    # Check for local virtual environments first
    if [ -f "${SCRIPT_DIR}/.venv/bin/python" ]; then
        echo "${SCRIPT_DIR}/.venv/bin/python"
    elif [ -f "${SCRIPT_DIR}/venv/bin/python" ]; then
        echo "${SCRIPT_DIR}/venv/bin/python"
    elif [ -f "/home/nirjhar/Python Codes/Einstein/einsteinenv/bin/python" ]; then
        echo "/home/nirjhar/Python Codes/Einstein/einsteinenv/bin/python"
    else
        which python3
    fi
}

PYTHON_BIN="$(detect_python)"

# --- 2. Syntax & Integrity Pre-Check ---
verify_syntax() {
    echo -e "${C_CYAN}➤ Verifying Python module integrity...${C_RESET}"
    if ! "${PYTHON_BIN}" -m py_compile "${SCRIPT_DIR}"/*.py > /dev/null 2>&1; then
        echo -e "${C_RED}✖ Syntax check failed! Please fix the errors before reloading the daemon.${C_RESET}"
        "${PYTHON_BIN}" -m py_compile "${SCRIPT_DIR}"/*.py
        exit 1
    fi
    echo -e "${C_GREEN}✔ All Python modules verified successfully.${C_RESET}"
}

# --- 3. Generate Optimized Service Unit File ---
generate_service_unit() {
    echo -e "${C_CYAN}➤ Configuring ${SERVICE_NAME}...${C_RESET}"
    
    cat <<EOF > "${SCRIPT_DIR}/${SERVICE_NAME}"
[Unit]
Description=WindyDash Retro Telemetry Daemon & Web Dashboard
Documentation=file:${SCRIPT_DIR}/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER:-root}
WorkingDirectory=${SCRIPT_DIR}
ExecStart="${PYTHON_BIN}" sysmon.py
Restart=always
RestartSec=3

# Safe environment loading
EnvironmentFile=-${SCRIPT_DIR}/.env

# Clean shutdown
KillSignal=SIGTERM
TimeoutStopSec=10
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF

    echo -e "${C_GREEN}✔ Service file generated with Python: ${PYTHON_BIN}${C_RESET}"
}

# --- 4. Install / Replace Systemd Service ---
install_service() {
    verify_syntax
    generate_service_unit

    echo -e "${C_CYAN}➤ Installing service to ${SYSTEMD_DEST}...${C_RESET}"
    if [ "$EUID" -ne 0 ]; then
        sudo cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${SYSTEMD_DEST}"
        sudo systemctl daemon-reload
        sudo systemctl enable --now "${SERVICE_NAME}"
    else
        cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${SYSTEMD_DEST}"
        systemctl daemon-reload
        systemctl enable --now "${SERVICE_NAME}"
    fi

    echo -e "${C_GREEN}✔ ${SERVICE_NAME} installed, enabled, and started!${C_RESET}"
    show_status
}

# --- 5. Reload / Update Daemon ---
reload_service() {
    verify_syntax

    echo -e "${C_CYAN}➤ Reloading and restarting ${SERVICE_NAME}...${C_RESET}"
    if [ "$EUID" -ne 0 ]; then
        # Re-copy in case service parameters changed
        if [ -f "${SCRIPT_DIR}/${SERVICE_NAME}" ]; then
            sudo cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${SYSTEMD_DEST}"
        fi
        sudo systemctl daemon-reload
        sudo systemctl restart "${SERVICE_NAME}"
    else
        if [ -f "${SCRIPT_DIR}/${SERVICE_NAME}" ]; then
            cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${SYSTEMD_DEST}"
        fi
        systemctl daemon-reload
        systemctl restart "${SERVICE_NAME}"
    fi

    # Check health
    sleep 1
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo -e "${C_GREEN}✔ ${SERVICE_NAME} reloaded successfully and is ACTIVE.${C_RESET}"
        show_status
    else
        echo -e "${C_RED}✖ Service failed to start. Showing recent journal logs:${C_RESET}"
        journalctl -u "${SERVICE_NAME}" -n 25 --no-pager
        exit 1
    fi
}

# --- 6. Show Status & Access URL ---
show_status() {
    PORT="4040"
    if [ -f "${SCRIPT_DIR}/.env" ]; then
        ENV_PORT=$(grep -E "^SYSMON_PORT=" "${SCRIPT_DIR}/.env" | cut -d'=' -f2 | tr -d ' "' || true)
        if [ -n "${ENV_PORT}" ]; then
            PORT="${ENV_PORT}"
        fi
    fi

    echo ""
    echo -e "${C_AMBER}${C_BOLD}SERVICE STATUS:${C_RESET}"
    systemctl status "${SERVICE_NAME}" --no-pager -l || true
    echo ""
    echo -e "${C_GREEN}${C_BOLD}WindyDash is live at: ${C_CYAN}http://127.0.0.1:${PORT}${C_RESET}"
    echo ""
}

# --- 7. Live Logs ---
show_logs() {
    echo -e "${C_CYAN}➤ Streaming live logs for ${SERVICE_NAME} (Ctrl+C to exit)...${C_RESET}"
    journalctl -u "${SERVICE_NAME}" -f -n 50
}

# --- 8. Stop Service ---
stop_service() {
    echo -e "${C_AMBER}➤ Stopping ${SERVICE_NAME}...${C_RESET}"
    if [ "$EUID" -ne 0 ]; then
        sudo systemctl stop "${SERVICE_NAME}"
    else
        systemctl stop "${SERVICE_NAME}"
    fi
    echo -e "${C_GREEN}✔ Service stopped.${C_RESET}"
}

# --- CLI Router ---
print_banner

ACTION="${1:-}"

case "${ACTION}" in
    install|setup)
        install_service
        ;;
    reload|restart|update|refresh)
        reload_service
        ;;
    status)
        show_status
        ;;
    logs|log)
        show_logs
        ;;
    stop)
        stop_service
        ;;
    help|--help|-h)
        echo "Usage: ./service.sh [COMMAND]"
        echo ""
        echo "Commands:"
        echo "  install / setup       Install or replace windydash.service and enable it"
        echo "  reload / update       Verify Python syntax, reload systemd, and refresh the daemon"
        echo "  status                Display current systemd status and dashboard URL"
        echo "  logs                  Follow real-time journal logs"
        echo "  stop                  Stop the running service"
        echo ""
        echo "Tip: Run './service.sh update' whenever you modify Python files or HTML/JS/CSS."
        ;;
    *)
        # Default behavior: if service file already exists in /etc/systemd/system, reload it; otherwise install it!
        if [ -f "${SYSTEMD_DEST}" ]; then
            echo -e "${C_AMBER}Detected existing service. Updating and refreshing...${C_RESET}"
            reload_service
        else
            echo -e "${C_AMBER}Setting up and installing new service...${C_RESET}"
            install_service
        fi
        ;;
esac
