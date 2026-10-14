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
    const password = (inpPassword && inpPassword.value.trim()) || "SecretMasterPassword";
    const rate = (inpRate && inpRate.value.trim()) || "25";

    const content = `# WindyDash Daemon Configuration (.env)
# ----------------------------------------
SYSMON_INTERVAL=${interval}
SYSMON_HOST=${host}
SYSMON_PORT=${port}
DASH_PASSWORD=${password}
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
