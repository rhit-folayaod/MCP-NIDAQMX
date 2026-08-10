"""Live browser dashboard, served from inside the MCP server process.

It has to live in this process. NI-DAQmx reserves a channel for the lifetime
of a task, so a separate dashboard process could not read `ai0` while the MCP
server held it (or vice versa). Sharing a process means the agent and the human
are looking at, and writing to, exactly the same hardware state.

The safety layer is not reimplemented here. `create_app` takes callables that
the server supplies, so a write from the browser passes through the same
allowlist and write-enable checks as a write from the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("daq-mcp")

# How often the browser gets a frame. 20 Hz looks smooth and costs little.
_FRAME_INTERVAL_S = 0.05


def create_app(
    *,
    snapshot: Callable[[], dict[str, Any]],
    set_digital: Callable[[str, bool], dict[str, Any]],
    config: Callable[[], dict[str, Any]],
) -> Starlette:
    """Build the dashboard app around server-supplied, safety-checked hooks."""

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(_PAGE)

    async def api_config(request: Request) -> JSONResponse:
        return JSONResponse(config())

    async def api_state(request: Request) -> JSONResponse:
        return JSONResponse(snapshot())

    async def api_stream(request: Request) -> StreamingResponse:
        async def frames():
            while True:
                if await request.is_disconnected():
                    break
                payload = json.dumps(snapshot())
                yield f"data: {payload}\n\n"
                await asyncio.sleep(_FRAME_INTERVAL_S)

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def api_write_digital(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Body must be JSON"}, status_code=400)

        channel = body.get("channel")
        value = body.get("value")
        if not isinstance(channel, str) or not isinstance(value, bool):
            return JSONResponse(
                {"error": "Expected {channel: str, value: bool}"}, status_code=400
            )

        result = set_digital(channel, value)
        status = 403 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/config", api_config),
            Route("/api/state", api_state),
            Route("/api/stream", api_stream),
            Route("/api/digital", api_write_digital, methods=["POST"]),
        ]
    )


def port_is_free(host: str, port: int) -> bool:
    """Check before starting, so a taken port is a clear message not a stray
    uvicorn traceback after we have already logged a URL that will not work."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def serve_in_thread(app: Starlette, host: str, port: int) -> threading.Thread:
    """Run uvicorn on its own event loop in a daemon thread.

    Daemon so it never keeps the MCP server alive after the client disconnects,
    and on its own loop so dashboard traffic cannot stall the stdio transport.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def run() -> None:
        try:
            asyncio.run(server.serve())
        except Exception as exc:
            logger.warning("dashboard server stopped: %s", exc)

    thread = threading.Thread(target=run, name="daq-dashboard", daemon=True)
    thread.start()
    return thread


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>daq-mcp live</title>
<style>
  :root {
    --bg: #0b0f14;
    --panel: #141b24;
    --border: #223041;
    --text: #e6edf3;
    --muted: #8b9bb0;
    --accent: #4cc2ff;
    --on: #3fb950;
    --off: #3d4754;
    --danger: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    background: var(--bg); color: var(--text);
    font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  h1 { font-size: 18px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  .pill {
    font-size: 12px; padding: 3px 10px; border-radius: 999px;
    border: 1px solid var(--border); color: var(--muted);
  }
  .pill.live { color: var(--on); border-color: #1f4a2c; }
  .pill.warn { color: var(--danger); border-color: #5c2426; }
  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }
  .panel h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin: 0 0 12px; font-weight: 600;
  }
  canvas { width: 100%; height: 260px; display: block; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(88px, 1fr)); gap: 12px; margin-top: 14px; }
  .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .stat .v { font-size: 17px; font-variant-numeric: tabular-nums; margin-top: 2px; }
  .row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 9px 0; border-bottom: 1px solid var(--border);
  }
  .row:last-child { border-bottom: none; }
  .chan { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
  .lamp { width: 12px; height: 12px; border-radius: 50%; background: var(--off); transition: background .12s; }
  .lamp.on { background: var(--on); box-shadow: 0 0 10px rgba(63,185,80,.65); }
  button.toggle {
    border: 1px solid var(--border); background: #1b2431; color: var(--text);
    border-radius: 6px; padding: 5px 14px; cursor: pointer; font-size: 12px; min-width: 58px;
  }
  button.toggle:hover:not(:disabled) { border-color: var(--accent); }
  button.toggle.on { background: #17492a; border-color: #2ea043; }
  button.toggle:disabled { opacity: .45; cursor: not-allowed; }
  .note { color: var(--muted); font-size: 12px; margin-top: 10px; }
  .err { color: var(--danger); font-size: 12px; margin-top: 10px; min-height: 16px; }
</style>
</head>
<body>
<header>
  <h1>daq-mcp live</h1>
  <span class="pill" id="p-backend">backend</span>
  <span class="pill" id="p-channel">channel</span>
  <span class="pill" id="p-writes">writes</span>
  <span class="pill" id="p-conn">connecting</span>
</header>
<div class="err" id="acq-err" style="margin:-8px 0 14px"></div>

<div class="grid">
  <div class="panel">
    <h2>Analog input</h2>
    <canvas id="chart"></canvas>
    <div class="stats">
      <div class="stat"><div class="k">Latest</div><div class="v" id="s-latest">--</div></div>
      <div class="stat"><div class="k">Mean</div><div class="v" id="s-mean">--</div></div>
      <div class="stat"><div class="k">RMS</div><div class="v" id="s-rms">--</div></div>
      <div class="stat"><div class="k">Pk-Pk</div><div class="v" id="s-p2p">--</div></div>
      <div class="stat"><div class="k">Std dev</div><div class="v" id="s-std">--</div></div>
      <div class="stat"><div class="k">Samples</div><div class="v" id="s-total">--</div></div>
    </div>
  </div>

  <div>
    <div class="panel" style="margin-bottom:16px">
      <h2>Digital outputs</h2>
      <div id="outputs"></div>
      <div class="note" id="out-note"></div>
      <div class="err" id="out-err"></div>
    </div>
    <div class="panel">
      <h2>Digital inputs</h2>
      <div id="inputs"></div>
      <div class="note">Polled live. Read-only by design.</div>
    </div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 3) => (v === null || v === undefined) ? "--" : Number(v).toFixed(d);

let cfg = { digital_outputs: [], digital_inputs: [], writes_enabled: false, backend: "?" };
let outState = {};

async function loadConfig() {
  cfg = await (await fetch("/api/config")).json();
  $("p-backend").textContent = cfg.backend;
  const w = $("p-writes");
  w.textContent = cfg.writes_enabled ? "writes enabled" : "writes disabled";
  w.className = "pill" + (cfg.writes_enabled ? "" : " warn");
  buildRows();
}

function buildRows() {
  const out = $("outputs");
  out.innerHTML = "";
  if (!cfg.digital_outputs.length) {
    out.innerHTML = '<div class="note">No writable digital channels configured.</div>';
  }
  for (const ch of cfg.digital_outputs) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      '<span class="chan">' + ch + '</span>' +
      '<span style="display:flex;align-items:center;gap:10px">' +
        '<span class="lamp" data-lamp="' + ch + '"></span>' +
        '<button class="toggle" data-ch="' + ch + '">OFF</button>' +
      '</span>';
    out.appendChild(row);
  }
  out.querySelectorAll("button.toggle").forEach((b) => {
    b.disabled = !cfg.writes_enabled;
    b.onclick = () => toggle(b.dataset.ch);
  });
  $("out-note").textContent = cfg.writes_enabled
    ? "The agent can drive these too. Last write wins."
    : "Set DAQ_MCP_ALLOW_WRITE=1 to enable output control.";

  const inp = $("inputs");
  inp.innerHTML = "";
  if (!cfg.digital_inputs.length) {
    inp.innerHTML = '<div class="note">No digital inputs configured.</div>';
  }
  for (const ch of cfg.digital_inputs) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      '<span class="chan">' + ch + '</span>' +
      '<span class="lamp" data-lamp="' + ch + '"></span>';
    inp.appendChild(row);
  }
}

async function toggle(ch) {
  const next = !outState[ch];
  $("out-err").textContent = "";
  try {
    const r = await fetch("/api/digital", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: ch, value: next }),
    });
    const body = await r.json();
    if (!r.ok) $("out-err").textContent = body.error || "Write refused";
  } catch (e) {
    $("out-err").textContent = String(e);
  }
}

// Chart: plain canvas, no dependencies, redrawn each frame.
const cv = $("chart");
const ctx = cv.getContext("2d");
function draw(trace) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== w * dpr || cv.height !== h * dpr) {
    cv.width = w * dpr; cv.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!trace || trace.length < 2) {
    ctx.fillStyle = "#8b9bb0"; ctx.font = "13px system-ui";
    ctx.fillText("waiting for samples...", 12, 22);
    return;
  }

  let lo = Math.min(...trace), hi = Math.max(...trace);
  if (hi - lo < 1e-6) { const m = (hi + lo) / 2; lo = m - 0.5; hi = m + 0.5; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const x = (i) => (i / (trace.length - 1)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * h;

  ctx.strokeStyle = "#223041"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const gy = (g / 4) * h;
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke();
  }
  ctx.fillStyle = "#8b9bb0"; ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(hi.toFixed(2) + " V", 4, 11);
  ctx.fillText(lo.toFixed(2) + " V", 4, h - 3);

  ctx.strokeStyle = "#4cc2ff"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x(0), y(trace[0]));
  for (let i = 1; i < trace.length; i++) ctx.lineTo(x(i), y(trace[i]));
  ctx.stroke();
}

function render(s) {
  const ch = $("p-channel");
  ch.textContent = s.streaming ? s.channel + " @ " + s.rate_hz + " Hz" : "not streaming";
  ch.className = "pill" + (s.streaming ? " live" : " warn");
  $("acq-err").textContent = s.error ? "Acquisition stopped: " + s.error : "";

  $("s-latest").textContent = fmt(s.latest) + " V";
  $("s-mean").textContent = fmt(s.mean);
  $("s-rms").textContent = fmt(s.rms);
  $("s-p2p").textContent = fmt(s.peak_to_peak);
  $("s-std").textContent = fmt(s.std_dev, 4);
  $("s-total").textContent = s.total_samples.toLocaleString();

  outState = s.digital_outputs || {};
  for (const [ch2, v] of Object.entries(s.digital_outputs || {})) {
    const lamp = document.querySelector('[data-lamp="' + ch2 + '"]');
    if (lamp) lamp.className = "lamp" + (v ? " on" : "");
    const btn = document.querySelector('button[data-ch="' + ch2 + '"]');
    if (btn) { btn.textContent = v ? "ON" : "OFF"; btn.className = "toggle" + (v ? " on" : ""); }
  }
  for (const [ch2, v] of Object.entries(s.digital_inputs || {})) {
    const lamp = document.querySelector('[data-lamp="' + ch2 + '"]');
    if (lamp) lamp.className = "lamp" + (v ? " on" : "");
  }
  draw(s.trace);
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => { $("p-conn").textContent = "connected"; $("p-conn").className = "pill live"; };
  es.onmessage = (e) => render(JSON.parse(e.data));
  es.onerror = () => {
    $("p-conn").textContent = "reconnecting"; $("p-conn").className = "pill warn";
    es.close(); setTimeout(connect, 1500);
  };
}

loadConfig().then(connect);
</script>
</body>
</html>
"""
