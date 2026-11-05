/**
 * WindyDash - Documentation Site Interactivity
 * Handles click-to-copy, interactive .env generator, and theme switcher.
 */

// --- 1. Clipboard Copy Utility ---
function copyText(text, btnElement) {
    if (!navigator.clipboard) {
        // Fallback for older browsers
        const textarea = document.createElement("textarea");
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        triggerCopyFeedback(btnElement);
        return;
    }

    navigator.clipboard.writeText(text).then(() => {
        triggerCopyFeedback(btnElement);
    }).catch(err => {
        console.error("Failed to copy:", err);
    });
}

function triggerCopyFeedback(btn) {
    if (!btn) return;
    const originalText = btn.innerHTML;
    btn.innerHTML = "✔ COPIED!";
    btn.style.borderColor = "var(--accent2)";
    btn.style.color = "var(--accent2)";
    setTimeout(() => {
        btn.innerHTML = originalText;
        btn.style.borderColor = "";
        btn.style.color = "";
    }, 2000);
}

// Hook all data-copy elements
document.querySelectorAll("[data-copy]").forEach(el => {
    el.onclick = () => {
        const text = el.getAttribute("data-copy");
        copyText(text, el);
    };
});

// --- 2. Interactive .env Generator ---
const inpInterval = document.getElementById("inpInterval");
const inpHost = document.getElementById("inpHost");
const inpPort = document.getElementById("inpPort");
const inpPassword = document.getElementById("inpPassword");
const inpRate = document.getElementById("inpRate");
const envOutput = document.getElementById("envOutput");
const btnCopyEnv = document.getElementById("btnCopyEnv");

function updateEnvPreview() {
    if (!envOutput) return;

    const interval = (inpInterval && inpInterval.value.trim()) || "1.0";
    const host = (inpHost && inpHost.value.trim()) || "127.0.0.1";
    const port = (inpPort && inpPort.value.trim()) || "4242";
    const password = inpPassword ? inpPassword.value.trim() : "";
    const rate = (inpRate && inpRate.value.trim()) || "25";

    let passLine = "# DASH_PASSWORD= (Operators managed in SQLite vault)";
    if (password) {
        passLine = `DASH_PASSWORD=${password}`;
    }

    const content = `# WindyDash Daemon Configuration (.env)
# ----------------------------------------
SYSMON_INTERVAL=${interval}
SYSMON_HOST=${host}
SYSMON_PORT=${port}
${passLine}
RATE_LIMIT=${rate}
RATE_WINDOW=5.0
`;

    envOutput.textContent = content;
    if (btnCopyEnv) {
        btnCopyEnv.setAttribute("data-copy", content);
    }
}

[inpInterval, inpHost, inpPort, inpPassword, inpRate].forEach(input => {
    if (input) {
        input.addEventListener("input", updateEnvPreview);
    }
});
updateEnvPreview();

if (btnCopyEnv) {
    btnCopyEnv.onclick = () => {
        copyText(envOutput.textContent, btnCopyEnv);
    };
}

// --- 3. Vintage Theme Toggle ---
const themeBtn = document.getElementById("themeToggleBtn");
const themeIcon = document.getElementById("themeIcon");

function applyTheme(isLight) {
    document.documentElement.classList.toggle("light", isLight);
    if (themeIcon) {
        themeIcon.textContent = isLight ? "☾" : "☼";
    }
    window.dispatchEvent(new Event("windydash-theme-change"));
}

if (themeBtn) {
    themeBtn.onclick = () => {
        const currentlyLight = document.documentElement.classList.contains("light");
        const nextLight = !currentlyLight;
        localStorage.setItem("windydash-site-theme", nextLight ? "light" : "dark");
        applyTheme(nextLight);
    };
}

// Initialize theme from preference
const savedTheme = localStorage.getItem("windydash-site-theme");
if (savedTheme === "light") {
    applyTheme(true);
} else {
    applyTheme(false);
}

// --- 4. Mobile navigation ---
const navToggle = document.getElementById("navToggle");
const navLinks = document.getElementById("navLinks");
if (navToggle && navLinks) {
    const menuIcon = navToggle.querySelector(".menu-icon");
    const setMenuState = (open) => {
        navLinks.classList.toggle("is-open", open);
        navToggle.setAttribute("aria-expanded", String(open));
        if (menuIcon) menuIcon.textContent = open ? "×" : "☰";
    };
    navToggle.addEventListener("click", () => {
        setMenuState(!navLinks.classList.contains("is-open"));
    });
    navLinks.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
        setMenuState(false);
    }));
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") setMenuState(false);
    });
    window.addEventListener("resize", () => {
        if (window.innerWidth > 1100) setMenuState(false);
    }, { passive: true });
}

// --- 5. Atmospheric Mt. Fuji and torii ink sketch ---
function initBackgroundSketch() {
    const canvas = document.getElementById("bg-sketch-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const render = () => {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = window.innerWidth;
        const h = window.innerHeight;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        const light = document.documentElement.classList.contains("light");
        const s = Math.max(.62, Math.min(Math.min(w / 1200, h / 800), 1.1));
        const cx = w * .5;
        const base = h * .53 + 85 * s;
        const peak = base - 180 * s;
        const ink = light ? "rgba(55,45,38,.14)" : "rgba(255,255,255,.08)";
        const accent = light ? "rgba(202,62,38,.72)" : "rgba(223,90,53,.62)";

        const sun = ctx.createRadialGradient(cx, peak + 22 * s, 0, cx, peak + 22 * s, 72 * s);
        sun.addColorStop(0, light ? "rgba(223,90,53,.2)" : "rgba(223,90,53,.16)");
        sun.addColorStop(1, "rgba(223,90,53,0)");
        ctx.fillStyle = sun;
        ctx.beginPath();
        ctx.arc(cx, peak + 22 * s, 72 * s, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.moveTo(cx - 240 * s, base);
        ctx.bezierCurveTo(cx - 130 * s, base - 25 * s, cx - 65 * s, peak + 45 * s, cx - 22 * s, peak);
        ctx.lineTo(cx + 22 * s, peak);
        ctx.bezierCurveTo(cx + 65 * s, peak + 45 * s, cx + 130 * s, base - 25 * s, cx + 240 * s, base);
        ctx.closePath();
        ctx.fillStyle = light ? "rgba(55,45,38,.06)" : "rgba(255,255,255,.035)";
        ctx.fill();
        ctx.strokeStyle = ink;
        ctx.lineWidth = .9;
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(cx - 56 * s, peak + 48 * s);
        [[-45,42],[-37,54],[-27,40],[-17,57],[-8,43],[0,61],[8,42],[17,58],[27,41],[37,53],[45,40]]
            .forEach(([x, y]) => ctx.lineTo(cx + x * s, peak + y * s));
        ctx.lineTo(cx + 56 * s, peak + 48 * s);
        ctx.lineTo(cx + 22 * s, peak);
        ctx.lineTo(cx - 22 * s, peak);
        ctx.closePath();
        ctx.fillStyle = light ? "rgba(255,255,255,.8)" : "rgba(255,255,255,.13)";
        ctx.fill();

        const gy = base + 12 * s;
        ctx.fillStyle = accent;
        ctx.fillRect(cx - 29 * s, gy - 5 * s, 12 * s, 5 * s);
        ctx.fillRect(cx + 17 * s, gy - 5 * s, 12 * s, 5 * s);
        ctx.beginPath();
        ctx.moveTo(cx - 29 * s, gy - 5 * s); ctx.lineTo(cx - 22 * s, gy - 62 * s);
        ctx.lineTo(cx - 17 * s, gy - 62 * s); ctx.lineTo(cx - 22 * s, gy - 5 * s);
        ctx.moveTo(cx + 29 * s, gy - 5 * s); ctx.lineTo(cx + 22 * s, gy - 62 * s);
        ctx.lineTo(cx + 17 * s, gy - 62 * s); ctx.lineTo(cx + 22 * s, gy - 5 * s);
        ctx.fill();
        ctx.fillRect(cx - 49 * s, gy - 68 * s, 98 * s, 5 * s);
        ctx.fillRect(cx - 38 * s, gy - 49 * s, 76 * s, 5 * s);
        ctx.beginPath();
        ctx.moveTo(cx - 57 * s, gy - 74 * s);
        ctx.quadraticCurveTo(cx, gy - 68 * s, cx + 57 * s, gy - 74 * s);
        ctx.lineTo(cx + 55 * s, gy - 68 * s);
        ctx.quadraticCurveTo(cx, gy - 63 * s, cx - 55 * s, gy - 68 * s);
        ctx.closePath();
        ctx.fill();
    };
    render();
    let timer;
    window.addEventListener("resize", () => {
        clearTimeout(timer);
        timer = setTimeout(render, 120);
    }, { passive: true });
    window.addEventListener("windydash-theme-change", render);
}
initBackgroundSketch();

// --- 6. Lightweight falling maple leaves ---
function initMapleLeaves() {
    const canvas = document.getElementById("maple-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let w = window.innerWidth, h = window.innerHeight;
    const leaves = Array.from({ length: 28 }, () => ({
        x: Math.random() * w, y: Math.random() * h, size: 8 + Math.random() * 11,
        speed: .7 + Math.random() * 1.2, drift: -0.5 + Math.random(), angle: Math.random() * 6.28
    }));
    const resize = () => { w = canvas.width = window.innerWidth; h = canvas.height = window.innerHeight; };
    resize();
    window.addEventListener("resize", resize, { passive: true });
    const draw = () => {
        ctx.clearRect(0, 0, w, h);
        const light = document.documentElement.classList.contains("light");
        const colors = light ? ["#c2410c", "#d97706", "#b91c1c", "#15803d"] : ["#df5a35", "#f2b744", "#d33e38", "#5cd4a8"];
        leaves.forEach(leaf => {
            leaf.y += leaf.speed; leaf.x += leaf.drift + Math.sin(leaf.y * .02) * .7; leaf.angle += .025;
            if (leaf.y > h + 30) { leaf.y = -30; leaf.x = Math.random() * w; }
            ctx.save(); ctx.translate(leaf.x, leaf.y); ctx.rotate(leaf.angle);
            ctx.fillStyle = colors[Math.floor(leaf.size) % colors.length];
            ctx.beginPath(); ctx.moveTo(0, -leaf.size);
            ctx.bezierCurveTo(leaf.size * .8, -leaf.size * .5, leaf.size * .9, leaf.size * .3, 0, leaf.size);
            ctx.bezierCurveTo(-leaf.size * .9, leaf.size * .3, -leaf.size * .8, -leaf.size * .5, 0, -leaf.size);
            ctx.fill(); ctx.restore();
        });
        requestAnimationFrame(draw);
    };
    draw();
}
initMapleLeaves();
