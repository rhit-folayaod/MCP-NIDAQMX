# Copyright 2026 Timi Folayan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route

logger = logging.getLogger("daq-mcp")

# How often the browser gets a frame. 10 Hz is smooth and leaves the HTTP
# event loop free for MCP tool calls on the same port.
_FRAME_INTERVAL_S = 0.1


def create_app(
    *,
    snapshot: Callable[[], dict[str, Any]],
    set_digital: Callable[[str, bool], dict[str, Any]],
    config: Callable[[], dict[str, Any]],
    inventory: Callable[[str | None], dict[str, Any]] | None = None,
    set_wiring: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    list_profiles: Callable[[], dict[str, Any]] | None = None,
    load_profile: Callable[[str], dict[str, Any]] | None = None,
    delete_profile: Callable[[str], dict[str, Any]] | None = None,
    save_capture: Callable[[dict[str, Any] | None], dict[str, Any]] | None = None,
    list_captures: Callable[[], dict[str, Any]] | None = None,
    get_capture: Callable[[str], dict[str, Any]] | None = None,
    mcp_app: Any = None,
    auth_token: str | None = None,
) -> Starlette:
    """Build the dashboard app around server-supplied, safety-checked hooks.

    Passing `mcp_app` mounts the MCP endpoint at /mcp on the same port, so a
    client like MCP Inspector attaches to this running process instead of
    spawning its own — which would fight this one for the device.

    `auth_token`, when set, gates every route. Required for non-loopback binds.
    """

    async def index(request: Request) -> HTMLResponse:
        # Embed a token from ?token= so the page can authorize EventSource
        # (which cannot set Authorization headers) without a second login UI.
        page = _PAGE
        q = request.query_params.get("token")
        if q:
            safe = json.dumps(q)
            page = page.replace(
                "let AUTH_TOKEN = sessionStorage.getItem('daq_token') || '';",
                f"let AUTH_TOKEN = {safe}; sessionStorage.setItem('daq_token', AUTH_TOKEN);",
                1,
            )
        return HTMLResponse(page)

    async def api_config(request: Request) -> JSONResponse:
        return JSONResponse(config())

    async def api_state(request: Request) -> JSONResponse:
        return JSONResponse(snapshot())

    async def api_stream(request: Request) -> StreamingResponse:
        async def frames():
            while True:
                if await request.is_disconnected():
                    break
                payload = await asyncio.to_thread(lambda: json.dumps(snapshot()))
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

    async def api_inventory(request: Request) -> JSONResponse:
        if inventory is None:
            return JSONResponse({"error": "Inventory not available"}, status_code=501)
        device = request.query_params.get("device")
        return JSONResponse(inventory(device))

    async def api_wiring(request: Request) -> JSONResponse:
        if set_wiring is None:
            return JSONResponse({"error": "Wiring not available"}, status_code=501)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Body must be a JSON object"}, status_code=400)
        result = set_wiring(body)
        status = 400 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def api_profiles(request: Request) -> JSONResponse:
        if list_profiles is None:
            return JSONResponse({"error": "Profiles not available"}, status_code=501)
        return JSONResponse(list_profiles())

    async def api_load_profile(request: Request) -> JSONResponse:
        if load_profile is None:
            return JSONResponse({"error": "Profiles not available"}, status_code=501)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Body must be JSON"}, status_code=400)
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name.strip():
            return JSONResponse({"error": "Expected {name: str}"}, status_code=400)
        result = load_profile(name.strip())
        status = 400 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def api_delete_profile(request: Request) -> JSONResponse:
        if delete_profile is None:
            return JSONResponse({"error": "Profiles not available"}, status_code=501)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Body must be JSON"}, status_code=400)
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name.strip():
            return JSONResponse({"error": "Expected {name: str}"}, status_code=400)
        result = delete_profile(name.strip())
        status = 400 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def api_capture(request: Request) -> JSONResponse:
        if save_capture is None:
            return JSONResponse({"error": "Captures not available"}, status_code=501)
        try:
            body = await request.json() if request.method == "POST" else {}
        except Exception:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "Body must be a JSON object"}, status_code=400)
        result = save_capture(body)
        status = 400 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def api_captures(request: Request) -> JSONResponse:
        if list_captures is None:
            return JSONResponse({"error": "Captures not available"}, status_code=501)
        return JSONResponse(list_captures())

    async def api_get_capture(request: Request) -> JSONResponse:
        if get_capture is None:
            return JSONResponse({"error": "Captures not available"}, status_code=501)
        name = request.path_params.get("name") or ""
        result = get_capture(name)
        status = 404 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def api_download_capture(request: Request) -> Response:
        from daq_mcp.captures import captures_dir

        name = request.path_params.get("name") or ""
        stem = name[:-5] if name.endswith(".json") else name
        path = captures_dir() / f"{stem}.json"
        if not path.is_file():
            return JSONResponse({"error": f"No capture named {name!r}"}, status_code=404)
        return FileResponse(
            path,
            media_type="application/json",
            filename=path.name,
        )

    routes: list[Any] = [
        Route("/", index),
        Route("/api/config", api_config),
        Route("/api/state", api_state),
        Route("/api/stream", api_stream),
        Route("/api/digital", api_write_digital, methods=["POST"]),
        Route("/api/inventory", api_inventory),
        Route("/api/wiring", api_wiring, methods=["PUT"]),
        Route("/api/profiles", api_profiles),
        Route("/api/profiles/load", api_load_profile, methods=["POST"]),
        Route("/api/profiles/delete", api_delete_profile, methods=["POST"]),
        Route("/api/capture", api_capture, methods=["POST"]),
        Route("/api/captures", api_captures),
        Route("/api/captures/{name}", api_get_capture),
        Route("/api/captures/{name}/download", api_download_capture),
    ]

    if mcp_app is None:
        app = Starlette(routes=routes)
    else:
        # The MCP app carries its own lifespan (session manager startup). Mounting
        # without handing that to the parent leaves the endpoint dead on arrival.
        routes.append(Mount("/mcp", app=mcp_app))
        app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

    if auth_token:
        from daq_mcp.auth import TokenAuthMiddleware

        app.add_middleware(TokenAuthMiddleware, token=auth_token)
    return app


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
  .ok { color: var(--on); font-size: 12px; margin-top: 10px; min-height: 16px; }
  .picker { margin-top: 16px; }
  .picker-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
  }
  .picker-col h3 {
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); margin: 0 0 8px; font-weight: 600;
  }
  .pick-list {
    max-height: 180px; overflow: auto; border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 8px; background: #0f141c;
  }
  .pick-list label {
    display: flex; gap: 8px; align-items: center; padding: 4px 0;
    font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px;
  }
  .picker-actions {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 12px;
  }
  select, input[type="number"] {
    background: #1b2431; color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 8px; font-size: 12px;
  }
  button.primary {
    border: 1px solid #2ea043; background: #17492a; color: var(--text);
    border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 12px;
  }
  button.primary:hover { border-color: var(--accent); }
  details.capture-box {
    margin-top: 12px; border: 1px solid var(--border); border-radius: 8px;
    background: #0f141c; padding: 8px 12px;
  }
  details.capture-box summary {
    cursor: pointer; color: var(--muted); font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  #capture-text {
    width: 100%; min-height: 160px; margin-top: 8px; resize: vertical;
    background: #0b0f14; color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px; font: 12px/1.45 ui-monospace, Menlo, Consolas, monospace;
  }
  #capture-list { margin-top: 10px; }
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

<div class="panel" style="margin-top:16px">
  <h2>Save test data</h2>
  <div class="note">
    Snapshot the current live window (metrics + samples) to this PC, then
    expand the summary or download JSON / CSV. The agent can also call
    <code>save_capture</code>.
  </div>
  <div class="picker-actions" style="margin-top:12px">
    <label>Label
      <input id="c-label" type="text" placeholder="e.g. blink-test" style="width:160px" maxlength="64">
    </label>
    <label>Note
      <input id="c-note" type="text" placeholder="optional" style="width:220px" maxlength="200">
    </label>
    <button class="primary" id="c-save" type="button">Capture now</button>
    <button class="toggle" id="c-dl-json" type="button" disabled>Download JSON</button>
    <button class="toggle" id="c-dl-csv" type="button" disabled>Download CSV</button>
  </div>
  <details class="capture-box" id="c-details">
    <summary>Metrics summary (expand)</summary>
    <textarea id="capture-text" readonly placeholder="Capture a run to see metrics here."></textarea>
  </details>
  <div class="note" id="c-path"></div>
  <div class="err" id="c-err"></div>
  <div class="ok" id="c-ok"></div>
  <div id="capture-list"></div>
</div>

<div class="panel picker">
  <h2>Channel wiring</h2>
  <div class="note">
    The driver lists every pin the device supports. You choose which ones this
    server may touch — and which digital lines are inputs vs outputs. Named
    profiles are saved on this PC (not committed). The agent uses the active
    profile's allowlist.
  </div>
  <div class="picker-actions" style="margin-top:12px">
    <label>Saved profiles
      <select id="w-profiles"></select>
    </label>
    <button class="toggle" id="w-load" type="button">Load</button>
    <button class="toggle" id="w-delete" type="button">Delete</button>
  </div>
  <div class="picker-actions">
    <label>Device
      <select id="w-device"></select>
    </label>
    <label>Stream
      <select id="w-live"></select>
    </label>
    <label>Rate (Hz)
      <input id="w-rate" type="number" min="1" step="1" value="1000" style="width:88px">
    </label>
  </div>
  <div class="picker-actions">
    <label>Save as
      <input id="w-name" type="text" placeholder="e.g. lab-bench" style="width:160px"
        maxlength="64">
    </label>
    <button class="primary" id="w-save" type="button">Save profile</button>
  </div>
  <div class="picker-grid" style="margin-top:14px">
    <div class="picker-col">
      <h3>Analog inputs</h3>
      <div class="pick-list" id="pick-ai"></div>
    </div>
    <div class="picker-col">
      <h3>Analog outputs</h3>
      <div class="pick-list" id="pick-ao"></div>
    </div>
    <div class="picker-col">
      <h3>Digital inputs</h3>
      <div class="pick-list" id="pick-di"></div>
    </div>
    <div class="picker-col">
      <h3>Digital outputs</h3>
      <div class="pick-list" id="pick-do"></div>
    </div>
  </div>
  <div class="err" id="w-err"></div>
  <div class="ok" id="w-ok"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 3) => (v === null || v === undefined) ? "--" : Number(v).toFixed(d);

let AUTH_TOKEN = sessionStorage.getItem('daq_token') || '';
function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (AUTH_TOKEN) h["Authorization"] = "Bearer " + AUTH_TOKEN;
  return h;
}
function withToken(url) {
  if (!AUTH_TOKEN) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(AUTH_TOKEN);
}

let cfg = { digital_outputs: [], digital_inputs: [], writes_enabled: false, backend: "?" };
let outState = {};
let inventoryPack = null;
let draft = null;

async function loadConfig() {
  cfg = await (await fetch(withToken("/api/config"), { headers: authHeaders() })).json();
  $("p-backend").textContent = cfg.backend;
  const w = $("p-writes");
  w.textContent = cfg.writes_enabled ? "writes enabled" : "writes disabled";
  w.className = "pill" + (cfg.writes_enabled ? "" : " warn");
  draft = Object.assign({}, cfg.wiring || {});
  $("w-rate").value = draft.live_rate_hz || cfg.live_rate_hz || 1000;
  $("w-name").value = cfg.active_profile || "";
  fillProfileSelect(cfg.profiles || [], cfg.active_profile);
  buildRows();
  await loadInventory(draft.device);
}

function fillProfileSelect(profiles, active) {
  const sel = $("w-profiles");
  sel.innerHTML = "";
  if (!profiles.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(no saved profiles yet)";
    sel.appendChild(opt);
    return;
  }
  for (const p of profiles) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name + (p.active || p.name === active ? " (active)" : "");
    sel.appendChild(opt);
  }
  if (active) sel.value = active;
}

async function loadInventory(device) {
  const q = device ? ("?device=" + encodeURIComponent(device)) : "";
  inventoryPack = await (await fetch(withToken("/api/inventory" + q), { headers: authHeaders() })).json();
  if (inventoryPack.error && !inventoryPack.inventory) {
    $("w-err").textContent = inventoryPack.error;
    return;
  }
  $("w-err").textContent = "";
  fillDeviceSelect();
  fillChannelLists();
  fillLiveSelect();
}

function fillDeviceSelect() {
  const sel = $("w-device");
  const devices = inventoryPack.devices || [];
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = "All devices (cDAQ modules, etc.)";
  sel.appendChild(all);
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.name;
    opt.textContent = d.name + " (" + (d.product_type || "?") + ")";
    sel.appendChild(opt);
  }
  const want = (draft && draft.device) || inventoryPack.selected || "all";
  sel.value = want;
  if (sel.value !== want) sel.value = "all";
  sel.onchange = () => {
    draft.device = sel.value;
    loadInventory(sel.value);
  };
}

function checkedSet(id) {
  return new Set(
    Array.from(document.querySelectorAll("#" + id + " input:checked")).map((el) => el.value)
  );
}

function fillChannelLists() {
  const inv = inventoryPack.inventory || {};
  const ai = inv.analog_input_channels || [];
  const ao = inv.analog_output_channels || [];
  const di = inv.digital_input_channels || [];
  const dout = inv.digital_output_channels || [];
  // Boards often list the same DIO under both DI and DO.
  const dio = Array.from(new Set([...di, ...dout])).sort();

  renderChecks("pick-ai", ai, new Set(draft.analog_inputs || []), () => fillLiveSelect());
  renderChecks("pick-ao", ao, new Set(draft.analog_outputs || []));
  renderChecks("pick-di", dio, new Set(draft.digital_inputs || []), syncDigitalExclusive);
  renderChecks("pick-do", dio, new Set(draft.digital_outputs || []), syncDigitalExclusive);
}

function renderChecks(containerId, channels, selected, onChange) {
  const box = $(containerId);
  box.innerHTML = "";
  if (!channels.length) {
    box.innerHTML = '<div class="note">None on this device.</div>';
    return;
  }
  for (const ch of channels) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = ch;
    cb.checked = selected.has(ch);
    if (onChange) cb.onchange = onChange;
    label.appendChild(cb);
    label.appendChild(document.createTextNode(ch));
    box.appendChild(label);
  }
}

function syncDigitalExclusive(ev) {
  // A line cannot be both input and output in our safety model.
  const ch = ev.target.value;
  const asInput = ev.target.closest("#pick-di") !== null;
  const other = document.querySelector(
    (asInput ? "#pick-do" : "#pick-di") + ' input[value="' + CSS.escape(ch) + '"]'
  );
  if (ev.target.checked && other) other.checked = false;
}

function fillLiveSelect() {
  const sel = $("w-live");
  const selectedAi = Array.from(checkedSet("pick-ai"));
  const fallback = draft.analog_inputs || [];
  const options = selectedAi.length ? selectedAi : fallback;
  sel.innerHTML = "";
  for (const ch of options) {
    const opt = document.createElement("option");
    opt.value = ch;
    opt.textContent = ch;
    sel.appendChild(opt);
  }
  const want = draft.live_channel || cfg.live_channel;
  if (want && options.includes(want)) sel.value = want;
  else if (options.length) sel.value = options[0];
}

function collectDraft() {
  return {
    profile_name: ($("w-name").value || "").trim(),
    device: $("w-device").value,
    live_channel: $("w-live").value,
    live_rate_hz: Number($("w-rate").value),
    analog_inputs: Array.from(checkedSet("pick-ai")),
    analog_outputs: Array.from(checkedSet("pick-ao")),
    digital_inputs: Array.from(checkedSet("pick-di")),
    digital_outputs: Array.from(checkedSet("pick-do")),
  };
}

$("w-save").onclick = async () => {
  $("w-err").textContent = "";
  $("w-ok").textContent = "";
  const body = collectDraft();
  if (!body.profile_name) {
    $("w-err").textContent = "Give the profile a name before saving.";
    return;
  }
  try {
    const r = await fetch(withToken("/api/wiring"), {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const res = await r.json();
    if (!r.ok) {
      $("w-err").textContent = res.error || "Save refused";
      return;
    }
    draft = res.wiring;
    $("w-ok").textContent = "Saved profile '" + (res.active_profile || body.profile_name) + "'." +
      (res.live_error ? " Live restart failed: " + res.live_error : "");
    await loadConfig();
  } catch (e) {
    $("w-err").textContent = String(e);
  }
};

$("w-load").onclick = async () => {
  $("w-err").textContent = "";
  $("w-ok").textContent = "";
  const name = $("w-profiles").value;
  if (!name) { $("w-err").textContent = "No profile selected."; return; }
  try {
    const r = await fetch(withToken("/api/profiles/load"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    });
    const res = await r.json();
    if (!r.ok) { $("w-err").textContent = res.error || "Load refused"; return; }
    $("w-ok").textContent = "Loaded profile '" + name + "'.";
    await loadConfig();
  } catch (e) {
    $("w-err").textContent = String(e);
  }
};

$("w-delete").onclick = async () => {
  $("w-err").textContent = "";
  $("w-ok").textContent = "";
  const name = $("w-profiles").value;
  if (!name) { $("w-err").textContent = "No profile selected."; return; }
  if (!confirm("Delete profile '" + name + "' from this PC?")) return;
  try {
    const r = await fetch(withToken("/api/profiles/delete"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    });
    const res = await r.json();
    if (!r.ok) { $("w-err").textContent = res.error || "Delete refused"; return; }
    $("w-ok").textContent = "Deleted '" + name + "'.";
    await loadConfig();
  } catch (e) {
    $("w-err").textContent = String(e);
  }
};

let lastCapture = null;

function downloadBlob(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function showCapture(res) {
  lastCapture = res;
  $("capture-text").value = res.summary || "";
  $("c-details").open = true;
  $("c-path").textContent = res.path ? ("Saved on this PC: " + res.path) : "";
  $("c-dl-json").disabled = false;
  $("c-dl-csv").disabled = !res.csv;
  refreshCaptureList();
}

async function refreshCaptureList() {
  try {
    const r = await fetch(withToken("/api/captures"), { headers: authHeaders() });
    const res = await r.json();
    const box = $("capture-list");
    box.innerHTML = "";
    const items = res.captures || [];
    if (!items.length) {
      box.innerHTML = '<div class="note">No saved captures yet.</div>';
      return;
    }
    for (const c of items.slice(0, 8)) {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML =
        '<span class="chan">' + c.name + '</span>' +
        '<button class="toggle" type="button" data-cap="' + c.name + '">Open</button>';
      box.appendChild(row);
    }
    box.querySelectorAll("button[data-cap]").forEach((b) => {
      b.onclick = () => openCapture(b.dataset.cap);
    });
  } catch (e) {
    /* ignore list refresh errors */
  }
}

async function openCapture(name) {
  $("c-err").textContent = "";
  try {
    const r = await fetch(withToken("/api/captures/" + encodeURIComponent(name)), {
      headers: authHeaders(),
    });
    const res = await r.json();
    if (!r.ok) { $("c-err").textContent = res.error || "Could not open"; return; }
    showCapture(res);
    $("c-ok").textContent = "Loaded " + name;
  } catch (e) {
    $("c-err").textContent = String(e);
  }
}

$("c-save").onclick = async () => {
  $("c-err").textContent = "";
  $("c-ok").textContent = "";
  try {
    const r = await fetch(withToken("/api/capture"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        label: ($("c-label").value || "").trim() || null,
        note: ($("c-note").value || "").trim() || null,
      }),
    });
    const res = await r.json();
    if (!r.ok) { $("c-err").textContent = res.error || "Capture failed"; return; }
    showCapture(res);
    $("c-ok").textContent = "Captured " + res.name;
  } catch (e) {
    $("c-err").textContent = String(e);
  }
};

$("c-dl-json").onclick = () => {
  if (!lastCapture || !lastCapture.name) return;
  window.location.href = withToken(
    "/api/captures/" + encodeURIComponent(lastCapture.name) + "/download"
  );
};

$("c-dl-csv").onclick = () => {
  if (!lastCapture || !lastCapture.csv) return;
  downloadBlob((lastCapture.name || "capture") + ".csv", lastCapture.csv, "text/csv");
};

loadConfig().then(() => { connect(); refreshCaptureList(); });

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
    const r = await fetch(withToken("/api/digital"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
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
function draw(trace, units) {
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

  let lo = trace[0], hi = trace[0];
  for (let i = 1; i < trace.length; i++) {
    const v = trace[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (hi - lo < 1e-6) { const m = (hi + lo) / 2; lo = m - 0.5; hi = m + 0.5; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const x = (i) => (i / (trace.length - 1)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * h;

  ctx.strokeStyle = "#223041"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const gy = (g / 4) * h;
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke();
  }
  const unit = units ? (" " + units) : "";
  ctx.fillStyle = "#8b9bb0"; ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(hi.toFixed(2) + unit, 4, 11);
  ctx.fillText(lo.toFixed(2) + unit, 4, h - 3);

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

  const unit = s.units ? (" " + s.units) : "";
  $("s-latest").textContent = fmt(s.latest) + unit;
  $("s-mean").textContent = fmt(s.mean) + unit;
  $("s-rms").textContent = fmt(s.rms) + unit;
  $("s-p2p").textContent = fmt(s.peak_to_peak) + unit;
  $("s-std").textContent = fmt(s.std_dev, 4) + unit;
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
  draw(s.trace, s.units);
}

let pendingFrame = null;
let rafId = 0;
let stream = null;
function queueRender(s) {
  pendingFrame = s;
  if (document.hidden) return;
  if (!rafId) {
    rafId = requestAnimationFrame(() => {
      rafId = 0;
      if (pendingFrame) render(pendingFrame);
    });
  }
}

function connect() {
  if (stream) stream.close();
  stream = new EventSource(withToken("/api/stream"));
  stream.onopen = () => { $("p-conn").textContent = "connected"; $("p-conn").className = "pill live"; };
  stream.onmessage = (e) => queueRender(JSON.parse(e.data));
  stream.onerror = () => {
    $("p-conn").textContent = "reconnecting"; $("p-conn").className = "pill warn";
  };
}
</script>
</body>
</html>
"""
