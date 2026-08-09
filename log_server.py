#!/usr/bin/env python3
"""
Liquor-Bot Remote Log Viewer + Diagnostic Viewer
Captures all bot logs and diagnostic snapshots for real-time remote viewing.
Auto-starts with the bot. Open http://127.0.0.1:5051 in a browser.
"""

import base64
import json
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime
from pathlib import Path

PORT = 5051
MAX_LOGS = 2000

# In-memory stores
log_entries = []
log_lock = threading.Lock()
diagnostic_snapshots = []  # list of {label, timestamp, screenshot_b64, report, html_snippet}
diag_lock = threading.Lock()


class LogCollector(logging.Handler):
    """Captures all log records into the in-memory store."""

    def emit(self, record):
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3],
                "level": record.levelname,
                "msg": self.format(record),
                "logger": record.name,
            }
            with log_lock:
                log_entries.append(entry)
                if len(log_entries) > MAX_LOGS:
                    del log_entries[: len(log_entries) - MAX_LOGS]
        except Exception:
            pass


def store_diagnostic(label: str, screenshot_bytes: bytes = None, report: dict = None, html_snippet: str = None):
    """Store a diagnostic snapshot in memory for serving via the dashboard."""
    snap = {
        "label": label,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "screenshot_b64": base64.b64encode(screenshot_bytes).decode() if screenshot_bytes else None,
        "report": report,
        "html_snippet": (html_snippet[:30000] if html_snippet else None),
    }
    with diag_lock:
        diagnostic_snapshots.append(snap)
        # Keep last 20 snapshots
        if len(diagnostic_snapshots) > 20:
            del diagnostic_snapshots[0]


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html><head>
<title>Liquor-Bot Logs</title>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'SF Mono', 'Consolas', monospace; font-size: 13px; }
  #header { background: #161b22; padding: 10px 16px; border-bottom: 1px solid #30363d; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 10; }
  #header h1 { font-size: 15px; color: #58a6ff; font-weight: 600; }
  #header .count { color: #8b949e; font-size: 12px; }
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  #controls input { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 8px; border-radius: 4px; font-size: 12px; width: 200px; }
  #controls button, .tab-btn { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  #controls button:hover, .tab-btn:hover { background: #30363d; }
  .filter-btn { cursor: pointer; padding: 2px 8px; border-radius: 10px; font-size: 11px; border: 1px solid #30363d; }
  .filter-btn.active { background: #1f6feb33; border-color: #1f6feb; }
  .tab-btn.active { background: #1f6feb33; border-color: #1f6feb; color: #58a6ff; }
  #logs { padding: 8px 16px; }
  .log-line { padding: 1px 0; white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
  .log-line:hover { background: #161b22; }
  .ts { color: #8b949e; }
  .level-INFO { color: #58a6ff; }
  .level-WARNING { color: #d29922; }
  .level-ERROR { color: #f85149; font-weight: bold; }
  .level-DEBUG { color: #8b949e; }
  .msg { color: #c9d1d9; }
  .highlight { background: #1f6feb44; }
  #status { position: fixed; bottom: 10px; right: 10px; padding: 4px 10px; border-radius: 10px; font-size: 11px; }
  .connected { background: #238636; color: white; }
  .disconnected { background: #f85149; color: white; }
  #autoscroll-indicator { font-size: 11px; color: #8b949e; }
  /* Diagnostics tab */
  #diag-panel { padding: 16px; display: none; }
  .diag-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
  .diag-card h3 { color: #f0883e; font-size: 14px; margin-bottom: 8px; }
  .diag-card .meta { color: #8b949e; font-size: 11px; margin-bottom: 8px; }
  .diag-card img { max-width: 100%; border: 1px solid #30363d; border-radius: 4px; margin: 8px 0; cursor: pointer; }
  .diag-card img:hover { border-color: #58a6ff; }
  .diag-card pre { background: #0d1117; padding: 8px; border-radius: 4px; overflow-x: auto; font-size: 11px; max-height: 400px; overflow-y: auto; }
  .diag-badge { display: inline-block; background: #f8514933; color: #f85149; padding: 1px 6px; border-radius: 8px; font-size: 11px; margin-left: 6px; }
  .input-row { padding: 2px 0; border-bottom: 1px solid #21262d; }
  .input-row .tag { color: #7ee787; }
  .input-row .id { color: #d2a8ff; }
  .input-row .type { color: #79c0ff; }
  .input-row .vis { color: #8b949e; }
  /* Fullscreen image modal */
  #img-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 100; cursor: pointer; }
  #img-modal img { max-width: 95%; max-height: 95%; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
</style>
</head><body>

<div id="header">
  <h1>Liquor-Bot</h1>
  <button class="tab-btn active" onclick="showTab('logs')">Logs</button>
  <button class="tab-btn" onclick="showTab('diag')" id="diag-tab-btn">Diagnostics <span class="diag-badge" id="diag-count" style="display:none">0</span></button>
  <span class="count" id="count">0 entries</span>
  <div id="controls">
    <input type="text" id="filter" placeholder="Filter logs..." oninput="applyFilter()">
    <span class="filter-btn active" onclick="toggleLevel(this, 'INFO')">INFO</span>
    <span class="filter-btn active" onclick="toggleLevel(this, 'WARNING')">WARN</span>
    <span class="filter-btn active" onclick="toggleLevel(this, 'ERROR')">ERROR</span>
    <button onclick="clearLogs()">Clear</button>
    <button onclick="downloadLogs()">Export</button>
    <span id="autoscroll-indicator">auto-scroll: on</span>
  </div>
</div>
<div id="logs"></div>
<div id="diag-panel"></div>
<div id="status" class="disconnected">disconnected</div>
<div id="img-modal" onclick="this.style.display='none'"><img id="img-modal-img"></div>

<script>
let logs = [];
let autoScroll = true;
let activeLevels = new Set(['INFO', 'WARNING', 'ERROR', 'DEBUG']);
let filterText = '';
let lastCount = 0;
let lastDiagCount = 0;
let currentTab = 'logs';

const logsDiv = document.getElementById('logs');
const diagPanel = document.getElementById('diag-panel');
const countEl = document.getElementById('count');
const statusEl = document.getElementById('status');
const filterEl = document.getElementById('filter');
const scrollIndicator = document.getElementById('autoscroll-indicator');
const diagCountEl = document.getElementById('diag-count');

window.addEventListener('scroll', () => {
  const atBottom = (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 50;
  autoScroll = atBottom;
  scrollIndicator.textContent = 'auto-scroll: ' + (autoScroll ? 'on' : 'off');
});

function showTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  if (tab === 'logs') {
    logsDiv.style.display = '';
    diagPanel.style.display = 'none';
    document.querySelectorAll('.tab-btn')[0].classList.add('active');
  } else {
    logsDiv.style.display = 'none';
    diagPanel.style.display = '';
    document.querySelectorAll('.tab-btn')[1].classList.add('active');
    pollDiag();
  }
}

function toggleLevel(btn, level) {
  if (activeLevels.has(level)) { activeLevels.delete(level); btn.classList.remove('active'); }
  else { activeLevels.add(level); btn.classList.add('active'); }
  renderLogs();
}

function applyFilter() { filterText = filterEl.value.toLowerCase(); renderLogs(); }

function renderLogs() {
  const filtered = logs.filter(l => activeLevels.has(l.level) && (!filterText || l.msg.toLowerCase().includes(filterText)));
  let html = '';
  for (const l of filtered) {
    const cls = filterText && l.msg.toLowerCase().includes(filterText) ? ' highlight' : '';
    html += '<div class="log-line' + cls + '"><span class="ts">' + esc(l.ts) + '</span> <span class="level-' + l.level + '">' + pad(l.level) + '</span> <span class="msg">' + esc(l.msg) + '</span></div>';
  }
  logsDiv.innerHTML = html;
  countEl.textContent = filtered.length + '/' + logs.length + ' entries';
  if (autoScroll) window.scrollTo(0, document.body.scrollHeight);
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function pad(s) { return (s + '       ').substring(0, 7); }

function clearLogs() { fetch('/api/clear', { method: 'POST' }); logs = []; lastCount = 0; renderLogs(); }

function downloadLogs() {
  const text = logs.map(l => l.ts + ' ' + l.level + ' ' + l.msg).join('\n');
  const blob = new Blob([text], { type: 'text/plain' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'liquor-bot-logs-' + new Date().toISOString().replace(/[:.]/g, '-') + '.txt'; a.click();
}

function showFullImg(src) {
  document.getElementById('img-modal-img').src = src;
  document.getElementById('img-modal').style.display = 'block';
}

function renderInputs(inputs) {
  if (!inputs || !Array.isArray(inputs)) return '<span class="vis">' + esc(String(inputs)) + '</span>';
  let html = '';
  for (const inp of inputs) {
    html += '<div class="input-row">';
    html += '<span class="tag">' + esc(inp.tag || 'INPUT') + '</span> ';
    if (inp.id) html += '<span class="id">id="' + esc(inp.id) + '"</span> ';
    if (inp.name) html += 'name="' + esc(inp.name) + '" ';
    html += '<span class="type">type=' + esc(inp.type || '?') + '</span> ';
    if (inp.cls) html += 'class="' + esc(inp.cls) + '" ';
    if (inp.text) html += 'text="' + esc(inp.text) + '" ';
    html += '<span class="vis">' + (inp.vis ? 'VISIBLE' : 'hidden') + ' ' + (inp.w||0) + 'x' + (inp.h||0) + '</span>';
    html += '</div>';
  }
  return html || '<span class="vis">no inputs found</span>';
}

function renderDiagnostics(snapshots) {
  if (!snapshots.length) { diagPanel.innerHTML = '<p style="color:#8b949e;padding:20px">No diagnostic snapshots yet. They appear automatically when the bot encounters issues.</p>'; return; }
  let html = '';
  // Show newest first
  for (let i = snapshots.length - 1; i >= 0; i--) {
    const s = snapshots[i];
    html += '<div class="diag-card">';
    html += '<h3>' + esc(s.label) + '</h3>';
    html += '<div class="meta">' + esc(s.timestamp) + (s.report && s.report.url ? ' | ' + esc(s.report.url) : '') + '</div>';
    if (s.screenshot_b64) {
      html += '<img src="data:image/png;base64,' + s.screenshot_b64 + '" onclick="showFullImg(this.src)" title="Click to enlarge">';
    }
    if (s.report && s.report.contexts) {
      for (const [ctx, inputs] of Object.entries(s.report.contexts)) {
        html += '<details><summary style="color:#58a6ff;cursor:pointer;margin:4px 0">' + esc(ctx) + ' (' + (Array.isArray(inputs) ? inputs.length + ' elements' : 'error') + ')</summary>';
        html += renderInputs(inputs);
        html += '</details>';
      }
    }
    html += '</div>';
  }
  diagPanel.innerHTML = html;
}

async function poll() {
  try {
    const resp = await fetch('/api/logs?after=' + lastCount);
    if (resp.ok) {
      const data = await resp.json();
      if (data.logs && data.logs.length > 0) { logs.push(...data.logs); lastCount = data.total; renderLogs(); }
      statusEl.className = 'connected'; statusEl.textContent = 'connected';
    }
    // Check for new diagnostics
    const dresp = await fetch('/api/diagnostics/count');
    if (dresp.ok) {
      const d = await dresp.json();
      if (d.count > 0) { diagCountEl.style.display = ''; diagCountEl.textContent = d.count; }
      if (d.count > lastDiagCount && currentTab === 'diag') { lastDiagCount = d.count; pollDiag(); }
      else { lastDiagCount = d.count; }
    }
  } catch (e) { statusEl.className = 'disconnected'; statusEl.textContent = 'disconnected'; }
}

async function pollDiag() {
  try {
    const resp = await fetch('/api/diagnostics');
    if (resp.ok) { const data = await resp.json(); renderDiagnostics(data.snapshots || []); }
  } catch (e) {}
}

async function init() {
  try {
    const resp = await fetch('/api/logs');
    if (resp.ok) { const data = await resp.json(); logs = data.logs || []; lastCount = data.total; renderLogs(); statusEl.className = 'connected'; statusEl.textContent = 'connected'; }
  } catch (e) {}
  setInterval(poll, 500);
}
init();
</script>
</body></html>"""


class LogRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._respond(200, "text/html", DASHBOARD_HTML.encode())
        elif self.path.startswith("/api/logs"):
            after = 0
            if "after=" in self.path:
                try: after = int(self.path.split("after=")[1].split("&")[0])
                except ValueError: pass
            with log_lock:
                new_logs = log_entries[after:] if (after > 0 and after <= len(log_entries)) else list(log_entries)
                total = len(log_entries)
            self._respond(200, "application/json", json.dumps({"logs": new_logs, "total": total}).encode())
        elif self.path == "/api/diagnostics":
            with diag_lock:
                snaps = list(diagnostic_snapshots)
            self._respond(200, "application/json", json.dumps({"snapshots": snaps}).encode())
        elif self.path == "/api/diagnostics/count":
            with diag_lock:
                count = len(diagnostic_snapshots)
            self._respond(200, "application/json", json.dumps({"count": count}).encode())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/clear":
            with log_lock: log_entries.clear()
            self._respond(200, "application/json", b'{"ok":true}')
        elif self.path == "/api/diagnostics/clear":
            with diag_lock: diagnostic_snapshots.clear()
            self._respond(200, "application/json", b'{"ok":true}')
        else:
            self.send_response(404); self.end_headers()

    def _respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def install_log_collector():
    """Install the log collector on the root logger."""
    collector = LogCollector()
    collector.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(collector)
    return collector


def start_server(port=PORT):
    """Start the log server in a background thread."""
    server = HTTPServer(("0.0.0.0", port), LogRequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"Log viewer running at http://127.0.0.1:{port}")
    return server


if __name__ == "__main__":
    install_log_collector()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger("log_server")
    logger.info("Log server started. Open http://127.0.0.1:5051")
    server = HTTPServer(("0.0.0.0", PORT), LogRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
