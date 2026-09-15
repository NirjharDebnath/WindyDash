const protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
const WS_URL = protocol + (window.location.host || "localhost:8765") + "/ws";const MAX_POINTS = 60;
let ws, retryTimer, latest = null;
const history = {cpu:[], mem:[], swap:[], rx:[], tx:[], read:[], write:[], load:[], time:[]};

const $ = id => document.getElementById(id);
const fmtBytes = (n=0) => {
    const u = ["B", "KB", "MB", "GB", "TB"];
    if (!n) return "0 B";
    let i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
    return `${(n / 1024**i).toFixed(i ? 1 : 0)} ${u[i]}`;
};
const fmtRate = n => `${fmtBytes(n)}/s`;
const fmtPct = n => `${Number(n||0).toFixed(1)}%`;
const fmtTemp = n => `${Number(n||0).toFixed(1)} °C`;
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

function chart(id, label, datasets, unit="") {
    return new Chart($(id), {
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

function theme() {
    document.documentElement.classList.toggle("light", localStorage.getItem("sysmon-theme") === "light");
    Object.values(charts).forEach(c => { if (c) c.update(); });
}
$("themeToggle").onclick = () => {
    localStorage.setItem("sysmon-theme", document.documentElement.classList.contains("light") ? "dark" : "light");
    theme();
};
theme();

// Tab switching
document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tab, .tab-panel").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    const target = $(b.dataset.tab);
    if (target) target.classList.add("active");
});

function initCharts() {
    charts.ovCpu = chart("overviewCpuChart", "CPU %", [{label: "CPU", data: history.cpu, borderColor: "#7c9cff"}], "%");
    charts.ovMem = chart("overviewMemChart", "Memory %", [{label: "Memory", data: history.mem, borderColor: "#4fd1c5"}], "%");
    charts.core = new Chart($("coreChart"), {
        type: "bar",
        data: { labels: [], datasets: [{ label: "Usage %", data: [], backgroundColor: "#7c9cff" }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, scales: { y: { beginAtZero: true, max: 100 } } }
    });
    charts.freq = chart("freqChart", "MHz", [{label: "Frequency", data: [], borderColor: "#f6c85f"}], "");
    charts.memory = new Chart($("memoryChart"), {
        type: "doughnut",
        data: { labels: ["Used", "Available"], datasets: [{ data: [0, 100], backgroundColor: ["#7c9cff", "#26313c"], borderWidth: 0 }] },
        options: { responsive: true, maintainAspectRatio: false }
    });
    charts.swap = chart("swapChart", "Swap %", [{label: "Swap", data: history.swap, borderColor: "#ff6b7a"}], "%");
    charts.load = chart("loadChart", "Load", [{label: "1m", data: history.load, borderColor: "#7c9cff"}]);
    charts.net = chart("networkChart", "Network", [{label: "RX", data: history.rx, borderColor: "#4fd1c5"}, {label: "TX", data: history.tx, borderColor: "#7c9cff"}], "");
    charts.disk = chart("diskChart", "Disk", [{label: "Read", data: history.read, borderColor: "#4fd1c5"}, {label: "Write", data: history.write, borderColor: "#f6c85f"}], "");
}

function updateChart(c) {
    if (!c) return;
    c.data.labels = labels();
    c.update("none");
}

function stats(id, items) {
    const el = $(id);
    if (el) el.innerHTML = items.map(([k, v]) => `<div class="stat"><label>${esc(k)}</label><b>${esc(v)}</b></div>`).join("");
}

function rows(id, obj, suffix="") {
    const el = $(id);
    if (!el) return;
    let a = Object.entries(obj || {});
    el.innerHTML = a.length
        ? a.map(([k, v]) => `<div class="sensor-row"><div>${esc(k)}<small>${esc(v.description || "")}</small></div><span class="value">${esc(v.value ?? v)}${suffix}</span></div>`).join("")
        : `<div class="empty">No sensors reported.</div>`;
}

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

    $("daemonStatus").textContent = "● Connected";
    $("daemonStatus").className = "status connected";
    $("lastUpdate").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    $("hostInfo").textContent = `${s.hostname || "Linux"} • kernel ${s.kernel || "—"} • uptime ${s.uptime_human || "—"}`;

    $("ovCpu").textContent = fmtPct(c.global_percent);
    $("ovCpuFreq").textContent = `${(c.freq_current_mhz || 0).toFixed(0)} MHz • ${c.logical_cores || 0} threads`;
    $("ovMem").textContent = fmtPct(m.percent);
    $("ovMemDetail").textContent = `${fmtBytes(m.used)} / ${fmtBytes(m.total)}`;
    $("ovLoad").textContent = Number(s.load1 || 0).toFixed(2);
    $("ovUptime").textContent = s.uptime_human || "—";
    $("ovNet").textContent = `↓ ${fmtRate(n.rx_bytes_per_sec || 0)}`;
    $("ovNetDetail").textContent = `↑ ${fmtRate(n.tx_bytes_per_sec || 0)}`;

    updateChart(charts.ovCpu);
    updateChart(charts.ovMem);
    updateChart(charts.swap);
    updateChart(charts.load);
    updateChart(charts.net);
    updateChart(charts.disk);

    // Processor Tab
    charts.core.data.labels = (c.per_core_percent || []).map((_, i) => `C${i}`);
    charts.core.data.datasets[0].data = c.per_core_percent || [];
    charts.core.update("none");

    charts.freq.data.labels = (c.per_core_freq_mhz || []).map((_, i) => `C${i}`);
    charts.freq.data.datasets[0].data = c.per_core_freq_mhz || [];
    charts.freq.update("none");

    $("coreCount").textContent = `${c.logical_cores || 0} logical cores`;
    stats("processorStats", [
        ["Physical cores", c.physical_cores],
        ["Logical cores", c.logical_cores],
        ["Current frequency", `${(c.freq_current_mhz || 0).toFixed(0)} MHz`],
        ["Context switches", c.ctx_switches]
    ]);

    // Memory Tab
    charts.memory.data.datasets[0].data = [m.used || 0, m.available || 0];
    charts.memory.update("none");
    $("memPercent").textContent = fmtPct(m.percent);
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

    // System Tab
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
    $("quickSystem").innerHTML = [["Hostname", s.hostname], ["OS", s.os], ["Kernel", s.kernel], ["Uptime", s.uptime_human]]
        .map(x => `<div>${esc(x[0])}</div><div>${esc(x[1])}</div>`).join("");
    $("systemInfo").innerHTML = [["Boot", s.boot_time], ["CPU model", c.model], ["Python", s.python]]
        .map(x => `<div>${esc(x[0])}</div><div>${esc(x[1])}</div>`).join("");

    // Processes Tab
    const p = d.processes || [];
    $("processCount").textContent = `Top ${p.length}`;
    $("processTable").innerHTML = p.map(x => `
        <tr>
            <td>${x.pid}</td>
            <td>${esc(x.username || "—")}</td>
            <td>${esc(x.name || "—")}</td>
            <td>${Number(x.cpu_percent || 0).toFixed(1)}%</td>
            <td>${Number(x.memory_percent || 0).toFixed(1)}%</td>
            <td>${x.num_threads || 0}</td>
            <td><span class="badge">${esc(x.status)}</span></td>
        </tr>`).join("");
    $("topProcess").innerHTML = p.slice(0, 4).map((proc, i) => `
        <div class="process-item">
            <div class="process-rank">${i + 1}</div>

            <div class="process-info">
                <div class="process-name">
                    ${esc(proc.name || "—")}
                </div>

                <div class="process-meta">
                    PID ${proc.pid}
                    • ${Number(proc.cpu_percent || 0).toFixed(1)}% CPU
                    • ${Number(proc.memory_percent || 0).toFixed(1)}% RAM
                </div>
            </div>
        </div>
    `).join("") || "—";

    // In updateHardware(d):
    const periph = d.peripherals || {};
    if ($("displayList")) {
        $("displayList").innerHTML = (p.displays || []).map(dp => `
            <div class="sensor-row"><div><b>${esc(dp.connector)}</b><small>Primary: ${esc(dp.resolution)}</small></div>
            <span class="badge">${dp.available_modes.length} modes</span></div>
        `).join("") || '<div class="empty">No displays connected.</div>';
    }
    if ($("usbList")) {
        $("usbList").innerHTML = (p.usb || []).map(u => `
            <div class="sensor-row"><div>${esc(u)}</div><span class="badge">USB</span></div>
        `).join("") || '<div class="empty">No external USB devices.</div>';
    }

    renderInterfaces(n.interfaces);
    renderDisks(d.filesystems);
    renderSensors(d.sensors);
    renderGpu(d.gpu);
    renderBattery(d.battery);
}

function updateNetworkTargets(d) {
    const grid = $("targetGrid");
    if (!grid) return;
    if (!d.results || d.results.length === 0) {
        grid.innerHTML = '<div class="empty">No targets configured in targets.json</div>';
        return;
    }
    grid.innerHTML = d.results.map(t => {
        const statusClass = t.success ? 'status-up' : 'status-down';
        const latencyText = t.latency_ms !== null ? `${t.latency_ms} ms` : '—';
        return `
        <div class="target-card">
            <div class="target-header">
                <span class="target-name">${esc(t.name)}</span>
                <div>
                    <span class="target-method">${esc(t.method)}</span>
                    <button onclick="deleteTarget('${t.id}')" style="background:none; border:none; color:var(--danger); cursor:pointer; margin-left:8px;" title="Delete">✕</button>
                </div>
            </div>
            <div class="target-status">
                <div><span class="status-indicator ${statusClass}"></span> ${esc(t.message)}</div>
                <strong>${latencyText}</strong>
            </div>
        </div>`;
    }).join("");
}

function renderInterfaces(x) {
    $("interfaces").innerHTML = Object.entries(x || {}).map(([k, v]) => `
        <div class="interface-row">
            <div><b>${esc(k)}</b><small>${v.address || "interface"}</small></div>
            <span>↓ ${fmtRate(v.rx_bytes_per_sec || 0)}<br>↑ ${fmtRate(v.tx_bytes_per_sec || 0)}</span>
        </div>`).join("") || '<div class="empty">No interfaces.</div>';
}

function renderDisks(x) {
    $("filesystems").innerHTML = (x || []).map(v => `
        <div class="disk-row">
            <div><b>${esc(v.mountpoint)}</b><small>${esc(v.device || "")}</small></div>
            <span>${fmtPct(v.percent)}<br>${fmtBytes(v.used)} / ${fmtBytes(v.total)}</span>
        </div>`).join("") || '<div class="empty">No filesystems.</div>';
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
    $("hotSensors").innerHTML = Object.values(t)
    .sort((a, b) => b.value - a.value)
    .slice(0, 4)
    .map((z, i) => `
        <div class="sensor-item">
            <div class="sensor-rank">${i + 1}</div>

            <div class="sensor-info">
                <div class="sensor-name">
                    ${esc(z.label)}
                </div>
                <div class="sensor-meta">
                    Temperature sensor
                </div>
            </div>

            <div class="sensor-value">
                ${fmtTemp(z.value)}
            </div>
        </div>
    `)
    .join("") || "No temperature sensors";
}

function renderBattery(b) {
    const el = $("batteryContent");
    if (!b || !b.length) {
        el.innerHTML = '<article class="panel"><div class="empty">No battery detected.</div></article>';
        return;
    }
    el.innerHTML = b.map(x => `
        <article class="panel">
            <div class="panel-title"><h2>Battery (${esc(x.name)})</h2><span>${esc(x.status)} • ${x.power_rate_w} W</span></div>
            <div class="sensor-list">
                <div class="sensor-row"><div>Health (Capacity)</div><span class="value">${x.health_capacity ?? "—"}%</span></div>
                <div class="sensor-row"><div>Percentage</div><span class="value">${x.percentage}%</span></div>
                <div class="sensor-row"><div>Cycles</div><span class="value">${x.cycles ?? "—"}</span></div>
                <div class="sensor-row"><div>Energy (Design)</div><span class="value">${x.energy_design_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Energy (Full)</div><span class="value">${x.energy_full_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Energy (Now)</div><span class="value">${x.energy_now_wh ?? "—"} Wh</span></div>
                <div class="sensor-row"><div>Voltage</div><span class="value">+${x.voltage_v ?? "—"} V</span></div>
            </div>
        </article>
    `).join("");
}

function renderGpu(g) {
    const el = $("gpuContent");
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
                <div class="sensor-row"><div>PCI Bus</div><span class="value">${esc(x.pci_bus)}</span></div>
                <div class="sensor-row"><div>Core Frequency</div><span class="value">${x.clock_core_mhz} MHz</span></div>
                <div class="sensor-row"><div>Memory Frequency</div><span class="value">${x.clock_mem_mhz} MHz</span></div>
                <div class="sensor-row"><div>Encoder / Decoder Utilization</div><span class="value">${x.encoder_utilization}% / ${x.decoder_utilization}%</span></div>
                <div class="sensor-row"><div>VRAM Used / Total</div><span class="value">${x.memory_used_mb} MB / ${x.memory_total_mb} MB</span></div>
            </div>
        </article>
    `).join("");
}

function connect() {
    clearTimeout(retryTimer);
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        $("daemonStatus").textContent = "● Connected";
        $("daemonStatus").className = "status connected";
    };

    ws.onmessage = e => {
        try {
            const data = JSON.parse(e.data);
            if (data.type === "hardware" || data.cpu) {
                updateHardware(data);
            } else if (data.type === "network_targets") {
                updateNetworkTargets(data);
            }
        } catch (err) {
            console.error(err);
        }
    };

    ws.onclose = () => {
        $("daemonStatus").textContent = "● Offline";
        $("daemonStatus").className = "status disconnected";
        retryTimer = setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();
}

initCharts();
connect();

// --- Device Status & Target Management Handlers ---

async function fetchDeviceStatus() {
    try {
        const res = await fetch("/api/device-status");
        const data = await res.json();
        $("deviceStatusGrid").innerHTML = `
            <div class="stat"><label>Hostname</label><b>${esc(data.hostname)}</b></div>
            <div class="stat"><label>Local IP</label><b>${esc(data.local_ip)}</b></div>
            <div class="stat"><label>Gateway</label><b>${esc(data.gateway)}</b></div>
            <div class="stat"><label>Interface</label><b>${esc(data.interface)}</b></div>
            <div class="stat"><label>DNS Servers</label><b>${esc(data.dns)}</b></div>
            <div class="stat"><label>Public IP</label><b>${esc(data.public_ip)}</b></div>
        `;
        // Pre-fill subnet discovery if valid local IPv4 detected
        if (data.local_ip && data.local_ip !== "Unknown") {
            const parts = data.local_ip.split(".");
            if (parts.length === 4) {
                $("discoverySubnet").value = `${parts[0]}.${parts[1]}.${parts[2]}.0/24`;
            }
        }
    } catch (e) {
        $("deviceStatusGrid").innerHTML = `<div class="empty">Failed to load device network topology.</div>`;
    }
}
fetchDeviceStatus();

const modal = $("targetModal");
const tMethod = $("tMethod");
const tPortContainer = $("tPortContainer");

$("openTargetModalBtn").onclick = () => modal.showModal();
$("closeTargetModalBtn").onclick = () => modal.close();

tMethod.onchange = () => {
    tPortContainer.style.display = tMethod.value === "tcp" ? "block" : "none";
};

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
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error("Failed to add target");
        modal.close();
        $("targetForm").reset();
        tPortContainer.style.display = "none";
    } catch (err) {
        alert("Target error: " + err.message);
    }
};

window.deleteTarget = async function(id) {
    if (!confirm("Remove this monitor?")) return;
    try {
        const res = await fetch(`/api/targets/${id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Delete failed");
    } catch (err) {
        alert("Target error: " + err.message);
    }
};

// --- Diagnostics NDJSON Streaming ---

async function consumeStream(url, payload, outputEl) {
    outputEl.textContent = "Initiating probe...\n";
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
            outputEl.textContent += `\nHTTP error: ${res.status}`;
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
                    if (data.type === "error") outputEl.textContent += `\n[Error] ${data.text}\n`;
                    if (data.type === "done") outputEl.textContent += "\n--- Completed ---\n";
                    outputEl.scrollTop = outputEl.scrollHeight;
                } catch (e) {
                    console.error("Parse error", line);
                }
            }
        }
    } catch (e) {
        outputEl.textContent += `\n[Fatal Error] ${e.message}`;
    }
}

$("btnTrace").onclick = () => {
    const host = $("traceHost").value;
    if (!host) return alert("Please enter a target host or IP.");
    consumeStream("/api/traceroute", { host }, $("traceOutput"));
};

$("btnDiscovery").onclick = () => {
    const subnet = $("discoverySubnet").value;
    if (!subnet) return alert("Please enter a valid CIDR subnet (e.g. 192.168.1.0/24).");
    consumeStream("/api/discovery", { subnet }, $("discoveryOutput"));
};

$("btnSpeedtest").onclick = async () => {
    const out = $("speedtestOutput");
    const btn = $("btnSpeedtest");
    
    $("stPing").textContent = "—";
    $("stDown").textContent = "—";
    $("stUp").textContent = "—";
    out.textContent = "Initiating benchmark...\n";
    btn.disabled = true;
    
    try {
        const res = await fetch("/api/speedtest", { method: "POST" });
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
                    
                    // Live UI Updates
                    if (data.type === "ping") $("stPing").textContent = `${data.ping_ms.toFixed(1)} ms`;
                    if (data.type === "progress" || data.type === "result") {
                        if (data.dir === "down") $("stDown").textContent = `${data.mbps.toFixed(1)} Mbps`;
                        if (data.dir === "up") $("stUp").textContent = `${data.mbps.toFixed(1)} Mbps`;
                    }
                    
                    out.scrollTop = out.scrollHeight;
                } catch (e) {
                    console.error("Speedtest parse error", line);
                }
            }
        }
    } catch (e) {
        out.textContent += `\n[Fatal] ${e.message}\n`;
    } finally {
        btn.disabled = false;
    }
};

