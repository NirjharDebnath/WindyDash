/**
 * WindyDash - Frontend Application & Telemetry Visualizer
 * Optimized for low client CPU/RAM usage with modern animations and network tools:
 * - Smart chart updating: only renders charts on the active, visible tab
 * - Pauses canvas repaints when browser tab is hidden/minimized
 * - Interactive real-time process search/filter
 * - Storage filesystem capacity meters with color-coded warning thresholds
 * - DNS record resolution & TCP port reachability tools
 * - Fixed peripherals rendering (displays, USB, and Thunderbolt)
 * - Secured target management & instant target recheck
 */

// --- Authentication & Handshake ---
let authToken = sessionStorage.getItem("dash_token");
if (!authToken) {
    authToken = prompt("Enter WindyDash Password (leave blank if none configured):") || "";
    sessionStorage.setItem("dash_token", authToken);
}

const protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
const host = window.location.host || "127.0.0.1:4040";
const MAX_POINTS = 60;
let ws, retryTimer, latest = null;
let activeTab = "overview";
let procFilterTerm = "";

const history = {
    cpu: [], mem: [], swap: [], rx: [], tx: [], read: [], write: [], load: [], time: []
};

// --- DOM & Formatting Utilities ---
const $ = id => document.getElementById(id);

const fmtBytes = (n = 0) => {
    const u = ["B", "KB", "MB", "GB", "TB"];
    if (!n || n <= 0) return "0 B";
    let i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
    return `${(n / (1024 ** i)).toFixed(i ? 1 : 0)} ${u[i]}`;
};

const fmtRate = n => `${fmtBytes(n)}/s`;
const fmtPct = n => `${Number(n || 0).toFixed(1)}%`;
const fmtTemp = n => `${Number(n || 0).toFixed(1)} °C`;

const esc = s => String(s ?? "").replace(/[&<>"']/g, m => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
}[m]));

function push(a, v) {
    a.push(Number(v) || 0);
    if (a.length > MAX_POINTS) a.shift();
}

function labels() {
    return history.time.map((_, i) => i - history.time.length + 1);
}

// --- Chart Initialization & Factory ---
function chart(id, label, datasets, unit = "") {
    const el = $(id);
    if (!el) return null;
    return new Chart(el, {
        type: "line",
        data: {
            labels: labels(),
            datasets: datasets.map(d => ({
                label: d.label,
                data: d.data,
                borderColor: d.borderColor,
                backgroundColor: d.backgroundColor || "transparent",
                fill: false,
                tension: 0.35,
                pointRadius: 0,
                borderWidth: 2
            }))
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: datasets.length > 1 } },
            scales: {
                x: { display: false },
                y: {
                    beginAtZero: true,
                    ticks: { callback: v => unit ? `${v}${unit}` : v },
                    grid: { color: "rgba(127,140,160,.12)" }
                }
            }
        }
    });
}

let charts = {};

function initCharts() {
    charts.ovCpu = chart("overviewCpuChart", "CPU %", [{ label: "CPU", data: history.cpu, borderColor: "#f59e0b" }], "%");
    charts.ovMem = chart("overviewMemChart", "Memory %", [{ label: "Memory", data: history.mem, borderColor: "#22c55e" }], "%");
    
    const coreEl = $("coreChart");
    if (coreEl) {
        charts.core = new Chart(coreEl, {
            type: "bar",
            data: { labels: [], datasets: [{ label: "Usage %", data: [], backgroundColor: "#f59e0b", borderRadius: 2 }] },
            options: { responsive: true, maintainAspectRatio: false, animation: false, scales: { y: { beginAtZero: true, max: 100 } } }
        });
    }

    charts.freq = chart("freqChart", "MHz", [{ label: "Frequency", data: [], borderColor: "#fbbf24" }], "");
    
    const memEl = $("memoryChart");
    if (memEl) {
        charts.memory = new Chart(memEl, {
            type: "doughnut",
            data: { labels: ["Used", "Available"], datasets: [{ data: [0, 100], backgroundColor: ["#f59e0b", "#2b231b"], borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, animation: false }
        });
    }

    charts.swap = chart("swapChart", "Swap %", [{ label: "Swap", data: history.swap, borderColor: "#ef4444" }], "%");
    charts.load = chart("loadChart", "Load", [{ label: "1m", data: history.load, borderColor: "#f59e0b" }]);
    charts.net = chart("networkChart", "Network", [
        { label: "RX", data: history.rx, borderColor: "#22c55e" },
        { label: "TX", data: history.tx, borderColor: "#ea580c" }
    ], "");
    charts.disk = chart("diskChart", "Disk", [
        { label: "Read", data: history.read, borderColor: "#22c55e" },
        { label: "Write", data: history.write, borderColor: "#f59e0b" }
    ], "");
}

function updateChart(c) {
    if (!c || document.hidden) return;
    c.data.labels = labels();
    c.update("none");
}

function refreshActiveTabCharts() {
    if (document.hidden) return;
    if (activeTab === "overview") {
        updateChart(charts.ovCpu);
        updateChart(charts.ovMem);
    } else if (activeTab === "processor" && latest) {
        const c = latest.cpu || {};
        if (charts.core) {
            charts.core.data.labels = (c.per_core_percent || []).map((_, i) => `C${i}`);
            charts.core.data.datasets[0].data = c.per_core_percent || [];
            charts.core.update("none");
        }
        if (charts.freq) {
            charts.freq.data.labels = (c.per_core_freq_mhz || []).map((_, i) => `C${i}`);
            charts.freq.data.datasets[0].data = c.per_core_freq_mhz || [];
            charts.freq.update("none");
        }
    } else if (activeTab === "memory" && latest) {
        const m = latest.memory || {};
        if (charts.memory) {
            charts.memory.data.datasets[0].data = [m.used || 0, m.available || 0];
            charts.memory.update("none");
        }
        updateChart(charts.swap);
    } else if (activeTab === "system") {
        updateChart(charts.load);
    } else if (activeTab === "network") {
        updateChart(charts.net);
    } else if (activeTab === "storage") {
        updateChart(charts.disk);
    }
}

// --- Theme Management ---
function updateThemeVisuals() {
    const isLight = localStorage.getItem("sysmon-theme") === "light";
    document.documentElement.classList.toggle("light", isLight);
    if ($("themeIcon")) {
        $("themeIcon").textContent = isLight ? "☾" : "☼";
    }
    Object.values(charts).forEach(c => { if (c) c.update("none"); });
}

if ($("themeToggle")) {
    $("themeToggle").onclick = () => {
        const currentIsLight = document.documentElement.classList.contains("light");
        localStorage.setItem("sysmon-theme", currentIsLight ? "dark" : "light");
        updateThemeVisuals();
    };
}
updateThemeVisuals();

// --- Tab Switching with Performance Hooks ---
document.querySelectorAll(".tab").forEach(b => {
    b.onclick = () => {
        document.querySelectorAll(".tab, .tab-panel").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        activeTab = b.dataset.tab || "overview";
        const target = $(activeTab);
        if (target) target.classList.add("active");
        refreshActiveTabCharts();
        
        if (activeTab === "processes" && latest) {
            renderProcessTable(latest.processes || []);
        }
    };
});

document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
        refreshActiveTabCharts();
    }
});

function stats(id, items) {
    const el = $(id);
    if (el) el.innerHTML = items.map(([k, v]) => `<div class="stat"><label>${esc(k)}</label><b>${esc(v)}</b></div>`).join("");
}

function rows(id, obj, suffix = "") {
    const el = $(id);
    if (!el) return;
    const a = Object.entries(obj || {});
    el.innerHTML = a.length
        ? a.map(([k, v]) => `<div class="sensor-row"><div>${esc(k)}<small>${esc(v.description || "")}</small></div><span class="value">${esc(v.value ?? v)}${suffix}</span></div>`).join("")
        : `<div class="empty">No sensors reported.</div>`;
}

// --- Real-time Process Filtering ---
function renderProcessTable(p) {
    const table = $("processTable");
    if (!table) return;

    let filtered = p;
    if (procFilterTerm) {
        filtered = p.filter(x => {
            const name = String(x.name || "").toLowerCase();
            const user = String(x.username || "").toLowerCase();
            const pid = String(x.pid || "");
            const status = String(x.status || "").toLowerCase();
            return name.includes(procFilterTerm) || user.includes(procFilterTerm) || pid.includes(procFilterTerm) || status.includes(procFilterTerm);
        });
    }

    if ($("processCount")) {
        $("processCount").textContent = procFilterTerm ? `${filtered.length} of ${p.length}` : `Top ${p.length}`;
    }

    if (!filtered.length) {
        table.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--muted);">No processes matching "${esc(procFilterTerm)}"</td></tr>`;
        return;
    }

    table.innerHTML = filtered.map(x => `
        <tr>
            <td><strong>${x.pid}</strong></td>
            <td>${esc(x.username || "—")}</td>
            <td><b>${esc(x.name || "—")}</b></td>
            <td><span class="badge" style="color:var(--accent);">${Number(x.cpu_percent || 0).toFixed(1)}%</span></td>
            <td>${Number(x.memory_percent || 0).toFixed(1)}%</td>
            <td>${x.num_threads || 0}</td>
            <td><span class="badge">${esc(x.status)}</span></td>
        </tr>`).join("");
}

if ($("procSearch")) {
    $("procSearch").oninput = (e) => {
        procFilterTerm = e.target.value.trim().toLowerCase();
        if ($("clearProcSearch")) {
            $("clearProcSearch").style.display = procFilterTerm ? "inline-block" : "none";
        }
        if (latest) renderProcessTable(latest.processes || []);
    };
}
if ($("clearProcSearch")) {
    $("clearProcSearch").onclick = () => {
        $("procSearch").value = "";
        procFilterTerm = "";
        $("clearProcSearch").style.display = "none";
        if (latest) renderProcessTable(latest.processes || []);
    };
}

// --- Main Telemetry Handler ---
function updateHardware(d) {
    latest = d;
    const c = d.cpu || {}, m = d.memory || {}, s = d.system || {}, n = d.network || {}, disk = d.disk || {};

    push(history.cpu, c.global_percent);
    push(history.mem, m.percent);
    push(history.swap, m.swap_total ? ((m.swap_total - m.swap_free) / m.swap_total * 100) : 0);
    push(history.rx, n.rx_bytes_per_sec);
    push(history.tx, n.tx_bytes_per_sec);
    push(history.read, disk.read_bytes_per_sec);
    push(history.write, disk.write_bytes_per_sec);
    push(history.load, s.load1);
    push(history.time, Date.now());

    // Update connection status
    const statusEl = $("daemonStatus");
    if (statusEl) {
        statusEl.className = "status connected";
    }
    if ($("daemonStatusText")) $("daemonStatusText").textContent = "Connected";
    if ($("lastUpdate")) $("lastUpdate").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if ($("hostInfo")) $("hostInfo").textContent = `${s.hostname || "Linux"} • kernel ${s.kernel || "—"} • uptime ${s.uptime_human || "—"}`;

    // Overview Hero Cards
    if ($("ovCpu")) $("ovCpu").textContent = fmtPct(c.global_percent);
    if ($("ovCpuFreq")) $("ovCpuFreq").textContent = `${(c.freq_current_mhz || 0).toFixed(0)} MHz • ${c.logical_cores || 0} cores`;
    if ($("ovMem")) $("ovMem").textContent = fmtPct(m.percent);
    if ($("ovMemDetail")) $("ovMemDetail").textContent = `${fmtBytes(m.used)} / ${fmtBytes(m.total)}`;
    if ($("ovLoad")) $("ovLoad").textContent = Number(s.load1 || 0).toFixed(2);
    if ($("ovUptime")) $("ovUptime").textContent = s.uptime_human || "—";
    if ($("ovNet")) $("ovNet").textContent = `↓ ${fmtRate(n.rx_bytes_per_sec || 0)}`;
    if ($("ovNetDetail")) $("ovNetDetail").textContent = `↑ ${fmtRate(n.tx_bytes_per_sec || 0)}`;

    // Top process cards on Overview
    const procList = d.processes || [];
    if ($("topProcess")) {
        $("topProcess").innerHTML = procList.slice(0, 4).map((proc, i) => `
            <div class="process-item">
                <div class="process-rank">${i + 1}</div>
                <div class="process-info">
                    <div class="process-name">${esc(proc.name || "—")}</div>
                    <div class="process-meta">PID ${proc.pid} • ${Number(proc.cpu_percent || 0).toFixed(1)}% CPU • ${Number(proc.memory_percent || 0).toFixed(1)}% RAM</div>
                </div>
            </div>
        `).join("") || "—";
    }

    // Hot sensors snapshot on Overview
    const tSensors = Object.values(d.sensors || {}).filter(z => z.type === "temperature");
    if ($("hotSensors")) {
        $("hotSensors").innerHTML = tSensors
            .sort((a, b) => b.value - a.value)
            .slice(0, 4)
            .map((z, i) => `
                <div class="sensor-item">
                    <div class="sensor-rank">${i + 1}</div>
                    <div class="sensor-info">
                        <div class="sensor-name">${esc(z.label)}</div>
                        <div class="sensor-meta">Thermal sensor</div>
                    </div>
                    <div class="sensor-value">${fmtTemp(z.value)}</div>
                </div>
            `).join("") || '<div class="empty">No temperature sensors.</div>';
    }

    // Quick System & System Tab stats
    if ($("quickSystem")) {
        $("quickSystem").innerHTML = [["Hostname", s.hostname], ["OS", s.os], ["Kernel", s.kernel], ["Uptime", s.uptime_human]]
            .map(x => `<div>${esc(x[0])}</div><div>${esc(x[1])}</div>`).join("");
    }
    if ($("systemInfo")) {
        $("systemInfo").innerHTML = [["Boot", s.boot_time], ["CPU model", c.model], ["Python", s.python]]
            .map(x => `<div>${esc(x[0])}</div><div>${esc(x[1])}</div>`).join("");
    }

    stats("processorStats", [
        ["Physical cores", c.physical_cores],
        ["Logical cores", c.logical_cores],
        ["Current frequency", `${(c.freq_current_mhz || 0).toFixed(0)} MHz`],
        ["Context switches", c.ctx_switches]
    ]);
    if ($("coreCount")) $("coreCount").textContent = `${c.logical_cores || 0} logical cores`;

    stats("memoryStats", [
        ["Total", fmtBytes(m.total)],
        ["Used", fmtBytes(m.used)],
        ["Available", fmtBytes(m.available)],
        ["Cached", fmtBytes(m.cached_bytes)],
        ["Buffers", fmtBytes(m.buffers_bytes)],
        ["Active", fmtBytes(m.active_bytes)],
        ["Inactive", fmtBytes(m.inactive_bytes)],
        ["Page tables", fmtBytes(m.page_tables_bytes)]
    ]);
    if ($("memPercent")) $("memPercent").textContent = fmtPct(m.percent);

    stats("systemStats", [
        ["Load 1m", Number(s.load1 || 0).toFixed(2)],
        ["Load 5m", Number(s.load5 || 0).toFixed(2)],
        ["Load 15m", Number(s.load15 || 0).toFixed(2)],
        ["Processes", s.process_count],
        ["Threads", s.thread_count],
        ["Open files", s.open_files],
        ["Kernel", s.kernel],
        ["Architecture", s.arch]
    ]);

    // Processes Tab update
    if (activeTab === "processes") {
        renderProcessTable(procList);
    }

    // Peripherals Rendering: Displays, USB & Thunderbolt
    const periph = d.peripherals || {};
    if ($("displayList")) {
        const dps = periph.displays || [];
        $("displayList").innerHTML = dps.length
            ? dps.map(dp => `
                <div class="sensor-row">
                    <div><b>${esc(dp.connector)}</b><small>Primary: ${esc(dp.resolution)}</small></div>
                    <span class="badge">${(dp.available_modes || []).length} modes</span>
                </div>
            `).join("")
            : '<div class="empty">No external displays connected.</div>';
    }
    if ($("usbList")) {
        const usbs = periph.usb || [];
        const tbs = periph.thunderbolt || [];
        let html = "";
        if (usbs.length) {
            html += usbs.map(u => `
                <div class="sensor-row"><div><b>${esc(u)}</b></div><span class="badge">USB</span></div>
            `).join("");
        }
        if (tbs.length) {
            html += tbs.map(t => `
                <div class="sensor-row"><div><b>⚡ ${esc(t.device)}</b></div><span class="badge" style="color:var(--accent);">${t.authorized ? "Authorized" : "Unauthorized"}</span></div>
            `).join("");
        }
        $("usbList").innerHTML = html || '<div class="empty">No USB or Thunderbolt devices found.</div>';
    }

    renderInterfaces(n.interfaces);
    renderDisks(d.filesystems);
    renderSensors(d.sensors);
    renderGpu(d.gpu);
    renderBattery(d.battery);

    refreshActiveTabCharts();
}

// --- Active Network Target Cards with Latency Badges ---
function updateNetworkTargets(d) {
    const grid = $("targetGrid");
    if (!grid) return;
    if (!d.results || d.results.length === 0) {
        grid.innerHTML = '<div class="empty">No targets configured in targets.json</div>';
        return;
    }
    grid.innerHTML = d.results.map(t => {
        const statusClass = t.success ? 'status-up' : 'status-down';
        let latencyBadgeClass = 'latency-slow';
        if (t.latency_ms !== null) {
            if (t.latency_ms < 35) latencyBadgeClass = 'latency-fast';
            else if (t.latency_ms < 100) latencyBadgeClass = 'latency-good';
            else if (t.latency_ms < 250) latencyBadgeClass = 'latency-warn';
        }
        const latencyText = t.latency_ms !== null ? `${t.latency_ms} ms` : 'Down';
        return `
        <div class="target-card">
            <div class="target-header">
                <span class="target-name">${esc(t.name)}</span>
                <div>
                    <span class="target-method">${esc(t.method)}</span>
                    <button onclick="deleteTarget('${t.id}')" style="background:none; border:none; color:var(--danger); cursor:pointer; margin-left:8px; font-weight:bold;" title="Delete Monitor">✕</button>
                </div>
            </div>
            <div class="target-status">
                <div><span class="status-indicator ${statusClass}"></span> ${esc(t.message)}</div>
                <span class="latency-badge ${latencyBadgeClass}">${latencyText}</span>
            </div>
        </div>`;
    }).join("");
}

// --- Enhanced Interface Cards with State & MTU ---
function renderInterfaces(x) {
    const el = $("interfaces");
    if (!el) return;
    el.innerHTML = Object.entries(x || {}).map(([k, v]) => {
        const isUp = (v.state || "up").toLowerCase() === "up";
        const isLoopback = k === "lo" || v.address === "127.0.0.1";
        
        let stateLabel = isUp ? "ONLINE" : "OFFLINE";
        let stateColor = isUp ? "var(--accent2)" : "var(--danger)";
        
        if (isLoopback) {
            stateLabel = "LOOPBACK";
            stateColor = "var(--accent)";
        }

        const ipDisplay = v.address || (isLoopback ? "127.0.0.1" : "No IP Assigned");

        return `
        <div class="interface-row">
            <div>
                <b>${esc(k)}</b>
                <span class="badge" style="margin-left:6px; color:${stateColor}; border-color:${stateColor};">${stateLabel}</span>
                <small>${esc(ipDisplay)} • MTU ${esc(v.mtu || "1500")}</small>
            </div>
            <span style="text-align:right;">
                <span style="color:var(--accent2);">↓ ${fmtRate(v.rx_bytes_per_sec || 0)}</span><br>
                <span style="color:var(--warn);">↑ ${fmtRate(v.tx_bytes_per_sec || 0)}</span>
            </span>
        </div>`;
    }).join("") || '<div class="empty">No active interfaces.</div>';
}

// --- Enhanced Disks with Visual Capacity Fill Bars ---
function renderDisks(x) {
    const el = $("filesystems");
    if (!el) return;
    el.innerHTML = (x || []).map(v => {
        const pct = v.percent || 0;
        let barColor = "var(--accent2)";
        if (pct >= 85) barColor = "var(--danger)";
        else if (pct >= 70) barColor = "var(--warn)";

        return `
        <div class="disk-row">
            <div>
                <b>${esc(v.mountpoint)}</b>
                <small>${esc(v.device || "")} (${esc(v.fstype || "fs")})</small>
            </div>
            <span style="text-align:right;">
                <b>${fmtPct(v.percent)}</b><br>
                <small>${fmtBytes(v.used)} / ${fmtBytes(v.total)}</small>
            </span>
            <div class="disk-progress-bar">
                <div class="disk-progress-fill" style="width: ${Math.min(pct, 100)}%; background: ${barColor};"></div>
            </div>
        </div>`;
    }).join("") || '<div class="empty">No filesystems reported.</div>';
}

function renderSensors(x) {
    let t = {}, f = {}, v = {}, o = {};
    Object.entries(x || {}).forEach(([k, z]) => {
        if (z.type === "temperature") t[k] = z;
        else if (z.type === "fan") f[k] = z;
        else if (z.type === "voltage") v[k] = z;
        else o[k] = z;
    });
    rows("temperatures", t);
    rows("fans", f);
    rows("voltages", v);
    rows("otherSensors", o);
}

function renderBattery(b) {
    const el = $("batteryContent");
    if (!el) return;
    if (!b || !b.length) {
        el.innerHTML = '<article class="panel"><div class="empty">No battery detected on this host.</div></article>';
        return;
    }
    el.innerHTML = b.map(x => `
        <article class="panel">
            <div class="panel-title"><h2>Battery (${esc(x.name)})</h2><span>${esc(x.status)} • ${x.power_rate_w} W</span></div>
            <div class="sensor-list">
                <div class="sensor-row"><div>Health (Capacity)</div><span class="value">${x.health_capacity ?? "—"}%</span></div>
                <div class="sensor-row"><div>Charge Level</div><span class="value">${x.percentage}%</span></div>
                <div class="sensor-row"><div>Charge Cycles</div><span class="value">${x.cycles ?? "—"}</span></div>
                <div class="sensor-row"><div>Energy Design</div><span class="value">${x.energy_design_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Energy Full</div><span class="value">${x.energy_full_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Energy Remaining</div><span class="value">${x.energy_now_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Terminal Voltage</div><span class="value">+${x.voltage_v ?? "—"} V</span></div>
            </div>
        </article>
    `).join("");
}

function renderGpu(g) {
    const el = $("gpuContent");
    if (!el) return;
    if (!g || !g.length) {
        el.innerHTML = '<article class="panel"><div class="empty">No dedicated GPU detected.</div></article>';
        return;
    }
    el.innerHTML = g.map(x => `
        <article class="panel">
            <div class="panel-title"><h2>${esc(x.name)}</h2><span>${x.temperature_c} °C • ${x.power_watts} W</span></div>
            <div class="sensor-list">
                <div class="sensor-row"><div>Driver Version</div><span class="value">${esc(x.driver_version)}</span></div>
                <div class="sensor-row"><div>vBIOS Version</div><span class="value">${esc(x.vbios)}</span></div>
                <div class="sensor-row"><div>PCI Bus ID</div><span class="value">${esc(x.pci_bus)}</span></div>
                <div class="sensor-row"><div>Graphics Clock</div><span class="value">${x.clock_core_mhz} MHz</span></div>
                <div class="sensor-row"><div>Memory Clock</div><span class="value">${x.clock_mem_mhz} MHz</span></div>
                <div class="sensor-row"><div>Encoder / Decoder</div><span class="value">${x.encoder_utilization}% / ${x.decoder_utilization}%</span></div>
                <div class="sensor-row"><div>VRAM Allocation</div><span class="value">${x.memory_used_mb} MB / ${x.memory_total_mb} MB</span></div>
            </div>
        </article>
    `).join("");
}

// --- WebSocket Connection & Lifecycle ---
function connect() {
    clearTimeout(retryTimer);
    ws = new WebSocket(`${protocol}${host}/ws?token=${encodeURIComponent(authToken)}`);

    ws.onopen = () => {
        const el = $("daemonStatus");
        if (el) {
            el.className = "status connected";
        }
        if ($("daemonStatusText")) $("daemonStatusText").textContent = "Connected";
    };

    ws.onclose = (e) => {
        const el = $("daemonStatus");
        if (el) {
            el.className = "status disconnected";
        }
        if ($("daemonStatusText")) $("daemonStatusText").textContent = "Sleeping";

        if (e.code === 1008) {
            sessionStorage.removeItem("dash_token");
            authToken = prompt("Incorrect password. Please enter WindyDash Password:");
            if (authToken !== null) {
                sessionStorage.setItem("dash_token", authToken);
                connect();
            }
            return;
        }
        retryTimer = setTimeout(connect, 3000);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "hardware") {
                updateHardware(data);
            } else if (data.type === "network_targets") {
                updateNetworkTargets(data);
            }
        } catch (err) {
            console.error("Failed to parse websocket data:", err);
        }
    };

    ws.onerror = () => ws.close();
}

initCharts();
connect();

// --- Device Status & Target Management ---
async function fetchDeviceStatus() {
    try {
        const res = await fetch("/api/device-status", {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-Token": authToken
            }
        });
        if (!res.ok) throw new Error("Status fetch error");
        const data = await res.json();
        const grid = $("deviceStatusGrid");
        if (grid) {
            grid.innerHTML = `
                <div class="stat"><label>Hostname</label><b>${esc(data.hostname)}</b></div>
                <div class="stat"><label>Local IP</label><b>${esc(data.local_ip)}</b></div>
                <div class="stat"><label>Gateway</label><b>${esc(data.gateway)}</b></div>
                <div class="stat"><label>Interface</label><b>${esc(data.interface)}</b></div>
                <div class="stat"><label>DNS Servers</label><b>${esc(data.dns)}</b></div>
                <div class="stat"><label>Public IP</label><b>${esc(data.public_ip)}</b></div>
            `;
        }
        if (data.local_ip && data.local_ip !== "Unknown" && $("discoverySubnet")) {
            const parts = data.local_ip.split(".");
            if (parts.length === 4) {
                $("discoverySubnet").value = `${parts[0]}.${parts[1]}.${parts[2]}.0/24`;
            }
        }
    } catch (e) {
        if ($("deviceStatusGrid")) {
            $("deviceStatusGrid").innerHTML = `<div class="empty">Failed to load device network topology.</div>`;
        }
    }
}
fetchDeviceStatus();

// Recheck All Targets Button
if ($("btnRecheckAll")) {
    $("btnRecheckAll").onclick = async () => {
        const btn = $("btnRecheckAll");
        btn.textContent = "Probing…";
        btn.disabled = true;
        try {
            await fetch("/api/targets/recheck", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Auth-Token": authToken }
            });
        } catch (err) {
            console.error("Recheck error:", err);
        } finally {
            setTimeout(() => {
                btn.textContent = "↻ Recheck Now";
                btn.disabled = false;
            }, 1000);
        }
    };
}

const modal = $("targetModal");
const tMethod = $("tMethod");
const tPortContainer = $("tPortContainer");

if ($("openTargetModalBtn") && modal) $("openTargetModalBtn").onclick = () => modal.showModal();
if ($("closeTargetModalBtn") && modal) $("closeTargetModalBtn").onclick = () => modal.close();
if ($("cancelTargetModalBtn") && modal) $("cancelTargetModalBtn").onclick = () => modal.close();

if (tMethod && tPortContainer) {
    tMethod.onchange = () => {
        tPortContainer.style.display = tMethod.value === "tcp" ? "block" : "none";
    };
}

if ($("targetForm")) {
    $("targetForm").onsubmit = async (e) => {
        e.preventDefault();
        const payload = {
            name: $("tName").value,
            host: $("tHost").value,
            method: tMethod.value,
            port: tMethod.value === "tcp" ? parseInt($("tPort").value) : null
        };

        try {
            const res = await fetch("/api/targets", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error("Failed to add target");
            if (modal) modal.close();
            $("targetForm").reset();
            if (tPortContainer) tPortContainer.style.display = "none";
        } catch (err) {
            alert("Target error: " + err.message);
        }
    };
}

window.deleteTarget = async function(id) {
    if (!confirm("Remove this monitor?")) return;
    try {
        const res = await fetch(`/api/targets/${id}`, {
            method: "DELETE",
            headers: {
                "X-Auth-Token": authToken
            }
        });
        if (!res.ok) throw new Error("Delete failed");
    } catch (err) {
        alert("Target error: " + err.message);
    }
};

// --- Diagnostics NDJSON Streaming Engine ---
async function consumeStream(url, payload, outputEl, stateEl = null) {
    outputEl.textContent = "Initiating probe stream...\n";
    if (stateEl) stateEl.textContent = "Running…";
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-Token": authToken
            },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            outputEl.textContent += `\nHTTP Error: ${res.status}`;
            if (stateEl) stateEl.textContent = "Failed";
            return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            let lines = buffer.split("\n");
            buffer = lines.pop();

            for (let line of lines) {
                if (!line.trim()) continue;
                try {
                    let data = JSON.parse(line);
                    if (data.type === "line") outputEl.textContent += data.text + "\n";
                    if (data.type === "host") outputEl.textContent += `[UP] Host Discovered: ${data.ip}\n`;
                    if (data.type === "dns_record") outputEl.textContent += `${data.text}\n`;
                    if (data.type === "port_result") outputEl.textContent += `[${data.open ? 'OPEN' : 'CLOSED'}] ${data.text}\n`;
                    if (data.type === "error") outputEl.textContent += `\n[Error] ${data.text}\n`;
                    if (data.type === "done") {
                        outputEl.textContent += "\n--- Completed ---\n";
                        if (stateEl) stateEl.textContent = "Completed";
                    }
                    outputEl.scrollTop = outputEl.scrollHeight;
                } catch (e) {
                    console.error("Parse error", line);
                }
            }
        }
    } catch (e) {
        outputEl.textContent += `\n[Fatal Error] ${e.message}`;
        if (stateEl) stateEl.textContent = "Error";
    }
}

// 1. Traceroute Button
if ($("btnTrace")) {
    $("btnTrace").onclick = () => {
        const host = $("traceHost").value;
        if (!host) return alert("Please enter a target host or IP.");
        consumeStream("/api/traceroute", { host }, $("traceOutput"), $("traceState"));
    };
}

// 2. Subnet Discovery Button
if ($("btnDiscovery")) {
    $("btnDiscovery").onclick = () => {
        const subnet = $("discoverySubnet").value;
        if (!subnet) return alert("Please enter a valid CIDR subnet (e.g. 192.168.1.0/24).");
        consumeStream("/api/discovery", { subnet }, $("discoveryOutput"), $("discoveryState"));
    };
}

// 3. DNS Resolver Button (NEW)
if ($("btnDns")) {
    $("btnDns").onclick = () => {
        const domain = $("dnsDomain").value;
        if (!domain) return alert("Please enter a domain or IP address.");
        consumeStream("/api/dns", { domain }, $("dnsOutput"), $("dnsState"));
    };
}

// 4. TCP Port Checker Button (NEW)
if ($("btnPortCheck")) {
    $("btnPortCheck").onclick = () => {
        const host = $("portHost").value;
        const port = parseInt($("portNum").value);
        if (!host || !port) return alert("Please provide both target host and valid port.");
        consumeStream("/api/portcheck", { host, port }, $("portOutput"), $("portState"));
    };
}

// 5. Bandwidth Speedtest Button
if ($("btnSpeedtest")) {
    $("btnSpeedtest").onclick = async () => {
        const out = $("speedtestOutput");
        const btn = $("btnSpeedtest");
        const stateEl = $("speedState");

        $("stPing").textContent = "—";
        $("stDown").textContent = "—";
        $("stUp").textContent = "—";
        out.textContent = "Initiating benchmark...\n";
        btn.disabled = true;
        if (stateEl) stateEl.textContent = "Benchmarking…";

        try {
            const res = await fetch("/api/speedtest", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                }
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            const reader = res.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                let lines = buffer.split("\n");
                buffer = lines.pop();

                for (let line of lines) {
                    if (!line.trim()) continue;
                    try {
                        let data = JSON.parse(line);
                        if (data.type === "line" || data.type === "result" || data.type === "ping" || data.type === "done") {
                            if (data.text) out.textContent += data.text + "\n";
                        }
                        if (data.type === "error") out.textContent += `[Error] ${data.text}\n`;
                        if (data.type === "ping") $("stPing").textContent = `${data.ping_ms.toFixed(1)} ms`;
                        if (data.type === "progress" || data.type === "result") {
                            if (data.dir === "down") $("stDown").textContent = `${data.mbps.toFixed(1)} Mbps`;
                            if (data.dir === "up") $("stUp").textContent = `${data.mbps.toFixed(1)} Mbps`;
                        }
                        if (data.type === "done" && stateEl) stateEl.textContent = "Completed";
                        out.scrollTop = out.scrollHeight;
                    } catch (e) {
                        console.error("Speedtest parse error", line);
                    }
                }
            }
        } catch (e) {
            out.textContent += `\n[Fatal] ${e.message}\n`;
            if (stateEl) stateEl.textContent = "Failed";
        } finally {
            btn.disabled = false;
        }
    };
}
