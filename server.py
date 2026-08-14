"""
daq-mcp — an MCP server that lets an AI coding agent talk to NI DAQ hardware.

Transport: stdio (what Cursor / Claude Desktop / Claude Code launch).
Backend:   real nidaqmx if a device is present, otherwise a simulated one,
           so the server is runnable by anyone who clones the repo.

Run:  uv run server.py
Env:  DAQ_MCP_SIMULATE=1     force the simulated backend
      DAQ_MCP_ALLOW_WRITE=1  enable digital/analog output (off by default)
"""

from __future__ import annotations

import logging
import json
import math
import os
import sys
import threading
from typing import Any, Literal

from fastmcp import FastMCP

from daq_mcp.backend import get_backend
from daq_mcp.live import LiveMonitor
from daq_mcp.wiring import (
    Wiring,
    delete_profile,
    get_active_profile_name,
    list_profiles,
    load_profile,
    load_wiring,
    save_profile,
    set_active_profile_name,
    validate_wiring,
    wiring_path,
)

mcp = FastMCP("daq-mcp")

# --------------------------------------------------------------------------
# Safety configuration
#
# This is the interesting part of the project, not the protocol plumbing.
# An LLM driving physical outputs is a real footgun: a hallucinated channel
# name or a stray voltage can damage hardware or hurt someone. The rules:
#   1. Writes are OFF unless explicitly enabled by env var.
#   2. Only channels on the allowlist can be touched at all.
#   3. Only channels wired as outputs can be written to.
#   4. Analog output is clamped to a configured safe range.
#   5. Every write returns what it actually did, so the model can verify.
#
# Rule 3 exists because direction is a physical fact, not a preference. Driving
# a line that a button also drives shorts one driver against the other. Listing
# channels by direction means "this is an input" is enforced, not remembered.
#
# The defaults below match one USB-6421 bench. Override them from the dashboard
# channel picker; named profiles land in .daq_mcp_profiles/ (gitignored).
# --------------------------------------------------------------------------

WRITES_ENABLED = os.getenv("DAQ_MCP_ALLOW_WRITE") == "1"

_DEFAULT_WIRING = Wiring()
ANALOG_INPUTS = set(_DEFAULT_WIRING.analog_inputs)
ANALOG_OUTPUTS = set(_DEFAULT_WIRING.analog_outputs)
DIGITAL_INPUTS = set(_DEFAULT_WIRING.digital_inputs)
DIGITAL_OUTPUTS = set(_DEFAULT_WIRING.digital_outputs)

CHANNEL_ALLOWLIST = ANALOG_INPUTS | ANALOG_OUTPUTS | DIGITAL_INPUTS | DIGITAL_OUTPUTS
WRITABLE_CHANNELS = ANALOG_OUTPUTS | DIGITAL_OUTPUTS

AO_VOLTAGE_LIMITS = (-5.0, 5.0)

# Live acquisition defaults. The dashboard is opt-in: an MCP server that
# silently opens a network port on startup would be a rude surprise.
DASHBOARD_ENABLED = os.getenv("DAQ_MCP_DASHBOARD") == "1"
DASHBOARD_HOST = os.getenv("DAQ_MCP_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DAQ_MCP_DASHBOARD_PORT", "8765"))
# Shared secret for --http / non-loopback binds. Empty means open (loopback only).
AUTH_TOKEN = os.getenv("DAQ_MCP_TOKEN", "").strip() or None
# Env vars win over the wiring file so a one-off launch can override without
# editing the picker. The file wins over the hardcoded bench defaults.
LIVE_CHANNEL = os.getenv("DAQ_MCP_LIVE_CHANNEL") or _DEFAULT_WIRING.live_channel
LIVE_RATE_HZ = float(os.getenv("DAQ_MCP_LIVE_RATE") or _DEFAULT_WIRING.live_rate_hz)

_PREVIEW_MAX_POINTS = 50

live = LiveMonitor(get_backend)
_wiring_lock = threading.Lock()
_current_wiring = _DEFAULT_WIRING
# True once the HTTP dashboard is actually listening (env flag alone is not enough
# for --dashboard-only / --http).
_dashboard_serving = False


def _apply_wiring(
    wiring: Wiring,
    *,
    restart_live: bool = False,
    respect_env: bool = False,
) -> dict[str, Any]:
    """Install wiring into the module-level allowlists the tools consult.

    Profile save/load always takes the profile's live channel/rate. Env overrides
    (`DAQ_MCP_LIVE_CHANNEL` / `DAQ_MCP_LIVE_RATE`) apply only when
    `respect_env=True` (startup with no explicit profile action).
    """
    global ANALOG_INPUTS, ANALOG_OUTPUTS, DIGITAL_INPUTS, DIGITAL_OUTPUTS
    global CHANNEL_ALLOWLIST, WRITABLE_CHANNELS, LIVE_CHANNEL, LIVE_RATE_HZ
    global _current_wiring

    with _wiring_lock:
        ANALOG_INPUTS = set(wiring.analog_inputs)
        ANALOG_OUTPUTS = set(wiring.analog_outputs)
        DIGITAL_INPUTS = set(wiring.digital_inputs)
        DIGITAL_OUTPUTS = set(wiring.digital_outputs)
        CHANNEL_ALLOWLIST = (
            ANALOG_INPUTS | ANALOG_OUTPUTS | DIGITAL_INPUTS | DIGITAL_OUTPUTS
        )
        WRITABLE_CHANNELS = ANALOG_OUTPUTS | DIGITAL_OUTPUTS
        if respect_env:
            LIVE_CHANNEL = os.getenv("DAQ_MCP_LIVE_CHANNEL") or wiring.live_channel
            LIVE_RATE_HZ = float(
                os.getenv("DAQ_MCP_LIVE_RATE") or wiring.live_rate_hz
            )
        else:
            LIVE_CHANNEL = wiring.live_channel
            LIVE_RATE_HZ = wiring.live_rate_hz
        _current_wiring = wiring

    result: dict[str, Any] = {"ok": True, "wiring": wiring.to_dict()}
    if restart_live:
        if live.is_streaming():
            live.stop()
        started = live.start(
            LIVE_CHANNEL, LIVE_RATE_HZ, poll_inputs=sorted(DIGITAL_INPUTS)
        )
        if "error" in started:
            result["live_error"] = started["error"]
        else:
            result["live"] = started
    return result


def _load_wiring_at_startup() -> None:
    try:
        wiring = load_wiring()
    except ValueError as exc:
        logging.getLogger("daq-mcp").warning("ignoring wiring file: %s", exc)
        return
    # If a named profile is active, it wins; otherwise allow env overrides.
    _apply_wiring(
        wiring,
        restart_live=False,
        respect_env=get_active_profile_name() is None,
    )
    active = get_active_profile_name()
    if active:
        logging.getLogger("daq-mcp").info("loaded wiring profile %r", active)
    elif wiring_path().is_file():
        logging.getLogger("daq-mcp").info("loaded wiring from %s", wiring_path())


def _inventory_for_device(device: str | None = None) -> dict[str, Any]:
    devices = get_backend().list_devices()
    if not devices:
        return {"devices": [], "selected": None}
    names = [d["name"] for d in devices]
    chosen = device or _current_wiring.device
    if chosen not in names:
        chosen = names[0]
    info = get_backend().describe_device(chosen)
    if "error" in info:
        return {"devices": devices, "selected": chosen, "error": info["error"]}
    return {"devices": devices, "selected": chosen, "inventory": info}


class ChannelNotAllowed(Exception):
    pass


class ChannelNotWritable(Exception):
    pass


def _check(channel: str) -> None:
    if channel not in CHANNEL_ALLOWLIST:
        raise ChannelNotAllowed(
            f"{channel!r} is not on the allowlist. Allowed: {sorted(CHANNEL_ALLOWLIST)}"
        )


def _check_writable(channel: str) -> None:
    """Allowlisted is not the same as writable: inputs are readable only."""
    _check(channel)
    if channel not in WRITABLE_CHANNELS:
        raise ChannelNotWritable(
            f"{channel!r} is an input and cannot be written. "
            f"Writable: {sorted(WRITABLE_CHANNELS)}"
        )


def _downsample(samples: list[float], max_points: int = _PREVIEW_MAX_POINTS) -> list[float]:
    n = len(samples)
    if n <= max_points:
        return list(samples)
    if max_points <= 1:
        return [samples[0]] if n else []
    step = (n - 1) / (max_points - 1)
    return [samples[int(round(i * step))] for i in range(max_points)]


def _waveform_stats(samples: list[float]) -> dict[str, float]:
    n = len(samples)
    if n == 0:
        return {
            "mean": 0.0,
            "rms": 0.0,
            "peak_to_peak": 0.0,
            "std_dev": 0.0,
            "min": 0.0,
            "max": 0.0,
        }
    mean = sum(samples) / n
    rms = math.sqrt(sum(x * x for x in samples) / n)
    lo = min(samples)
    hi = max(samples)
    variance = sum((x - mean) ** 2 for x in samples) / n
    return {
        "mean": mean,
        "rms": rms,
        "peak_to_peak": hi - lo,
        "std_dev": math.sqrt(variance),
        "min": lo,
        "max": hi,
    }


# --------------------------------------------------------------------------
# Tools
#
# Granularity note: each tool is one complete hardware operation. Splitting
# "create task / add channel / start / read / close" into five tools would
# force the model into five round-trips and let it leave a task open. One
# call = one task, opened and closed inside the function.
# --------------------------------------------------------------------------


@mcp.tool
def list_devices() -> list[dict]:
    """List NI DAQ devices visible to this machine.

    Returns the device name, product type, serial number, and which channel
    types it supports. Call this first — channel names are device-specific.
    """
    return get_backend().list_devices()


@mcp.tool
def describe_device(device: str) -> dict:
    """Describe one device's full channel inventory.

    Returns available analog input, analog output, digital input, and digital
    output channels, plus voltage ranges and max sample rates. Use this to
    pick valid channel names before reading or writing.
    """
    return get_backend().describe_device(device)


@mcp.tool
def read_analog(
    channel: str,
    samples: int = 1,
    rate_hz: float = 1000.0,
    terminal_config: Literal["default", "rse", "nrse", "diff"] = "default",
) -> dict:
    """Read voltage samples from an analog input channel.

    channel: fully qualified, e.g. "Dev1/ai0"
    samples: how many samples to acquire (1 = single instantaneous reading)
    rate_hz: sample rate for multi-sample reads

    Returns the samples plus min/mean/max, so the model can reason about the
    signal without pulling thousands of raw floats into context.
    """
    _check(channel)
    # A live stream holds this channel open, so a one-shot task would fail with
    # a resource-reserved error. Serve the most recent samples instead.
    if live.is_streaming(channel):
        values = live.recent(samples)
        return {
            "channel": channel,
            "samples": values,
            "rate_hz": live.rate_hz,
            "terminal_config": terminal_config,
            "source": "live_buffer",
            "min": min(values) if values else 0.0,
            "mean": sum(values) / len(values) if values else 0.0,
            "max": max(values) if values else 0.0,
        }
    return get_backend().read_analog(channel, samples, rate_hz, terminal_config)


@mcp.tool
def read_digital(channel: str) -> dict:
    """Read the current logic level of a digital input line.

    channel: e.g. "Dev1/port0/line0"
    Returns {"channel": ..., "value": true/false}.
    """
    _check(channel)
    return get_backend().read_digital(channel)


@mcp.tool
def write_digital(channel: str, value: bool) -> dict:
    """Set a digital output line high or low.

    Requires DAQ_MCP_ALLOW_WRITE=1. Returns the line's state after the write
    (read back from hardware, not echoed) so the model can confirm the effect.
    """
    if not WRITES_ENABLED:
        return {"error": "Writes disabled. Set DAQ_MCP_ALLOW_WRITE=1 to enable."}
    _check_writable(channel)
    result = get_backend().write_digital(channel, value)
    if "error" not in result:
        live.set_digital_output(channel, bool(result["value"]))
    return result


@mcp.tool
def write_analog(channel: str, voltage: float) -> dict:
    """Set an analog output channel to a DC voltage.

    Clamped to AO_VOLTAGE_LIMITS. Returns both the requested and the actual
    applied voltage, and flags whether clamping occurred — never silently
    changes the value the model asked for.
    """
    if not WRITES_ENABLED:
        return {"error": "Writes disabled. Set DAQ_MCP_ALLOW_WRITE=1 to enable."}
    _check_writable(channel)
    lo, hi = AO_VOLTAGE_LIMITS
    applied = max(lo, min(hi, voltage))
    result = get_backend().write_analog(channel, applied)
    if "error" in result:
        return result
    return {
        "channel": channel,
        "requested_voltage": voltage,
        "applied_voltage": applied,
        "clamped": applied != voltage,
    }


@mcp.tool
def monitor_analog(channel: str, duration_s: float, rate_hz: float = 1000.0) -> dict:
    """Acquire a finite analog waveform and return summary statistics.

    Built for the "is my sensor behaving?" question: returns mean, RMS, peak-
    to-peak, standard deviation, and a downsampled preview rather than the
    full sample array. Keeps large acquisitions out of the context window.
    """
    _check(channel)
    if live.is_streaming(channel):
        wanted = max(1, int(round(duration_s * rate_hz)))
        samples = live.recent(wanted)
        result = {"sample_count": len(samples)}
    else:
        result = get_backend().acquire_waveform(channel, duration_s, rate_hz)
        if "error" in result:
            return result
        samples = result.get("samples", [])
    stats = _waveform_stats(samples)
    return {
        "channel": channel,
        "duration_s": duration_s,
        "rate_hz": rate_hz,
        "sample_count": result.get("sample_count", len(samples)),
        **stats,
        "preview": _downsample(samples),
    }


@mcp.tool
def self_test(device: str) -> dict:
    """Run the device's built-in self-test and report pass/fail.

    Useful as a first diagnostic when reads return unexpected values.
    """
    return get_backend().self_test(device)


# --------------------------------------------------------------------------
# Live acquisition
#
# One background task stays open and fills a rolling window. While it runs,
# read_analog and monitor_analog on that channel serve from the buffer, since
# the driver will not hand the same channel to a second task.
# --------------------------------------------------------------------------


@mcp.tool
def start_live(channel: str = LIVE_CHANNEL, rate_hz: float = LIVE_RATE_HZ) -> dict:
    """Begin continuous background acquisition on an analog input channel.

    Powers the browser dashboard and lets later reads answer "what has the
    signal been doing?" rather than "what is it this instant". Only one
    channel can stream at a time.
    """
    _check(channel)
    result = live.start(channel, rate_hz, poll_inputs=sorted(DIGITAL_INPUTS))
    if "error" in result:
        return result
    return {
        "streaming": True,
        "channel": channel,
        "rate_hz": rate_hz,
        "dashboard_url": _dashboard_url(),
    }


@mcp.tool
def stop_live() -> dict:
    """Stop continuous acquisition and release the channel for one-shot reads."""
    return live.stop()


@mcp.tool
def live_status() -> dict:
    """Summarize the running acquisition: stats, latest value, digital states.

    Returns statistics over the rolling window plus a short preview, not the
    whole buffer, for the same context-cost reason as monitor_analog.
    """
    snap = live.snapshot(max_points=_PREVIEW_MAX_POINTS)
    snap["dashboard_url"] = _dashboard_url()
    return snap


@mcp.tool
def get_wiring() -> dict:
    """Return the active channel roles, profile name, and saved profile list."""
    with _wiring_lock:
        wiring = _current_wiring.to_dict()
    return {
        "wiring": wiring,
        "active_profile": get_active_profile_name(),
        "profiles": list_profiles(),
        "path": str(wiring_path()),
        "allowlist": sorted(CHANNEL_ALLOWLIST),
        "writable": sorted(WRITABLE_CHANNELS),
    }


@mcp.tool
def set_wiring(
    device: str,
    live_channel: str,
    analog_inputs: list[str],
    digital_inputs: list[str],
    digital_outputs: list[str],
    analog_outputs: list[str] | None = None,
    live_rate_hz: float = 1000.0,
    profile_name: str | None = None,
) -> dict:
    """Update channel roles and save them as a named local profile.

    If profile_name is omitted, overwrites the active profile (or creates
    'default'). Live acquisition restarts on success.
    """
    body = {
        "device": device,
        "live_channel": live_channel,
        "live_rate_hz": live_rate_hz,
        "analog_inputs": analog_inputs,
        "analog_outputs": list(analog_outputs or []),
        "digital_inputs": digital_inputs,
        "digital_outputs": digital_outputs,
        "profile_name": profile_name,
    }
    return _dashboard_set_wiring(body)


@mcp.tool
def list_wiring_profiles() -> dict:
    """List named wiring profiles saved on this machine."""
    return {
        "active_profile": get_active_profile_name(),
        "profiles": list_profiles(),
    }


@mcp.tool
def load_wiring_profile(name: str) -> dict:
    """Activate a saved wiring profile and restart live acquisition."""
    return _dashboard_load_profile(name)


@mcp.tool
def delete_wiring_profile(name: str) -> dict:
    """Delete a named wiring profile from local disk."""
    return _dashboard_delete_profile(name)


def _dashboard_url() -> str | None:
    if not (_dashboard_serving or DASHBOARD_ENABLED):
        return None
    base = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/"
    if AUTH_TOKEN:
        return f"{base}?token=…"
    return base


# --------------------------------------------------------------------------
# Resources — read-only context the model can pull in without a tool call.
# --------------------------------------------------------------------------


@mcp.resource("daq://config")
def current_config() -> str:
    """The active safety configuration: allowlist, write status, voltage limits."""
    lo, hi = AO_VOLTAGE_LIMITS
    lines = [
        "daq-mcp safety configuration",
        f"writes_enabled: {WRITES_ENABLED}",
        f"ao_voltage_limits: [{lo}, {hi}]",
        "readable_channels:",
    ]
    for ch in sorted(CHANNEL_ALLOWLIST):
        lines.append(f"  - {ch}")
    lines.append("writable_channels:")
    for ch in sorted(WRITABLE_CHANNELS) or []:
        lines.append(f"  - {ch}")
    if not WRITABLE_CHANNELS:
        lines.append("  (none)")
    if live.is_streaming():
        snap = live.snapshot(max_points=1)
        lines.append(f"live_streaming: {snap['channel']} @ {snap['rate_hz']} Hz")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Dashboard wiring
#
# The browser writes through the same gate as the model: this function is what
# the dashboard app is given, so there is no second, laxer path to the pins.
# --------------------------------------------------------------------------


def _dashboard_set_digital(channel: str, value: bool) -> dict:
    if not WRITES_ENABLED:
        return {"error": "Writes disabled. Set DAQ_MCP_ALLOW_WRITE=1 to enable."}
    try:
        _check_writable(channel)
    except (ChannelNotAllowed, ChannelNotWritable) as exc:
        return {"error": str(exc)}
    result = get_backend().write_digital(channel, value)
    if "error" not in result:
        live.set_digital_output(channel, bool(result["value"]))
    return result


def _dashboard_config() -> dict:
    with _wiring_lock:
        wiring = _current_wiring.to_dict()
    return {
        "backend": type(get_backend()).__name__,
        "writes_enabled": WRITES_ENABLED,
        "digital_inputs": sorted(DIGITAL_INPUTS),
        "digital_outputs": sorted(DIGITAL_OUTPUTS),
        "analog_inputs": sorted(ANALOG_INPUTS),
        "analog_outputs": sorted(ANALOG_OUTPUTS),
        "ao_voltage_limits": list(AO_VOLTAGE_LIMITS),
        "live_channel": LIVE_CHANNEL,
        "live_rate_hz": LIVE_RATE_HZ,
        "wiring": wiring,
        "wiring_file": str(wiring_path()),
        "active_profile": get_active_profile_name(),
        "profiles": list_profiles(),
    }


def _dashboard_inventory(device: str | None = None) -> dict:
    return _inventory_for_device(device)


def _dashboard_set_wiring(body: dict[str, Any]) -> dict:
    name = body.get("profile_name") or body.get("name")
    try:
        wiring = Wiring.from_dict(body)
    except ValueError as exc:
        return {"error": str(exc)}

    inv_pack = _inventory_for_device(wiring.device)
    inventory = inv_pack.get("inventory")
    problems = validate_wiring(wiring, inventory)
    if problems:
        return {"error": "; ".join(problems), "problems": problems}

    profile_name = name if isinstance(name, str) and name.strip() else (
        get_active_profile_name() or "default"
    )
    try:
        path = save_profile(str(profile_name), wiring, make_active=True)
    except (OSError, ValueError) as exc:
        return {"error": f"Could not save profile: {exc}"}

    applied = _apply_wiring(wiring, restart_live=True)
    applied["path"] = str(path)
    applied["active_profile"] = get_active_profile_name()
    applied["profiles"] = list_profiles()
    return applied


def _dashboard_load_profile(name: str) -> dict:
    try:
        wiring = load_profile(name)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}
    set_active_profile_name(name)
    applied = _apply_wiring(wiring, restart_live=True)
    applied["active_profile"] = get_active_profile_name()
    applied["profiles"] = list_profiles()
    return applied


def _dashboard_delete_profile(name: str) -> dict:
    try:
        result = delete_profile(name)
    except (ValueError, OSError) as exc:
        return {"error": str(exc)}
    # If we deleted the active profile, keep running on in-memory wiring until
    # the user loads or saves another — do not silently revert to defaults.
    result["active_profile"] = get_active_profile_name()
    result["profiles"] = list_profiles()
    return result


def _dashboard_list_profiles() -> dict:
    return {
        "active_profile": get_active_profile_name(),
        "profiles": list_profiles(),
    }


def _build_dashboard_app(mcp_app=None):
    from daq_mcp.dashboard import create_app

    return create_app(
        snapshot=lambda: live.snapshot(max_points=400),
        set_digital=_dashboard_set_digital,
        config=_dashboard_config,
        inventory=_dashboard_inventory,
        set_wiring=_dashboard_set_wiring,
        list_profiles=_dashboard_list_profiles,
        load_profile=_dashboard_load_profile,
        delete_profile=_dashboard_delete_profile,
        mcp_app=mcp_app,
        auth_token=AUTH_TOKEN,
    )


def _dashboard_port_available() -> bool:
    from daq_mcp.auth import is_loopback_host
    from daq_mcp.dashboard import port_is_free

    if not is_loopback_host(DASHBOARD_HOST) and not AUTH_TOKEN:
        logging.getLogger("daq-mcp").error(
            "DAQ_MCP_DASHBOARD_HOST=%s is not loopback; set DAQ_MCP_TOKEN "
            "before exposing the dashboard / MCP HTTP endpoint",
            DASHBOARD_HOST,
        )
        return False
    if port_is_free(DASHBOARD_HOST, DASHBOARD_PORT):
        return True
    logging.getLogger("daq-mcp").error(
        "dashboard port %d is already in use (another server still running?); "
        "set DAQ_MCP_DASHBOARD_PORT to pick another",
        DASHBOARD_PORT,
    )
    return False


def _start_dashboard() -> bool:
    global _dashboard_serving
    from daq_mcp.dashboard import serve_in_thread

    if not _dashboard_port_available():
        return False
    serve_in_thread(_build_dashboard_app(), DASHBOARD_HOST, DASHBOARD_PORT)
    _dashboard_serving = True
    logging.getLogger("daq-mcp").info(
        "dashboard on http://%s:%d/%s",
        DASHBOARD_HOST,
        DASHBOARD_PORT,
        " (token required)" if AUTH_TOKEN else "",
    )
    return True


if __name__ == "__main__":
    # stderr only — stdout carries the JSON-RPC stream.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log = logging.getLogger("daq-mcp")

    # Three ways to run, because a DAQ device belongs to one process at a time
    # and different clients need to reach that one process differently:
    #   (default)         stdio MCP, for an editor that launches this itself
    #   --dashboard-only  browser only, no MCP client attached
    #   --http            dashboard and MCP on one port, so Inspector and a
    #                     browser (and an editor) can all attach at once
    dashboard_only = "--dashboard-only" in sys.argv
    http_mode = "--http" in sys.argv

    # NI-DAQmx must be probed on the main thread. FastMCP runs sync tools in a
    # worker thread; lazy backend init there can deadlock on Windows over stdio.
    backend = get_backend()
    log.info("backend=%s writes_enabled=%s", type(backend).__name__, WRITES_ENABLED)
    _load_wiring_at_startup()

    def start_live_acquisition() -> None:
        # Start streaming immediately so the dashboard has a trace on open,
        # rather than requiring the model to call start_live first.
        started = live.start(
            LIVE_CHANNEL, LIVE_RATE_HZ, poll_inputs=sorted(DIGITAL_INPUTS)
        )
        if "error" in started:
            log.warning("live acquisition did not start: %s", started["error"])

    if http_mode:
        import uvicorn

        if not _dashboard_port_available():
            sys.exit(1)
        start_live_acquisition()
        app = _build_dashboard_app(mcp_app=mcp.http_app(path="/"))
        _dashboard_serving = True
        log.info(
            "dashboard on http://%s:%d/ | MCP endpoint http://%s:%d/mcp/%s",
            DASHBOARD_HOST,
            DASHBOARD_PORT,
            DASHBOARD_HOST,
            DASHBOARD_PORT,
            " (token required)" if AUTH_TOKEN else "",
        )
        try:
            uvicorn.run(
                app,
                host=DASHBOARD_HOST,
                port=DASHBOARD_PORT,
                log_level="warning",
                access_log=False,
            )
        finally:
            live.stop()

    elif dashboard_only:
        _start_dashboard()
        start_live_acquisition()
        log.info("dashboard-only mode; Ctrl+C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            live.stop()

    else:
        if DASHBOARD_ENABLED:
            _start_dashboard()
            start_live_acquisition()
        mcp.run()
