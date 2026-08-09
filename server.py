"""
daq-mcp — an MCP server that lets an AI coding agent talk to NI DAQ hardware.

Transport: stdio (what Cursor / Claude Desktop / Claude Code launch).
Backend:   real nidaqmx if a device is present, otherwise a simulated one,
           so the server is runnable by anyone who clones the repo.

Run:  uv run server.py
Env:  DAQ_MCP_SIMULATE=1     force the simulated backend
      DAQ_MCP_ALLOW_WRITE=1  enable digital/analog output (off by default)
"""

import os
from typing import Literal

from fastmcp import FastMCP

mcp = FastMCP("daq-mcp")

# --------------------------------------------------------------------------
# Safety configuration
#
# This is the interesting part of the project, not the protocol plumbing.
# An LLM driving physical outputs is a real footgun: a hallucinated channel
# name or a stray voltage can damage hardware or hurt someone. The rules:
#   1. Writes are OFF unless explicitly enabled by env var.
#   2. Only channels on the allowlist can be touched at all.
#   3. Analog output is clamped to a configured safe range.
#   4. Every write returns what it actually did, so the model can verify.
# --------------------------------------------------------------------------

WRITES_ENABLED = os.getenv("DAQ_MCP_ALLOW_WRITE") == "1"
CHANNEL_ALLOWLIST = {"Dev1/ai0", "Dev1/ai1", "Dev1/port0/line0", "Dev1/port0/line1"}
AO_VOLTAGE_LIMITS = (-5.0, 5.0)


class ChannelNotAllowed(Exception):
    pass


def _check(channel: str) -> None:
    if channel not in CHANNEL_ALLOWLIST:
        raise ChannelNotAllowed(
            f"{channel!r} is not on the allowlist. Allowed: {sorted(CHANNEL_ALLOWLIST)}"
        )


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
    ...


@mcp.tool
def describe_device(device: str) -> dict:
    """Describe one device's full channel inventory.

    Returns available analog input, analog output, digital input, and digital
    output channels, plus voltage ranges and max sample rates. Use this to
    pick valid channel names before reading or writing.
    """
    ...


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
    ...


@mcp.tool
def read_digital(channel: str) -> dict:
    """Read the current logic level of a digital input line.

    channel: e.g. "Dev1/port0/line0"
    Returns {"channel": ..., "value": true/false}.
    """
    _check(channel)
    ...


@mcp.tool
def write_digital(channel: str, value: bool) -> dict:
    """Set a digital output line high or low.

    Requires DAQ_MCP_ALLOW_WRITE=1. Returns the line's state after the write
    (read back from hardware, not echoed) so the model can confirm the effect.
    """
    if not WRITES_ENABLED:
        return {"error": "Writes disabled. Set DAQ_MCP_ALLOW_WRITE=1 to enable."}
    _check(channel)
    ...


@mcp.tool
def write_analog(channel: str, voltage: float) -> dict:
    """Set an analog output channel to a DC voltage.

    Clamped to AO_VOLTAGE_LIMITS. Returns both the requested and the actual
    applied voltage, and flags whether clamping occurred — never silently
    changes the value the model asked for.
    """
    if not WRITES_ENABLED:
        return {"error": "Writes disabled. Set DAQ_MCP_ALLOW_WRITE=1 to enable."}
    _check(channel)
    lo, hi = AO_VOLTAGE_LIMITS
    applied = max(lo, min(hi, voltage))
    ...


@mcp.tool
def monitor_analog(channel: str, duration_s: float, rate_hz: float = 1000.0) -> dict:
    """Acquire a finite analog waveform and return summary statistics.

    Built for the "is my sensor behaving?" question: returns mean, RMS, peak-
    to-peak, standard deviation, and a downsampled preview rather than the
    full sample array. Keeps large acquisitions out of the context window.
    """
    _check(channel)
    ...


@mcp.tool
def self_test(device: str) -> dict:
    """Run the device's built-in self-test and report pass/fail.

    Useful as a first diagnostic when reads return unexpected values.
    """
    ...


# --------------------------------------------------------------------------
# Resources — read-only context the model can pull in without a tool call.
# --------------------------------------------------------------------------


@mcp.resource("daq://config")
def current_config() -> str:
    """The active safety configuration: allowlist, write status, voltage limits."""
    ...


if __name__ == "__main__":
    mcp.run()
