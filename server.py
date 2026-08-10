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
import math
import os
import sys
import threading
from typing import Literal

from fastmcp import FastMCP

from daq_mcp.backend import get_backend
from daq_mcp.live import LiveMonitor

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
# --------------------------------------------------------------------------

WRITES_ENABLED = os.getenv("DAQ_MCP_ALLOW_WRITE") == "1"

ANALOG_INPUTS = {"Dev1/ai0", "Dev1/ai1"}
ANALOG_OUTPUTS: set[str] = set()
DIGITAL_INPUTS = {"Dev1/port0/line0", "Dev1/port0/line1"}  # buttons
DIGITAL_OUTPUTS = {"Dev1/port0/line6", "Dev1/port0/line7"}  # LEDs

CHANNEL_ALLOWLIST = ANALOG_INPUTS | ANALOG_OUTPUTS | DIGITAL_INPUTS | DIGITAL_OUTPUTS
WRITABLE_CHANNELS = ANALOG_OUTPUTS | DIGITAL_OUTPUTS

AO_VOLTAGE_LIMITS = (-5.0, 5.0)

# Live acquisition defaults. The dashboard is opt-in: an MCP server that
# silently opens a network port on startup would be a rude surprise.
DASHBOARD_ENABLED = os.getenv("DAQ_MCP_DASHBOARD") == "1"
DASHBOARD_HOST = os.getenv("DAQ_MCP_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DAQ_MCP_DASHBOARD_PORT", "8765"))
LIVE_CHANNEL = os.getenv("DAQ_MCP_LIVE_CHANNEL", "Dev1/ai0")
LIVE_RATE_HZ = float(os.getenv("DAQ_MCP_LIVE_RATE", "1000"))

_PREVIEW_MAX_POINTS = 50

live = LiveMonitor(get_backend)


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


def _dashboard_url() -> str | None:
    if not DASHBOARD_ENABLED:
        return None
    return f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/"


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
    return {
        "backend": type(get_backend()).__name__,
        "writes_enabled": WRITES_ENABLED,
        "digital_inputs": sorted(DIGITAL_INPUTS),
        "digital_outputs": sorted(DIGITAL_OUTPUTS),
        "analog_inputs": sorted(ANALOG_INPUTS),
        "ao_voltage_limits": list(AO_VOLTAGE_LIMITS),
    }


def _start_dashboard() -> bool:
    from daq_mcp.dashboard import create_app, port_is_free, serve_in_thread

    log = logging.getLogger("daq-mcp")
    if not port_is_free(DASHBOARD_HOST, DASHBOARD_PORT):
        log.error(
            "dashboard port %d is already in use (another server still running?); "
            "set DAQ_MCP_DASHBOARD_PORT to pick another",
            DASHBOARD_PORT,
        )
        return False

    app = create_app(
        snapshot=lambda: live.snapshot(max_points=400),
        set_digital=_dashboard_set_digital,
        config=_dashboard_config,
    )
    serve_in_thread(app, DASHBOARD_HOST, DASHBOARD_PORT)
    log.info("dashboard on http://%s:%d/", DASHBOARD_HOST, DASHBOARD_PORT)
    return True


if __name__ == "__main__":
    # stderr only — stdout carries the JSON-RPC stream.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log = logging.getLogger("daq-mcp")

    # Standalone dashboard: no MCP client, no stdio loop. Without this the
    # stdio transport reads EOF on an unattached stdin and exits immediately,
    # taking the dashboard with it.
    dashboard_only = "--dashboard-only" in sys.argv

    # NI-DAQmx must be probed on the main thread. FastMCP runs sync tools in a
    # worker thread; lazy backend init there can deadlock on Windows over stdio.
    backend = get_backend()
    log.info("backend=%s writes_enabled=%s", type(backend).__name__, WRITES_ENABLED)

    if DASHBOARD_ENABLED or dashboard_only:
        _start_dashboard()
        # Start streaming immediately so the dashboard has a trace on open,
        # rather than requiring the model to call start_live first.
        started = live.start(
            LIVE_CHANNEL, LIVE_RATE_HZ, poll_inputs=sorted(DIGITAL_INPUTS)
        )
        if "error" in started:
            log.warning("live acquisition did not start: %s", started["error"])

    if dashboard_only:
        log.info("dashboard-only mode; Ctrl+C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            live.stop()
    else:
        mcp.run()
