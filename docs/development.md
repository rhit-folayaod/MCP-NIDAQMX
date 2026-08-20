# Development history

Narrative of how daq-mcp was built: stages, the problem each stage was
answering, and what we changed when the first design met real hardware or
real MCP clients. Use this as source material for a later paper — it is not
a user guide. Operator docs live in the rest of [docs/](README.md).

This project is **not affiliated with NI / Emerson**. Hardware facts below
come from NI-DAQmx behavior we observed while implementing a *client* of
that driver.

Commits cited are on `main` as of `b205a36`, plus later in-tree work noted
explicitly.

---

## 0. Problem we were actually solving

Most MCP servers wrap HTTP APIs or files. DAQ is different in three ways that
showed up repeatedly:

1. **A wrong tool argument is physical.** A hallucinated channel, a write to
   an input, or an unbounded analog voltage is not a 4xx — it can short a
   line or drive a motor.
2. **The driver owns the pin.** NI-DAQmx reserves a channel for the lifetime
   of a Task. Two processes (MCP server vs dashboard, or MCP Inspector
   spawning a second server) cannot both stream `ai0`.
3. **MCP is request/response.** The protocol cannot “watch a signal.” Snapshot
   tools are the right granularity for an agent turn; they are the wrong
   granularity for a human staring at a plot.

The interesting engineering is therefore **safety + process layout + live
state**, not JSON-RPC plumbing. FastMCP and stdio/HTTP are the transport;
`server.py` is the policy.

---

## 1. Skeleton: tools, simulator, safety defaults

**Commits:** `3789e0b` … `7e32ca1` (`initial mcp daq server`).

**Challenge.** Ship something an editor can launch without NI drivers, and
encode “don’t let an agent drive the bench by default” before the first
real write.

**What we implemented.**

- FastMCP server in `server.py` with a small tool set: `list_devices`,
  `describe_device`, `read_analog`, `read_digital`, `write_digital`,
  `write_analog`, `monitor_analog`, `self_test`.
- Backend interface (`DAQBackend`) with `SimulatedBackend` (pure Python
  `Dev1` / `Dev2`) and a real `NIDAQmxBackend` behind an optional extra.
- Safety knobs that survived every later stage:
  - writes **off** unless `DAQ_MCP_ALLOW_WRITE=1`
  - channel **allowlist**
  - analog output **clamp** (default ±5 V) with `requested` / `applied` /
    `clamped` in the response so the model cannot be lied to
- One hardware operation per tool: open Task, do the job, close Task. That
  avoids leaving a reservation across agent turn-taking.
- `monitor_analog` returns **stats + a short preview**, not thousands of
  floats (context-window cost, and the model does not need the raw array to
  answer “is this sensor alive?”).

**Why it mattered.** CI and clones work with `DAQ_MCP_SIMULATE=1`. The
simulator could not catch several later bugs; it *could* lock the tool
contracts and the safety tests.

---

## 2. Windows stdio deadlock on real NI-DAQmx

**Commit:** `f3846b9` — *Fix tool-call deadlock when NI-DAQmx drivers are present.*

**Challenge.** With drivers installed, Cursor/Inspector **connected** (tools
listed) but every tool **hung forever**. Simulation was fine. That pattern
looks like “MCP is broken”; it was the driver + thread.

**Root cause.** `NIDAQmxBackend` was created lazily on first tool call.
FastMCP runs sync tools on a **worker thread**. On Windows, first contact
with NI-DAQmx from that thread, over **stdio MCP**, deadlocks. The client
is waiting on stdout JSON-RPC; the server is waiting inside the driver.

**Fix.** Probe `get_backend()` on the **main thread** before starting the
transport (`if __name__ == "__main__"`). Log backend selection to **stderr**
(stdout is the MCP byte stream). README paths stopped being machine-local.

**Lesson for a paper.** Lazy init is a footgun when the library is not
thread-safe on first use and the RPC transport owns stdin/stdout.

---

## 3. Digital write “verification” that undid the write

**Commit:** `2c05f0d` — *Read digital output state back from the writing task.*

**Challenge.** `write_digital` to an LED succeeded, then read-back reported
`false`. The model (and the human) concluded the write failed.

**Root cause.** After `task.write()`, we opened a **separate DI task** on the
same line “to verify.” Adding that line as an input **reconfigures** it.
The drive drops; the read is whatever the circuit pulls. Found on a
multifunction USB board; the simulator stored a bool and could not reproduce
it.

**Fix.** Read back from the **same DO task** that performed the write. Safety
docs still require that the response is hardware state, not an echo of
`value=true`.

**Lesson.** “Verify by reading” is correct *if* the read does not change
direction. DAQmx channel direction is not a no-op metadata bit.

---

## 4. Live acquisition and an in-process dashboard

**Commit:** `3b7d048` — *Add live acquisition with a browser dashboard.*

**Challenge.** Snapshot tools cannot express “show me the signal.” A second
process for a plot cannot share the streaming Task.

**What we implemented.**

- `LiveMonitor`: one background thread, one continuous AI Task, a rolling
  window, optional DI poll, cached snapshots for the UI.
- Starlette dashboard in **this process**: SSE frames, digital toggles.
- While a channel is streaming, `read_analog` / `monitor_analog` on **that
  channel** serve `"source": "live_buffer"` instead of a second Task
  (`-50103` resource reserved).
- Streaming errors: only DAQmx “samples not ready yet” (`-200284`) is
  ignored. Overflow (`-200279`), unplug, invalidated Task **stop** the
  monitor, release the channel, and surface the message. A dead Task
  returning empty reads looks exactly like an idle stream if you swallow
  errors.

**Dashboard writes** go through the same allowlist and write gate as MCP.
The human and the agent are not two security domains.

**`--dashboard-only`:** a stdio MCP server exits when the client closes
stdin; that would kill the plot. Dashboard-only skips the stdio loop.

---

## 5. Direction is a physical fact

**Same era as the live dashboard (and wiring later).**

**Challenge.** NI-DAQmx lists every pin a device *supports*. It does not know
that `line0` is a button and `line6` is an LED. An allowlist of “all DIO”
lets an agent drive an input and fight another driver.

**Fix.** Channels are declared **by role**: analog in, analog out, digital
in, digital out. `WRITABLE_CHANNELS` is derived from the output groups.
Inputs remain readable. The dashboard needed that split for UI anyway; the
safety model used it as the source of truth.

---

## 6. HTTP MCP, Inspector collisions, token gate

**Commit:** `0e3911a` — *Add channel picker, HTTP MCP, and token-gated remote access.*

**Challenge.** MCP Inspector (and later a phone/cloud agent) wants HTTP.
Launching a **second** `server.py` steals the device and the port. Binding
`0.0.0.0` without auth would expose a write-capable DAQ server.

**Fix.**

- `--http`: one Starlette app, dashboard + Streamable HTTP MCP at `/mcp/`,
  MCP lifespan mounted correctly (session manager must start).
- `DAQ_MCP_TOKEN`: required for non-loopback binds. Browser EventSource
  cannot set `Authorization`, so `?token=` is accepted as well as Bearer /
  `X-DAQ-Token`.
- Dashboard **channel picker** loads driver inventory, lets a human assign
  roles, persists a gitignored wiring file, and **updates the same
  allowlist** the tools consult.
- `get_wiring` / `set_wiring` so an agent can see/change what the UI set.
- CI: simulator pytest on push.

**Remote agents** still need a tunnel to loopback plus Bearer. The server
does not want to be a public DAQ appliance.

---

## 7. Named wiring profiles

**Commit:** `d6f8020`.

**Challenge.** One gitignored JSON file is not enough when you switch
benches (simulator vs CompactDAQ vs a USB board). Phone agent and laptop UI
must share the **active** profile.

**Fix.** `.daq_mcp_profiles/` (gitignored): save / load / delete by name.
MCP mirrors the dashboard. Live acquisition restarts on a successful load so
the plot matches the new `live_channel` / rate.

---

## 8. CompactDAQ inventory and captures

**Commit:** `f797921` (and related dashboard work).

**Challenge.** `describe_device("cDAQ1")` on a chassis often returns **no
pins**. Channels live on `cDAQ1ModN`. A picker that only queried the chassis
looked empty. Agents that used chassis names could not choose modules.

**Fix.** Merge module inventories when the selected name is a chassis (or
`all`). Picker and `describe_device` paths stay consistent.

**Captures.** MCP cannot hold a full run in context. `save_capture` writes
the live window (metrics + samples) under `.daq_mcp_captures/` (gitignored)
and returns a text summary. Dashboard can download JSON/CSV. That is the
“lab notebook” hook without pretending the repo is a data store.

---

## 9. Making the repo public without leaking a bench

**Commits:** `9cc53e4`, `de7895f`, `d5c26c7`, `077c39c`, `b63b510` (docs half).

**Challenge.** Demo config, tokens, tunnel URLs, and kit-specific README
copy do not belong on a public GitHub repo.

**Fix.**

- Gitignore: `.cursor/mcp.json`, wiring profiles, captures, `.env*`.
- Apache 2.0 `LICENSE` / `NOTICE`, source headers, CONTRIBUTING, Ruff.
- README rewritten as a **general** MCP+DAQmx client; kit walkthroughs
  moved out of the homepage.
- `docs/` as the user-facing site (architecture, safety, config, tools,
  devices, examples, limitations, troubleshooting).
- Examples use **simulator names** (`Dev1/…`) so clones run without a
  chassis.

**Rule that kept biting in later demos:** local MCP config is **not** what a
cloud agent reads. Pointing Cursor desktop at a URL does not configure
cursor.com/agents.

---

## 10. Specialty analog: strain, thermocouple, IEPE accel

**Commit:** `b63b510` — *Add specialty AI measurements and public docs site.*

**Challenge.** CompactDAQ modules are not “voltage AI with extra steps.”

| Typical module | If you `add_ai_voltage_chan` |
| --- | --- |
| Strain bridge (e.g. NI 9236) | Driver rejects the measurement type |
| Thermocouple (e.g. NI 9213) | Wrong create-channel; rates are ~tens of Hz |
| IEPE / DSA accel (e.g. NI 9234) | Needs IEPE + **sample clock**; on-demand reads fail |

An agent that only knows `read_analog(..., voltage)` looks broken on a
training chassis that is working.

**Fix.** `AiOptions` / `measurement` on `read_analog`, `monitor_analog`,
`start_live`: `voltage` | `strain` | `thermocouple` | `accelerometer`, plus
TC type, gage factor, accel mV/g. Backends map to the matching DAQmx
create-channel. Rates clamped per kind (thermocouple especially). Units
travel with snapshots (`V`, `strain`, `deg_C`, `g`). `describe_device` may
hint `suggested_measurement` from product-type strings.

Simulator produces plausible fake traces so tests do not need hardware.

**Bring-up on a mixed CompactDAQ** (local, not in git): strain and type-K
thermocouple one-shots worked while a different module streamed; analog out
and accel could run together because they are **different modules / Tasks**.
That is a chassis property, not a promise of multi-stream AI (the server
still allows **one** live AI stream).

---

## 11. Dashboard lag at high sample rates

**Commit:** `b205a36` — *Keep the live dashboard responsive at high sample rates.*

**Challenge.** A 9234-class stream at several kHz filled a 20k-sample window.
Every SSE tick (~20 Hz) **copied and summarized the whole window on the HTTP
event loop** (same loop as Streamable HTTP MCP). The chart hitching, and MCP
tool calls feeling slow, were the same bottleneck.

A second UI bug: EventSource `onerror` **closed and reconnected**, which
some browsers fire spuriously → reconnect storm → frozen/laggy page.

**Fix.**

- Short **display** deque (~4k samples) for chart stats/trace; full window
  kept for `save_capture`.
- Snapshot **published on the acquisition thread**; SSE reads the cache.
- SSE ~10 Hz; `json.dumps` offloaded with `asyncio.to_thread`.
- Browser: `requestAnimationFrame` coalescing; let EventSource reconnect
  itself; min/max without `Math.min(...trace)` spread.
- Axis labels use snapshot **units**, not always volts.

**Related production issue.** After hours of streaming, `total_samples`
could **stop increasing** while `streaming=true` and `error=null` (empty
reads or a stuck `read_stream`). Restart `stop_live` / `start_live` (or
the HTTP process). Treat “chart frozen” as possibly a **dead consumer**,
not only a frontend bug. Troubleshooting already documents `-200279`.

---

## 12. Remote / phone agents (operational, not a git feature)

**Challenge.** The demo we cared about was: **phone prompts an agent, laptop
shows the plot.** Cloud VMs have **no USB and no NI-DAQmx**. “Create a cloud
agent on this repo” only boots a **simulator** (or empty hardware) inside
Cursor’s environment-setup VM. `.cursor/mcp.json` is gitignored and **not**
sent to that VM.

**What actually works.**

1. One `--http` process on the bench PC (writes enabled only for the demo).
2. Tunnel to `127.0.0.1:8765`.
3. Cloud MCP config: HTTP URL `https://…/mcp/` plus `Authorization: Bearer …`.
4. Laptop browser: `http://127.0.0.1:8765/?token=…` (local plot, not the
   tunnel — lower latency, no extra hop).

**Network.** Some campus/Ethernet paths **block Cloudflare’s tunnel port**.
Binding `cloudflared` to a phone-hotspot interface (`http2`) was the path
that registered. Quick tunnels mint a **new hostname** every restart; the
cloud MCP URL must be updated or the agent reports “tools not connected.”

**stdio vs `/mcp/`.** Dashboard-only or stdio-with-dashboard has **no**
Streamable HTTP MCP. Cloud clients need `--http`.

**GET `/mcp/` without MCP headers** returns 401 / 406 / missing session —
that is expected. It is not a webpage.

Do not commit tokens or tunnel hostnames.

---

## 13. Hardware mapping we only learned on the bench

These are **configuration** facts, not server features. They belong in a
paper as “the agent still needs a correct allowlist / prompt.”

- **Fan / analog out.** On a 9263-style screw block, a BNC on terminals 2
  and 3 is typically **AO 1 and its COM**, i.e. `…/ao1`, not `ao2`. `ao1` at
  **1 V** can be inaudible on a 6000 RPM fan; **~4 V** was clearly spinning
  with AO still inside the ±5 V clamp. Always return to **0 V**.
- **LEDs.** Eight DO lines on a 9472-style module mapped 1:1 to face LEDs
  when the wiring profile listed `port0/line0`–`line7` as outputs.
- **Accel + fan.** Streaming `accelerometer` on one module while writing AO
  on another is the “two tools, one chassis” demo. RMS rose when the fan
  ran — useful as a qualitative check, not a calibrated RPM.

Prompts that said “1.0 V on ao0 or ao2” wasted time. The server did the
right thing (write the channel you named); the **name** was wrong.

---

## 14. LED chase without one RPC per blink

**In-tree after the public docs cut** (`animate_digital` on `server.py`;
simulator tests included). Not every clone of `main` at `b205a36` has this.

**Challenge.** A “dance the LEDs” demo via eight `write_digital` calls over
**cloud HTTP MCP** is one round-trip per edge. Latency makes a chase look
broken. Blocking the asyncio loop for a long Python loop would also freeze
SSE if the tool ran on that loop (FastMCP sync tools are intended to run in
a worker thread).

**Fix.** `animate_digital`: ping-pong one-hot over allowlisted DOs **inside
the server process**, update the live DO cache so dashboard lamps move,
**always** all-off in `finally`. Duration and step capped. Same write gate
and `_check_writable` as a single-line write.

**Lesson.** For physical patterns, **one tool = one show**, not one tool =
one edge, when the client is remote.

---

## 15. Tool-design rules that survived

These are the invariants to keep if the paper discusses “MCP for I/O”:

| Rule | Why |
| --- | --- |
| One snapshot tool ≈ one Task lifetime | Agents abandon mid-plan; don’t leak reservations |
| Summaries over raw waveforms | Context limits; “is it alive?” needs RMS not 20k floats |
| Writes off, allowlist, direction, AO clamp | Physical faults vs HTTP errors |
| Same gates for the browser | Otherwise the UI is a privilege escalation |
| One live AI stream, one process | DAQmx reservation + port |
| Live errors are fatal except “not ready” | Empty buffer ≠ healthy stream |
| Stderr for logs | Stdout is MCP |
| Probe NI-DAQmx on the main thread (Windows) | Stdio deadlock |
| DO read-back from the DO task | DI verify reconfigures the line |
| Gitignore bench secrets | Public repo vs demo tokens |

---

## 16. Suggested paper threads

1. **Safety as the product.** Compare allowlist + direction + clamp +
   explicit `clamped` to “the model promised it wrote 10 V.”
2. **Process identity.** Why GUI and agent must share an address space (or
   an explicit multiplexer) when the driver is exclusive.
3. **Measurement type as API.** Voltage-shaped tools on specialty modules
   fail closed; `measurement=` is part of the contract.
4. **Remote agents.** Transport (HTTP MCP + auth + tunnel) vs environment
   (cloud VM has no DAQ). Desktop `mcp.json` ≠ cloud MCP.
5. **Evaluation.** Simulator tests lock contracts; several bugs **only**
   appeared on hardware (DO read-back, stdio deadlock, strain vs voltage).
6. **What we refused.** Full DAQmx task graphs, counters, multi-stream AI,
   shipping LabVIEW/TestStand, committing wiring or tokens.

---

## Timeline (git)

| Commit | Stage |
| --- | --- |
| `7e32ca1` | Initial MCP + simulator + safety skeleton |
| `f3846b9` | Main-thread NI-DAQmx probe (Windows stdio deadlock) |
| `2c05f0d` | DO task read-back |
| `3b7d048` | Live monitor + in-process dashboard + direction |
| `0e3911a` | Picker, `--http`, token, CI |
| `d6f8020` | Named local profiles |
| `f797921` | cDAQ inventory merge, captures, TECH_STACK |
| `077c39c` | Apache 2.0, public polish |
| `b63b510` | Strain / TC / accel + `docs/` |
| `b205a36` | High-rate dashboard / SSE |

Later: remote-demo operations, bench AO/DO mapping, `animate_digital`.
