"""Tests against the simulated backend (no NI drivers required)."""

from __future__ import annotations

import os
import time

import pytest

# Force simulation before importing server / selecting a backend.
os.environ["DAQ_MCP_SIMULATE"] = "1"
os.environ.pop("DAQ_MCP_ALLOW_WRITE", None)

from daq_mcp.backend import get_backend, reset_backend

import server


@pytest.fixture(autouse=True)
def _fresh_backend(monkeypatch):
    """Isolated backend + default safety knobs for every test."""
    monkeypatch.setenv("DAQ_MCP_SIMULATE", "1")
    monkeypatch.delenv("DAQ_MCP_ALLOW_WRITE", raising=False)
    monkeypatch.setattr(server, "WRITES_ENABLED", False)
    # line0/line1 stand in for inputs (buttons), ao0/line6 for outputs.
    monkeypatch.setattr(server, "DIGITAL_INPUTS", {"Dev1/port0/line0", "Dev1/port0/line1"})
    monkeypatch.setattr(server, "DIGITAL_OUTPUTS", {"Dev1/port0/line6"})
    monkeypatch.setattr(server, "ANALOG_OUTPUTS", {"Dev1/ao0"})
    monkeypatch.setattr(
        server,
        "CHANNEL_ALLOWLIST",
        {
            "Dev1/ai0",
            "Dev1/ai1",
            "Dev1/ao0",
            "Dev1/port0/line0",
            "Dev1/port0/line1",
            "Dev1/port0/line6",
        },
    )
    monkeypatch.setattr(server, "WRITABLE_CHANNELS", {"Dev1/ao0", "Dev1/port0/line6"})
    monkeypatch.setattr(server, "AO_VOLTAGE_LIMITS", (-5.0, 5.0))
    reset_backend()
    yield
    if server.live.is_streaming():
        server.live.stop()
    reset_backend()


def test_list_devices_well_formed():
    devices = server.list_devices()
    assert isinstance(devices, list)
    assert len(devices) >= 2
    for dev in devices:
        assert "name" in dev
        assert "product_type" in dev
        assert "serial_number" in dev
        assert "supports" in dev
        for key in ("analog_input", "analog_output", "digital_input", "digital_output"):
            assert key in dev["supports"]


def test_describe_device_well_formed():
    info = server.describe_device("Dev1")
    assert info["name"] == "Dev1"
    assert isinstance(info["analog_input_channels"], list)
    assert "Dev1/ai0" in info["analog_input_channels"]
    assert "max_ai_rate_hz" in info


def test_read_analog_well_formed():
    result = server.read_analog("Dev1/ai0", samples=10, rate_hz=1000.0)
    assert result["channel"] == "Dev1/ai0"
    assert len(result["samples"]) == 10
    assert "min" in result and "mean" in result and "max" in result


def test_read_digital_well_formed():
    result = server.read_digital("Dev1/port0/line0")
    assert result == {"channel": "Dev1/port0/line0", "value": False} or (
        result["channel"] == "Dev1/port0/line0" and isinstance(result["value"], bool)
    )


def test_self_test_well_formed():
    result = server.self_test("Dev1")
    assert result["device"] == "Dev1"
    assert result["passed"] is True


def test_current_config_resource():
    text = server.current_config()
    assert "writes_enabled:" in text
    assert "ao_voltage_limits:" in text
    assert "Dev1/ai0" in text


def test_allowlist_rejects_unknown_channel():
    with pytest.raises(server.ChannelNotAllowed):
        server.read_analog("Dev1/ai99")
    with pytest.raises(server.ChannelNotAllowed):
        server.read_digital("Dev1/port0/line7")
    with pytest.raises(server.ChannelNotAllowed):
        server.monitor_analog("Dev9/ai0", duration_s=0.01)


def test_writes_refused_when_disabled():
    dig = server.write_digital("Dev1/port0/line0", True)
    assert "error" in dig
    assert "DAQ_MCP_ALLOW_WRITE" in dig["error"]

    ao = server.write_analog("Dev1/ao0", 1.0)
    assert "error" in ao
    assert "DAQ_MCP_ALLOW_WRITE" in ao["error"]


def test_analog_output_clamping_both_ends(monkeypatch):
    monkeypatch.setattr(server, "WRITES_ENABLED", True)

    high = server.write_analog("Dev1/ao0", 12.0)
    assert high["requested_voltage"] == 12.0
    assert high["applied_voltage"] == 5.0
    assert high["clamped"] is True

    low = server.write_analog("Dev1/ao0", -12.0)
    assert low["requested_voltage"] == -12.0
    assert low["applied_voltage"] == -5.0
    assert low["clamped"] is True

    mid = server.write_analog("Dev1/ao0", 2.5)
    assert mid["requested_voltage"] == 2.5
    assert mid["applied_voltage"] == 2.5
    assert mid["clamped"] is False


def test_monitor_analog_preview_capped():
    # 2s @ 1 kHz => 2000 samples; preview must stay ≤ 50.
    result = server.monitor_analog("Dev1/ai0", duration_s=2.0, rate_hz=1000.0)
    assert result["sample_count"] == 2000
    assert "samples" not in result
    assert len(result["preview"]) <= 50
    for key in ("mean", "rms", "peak_to_peak", "std_dev"):
        assert key in result


def test_monitor_analog_short_preview_not_padded():
    result = server.monitor_analog("Dev1/ai1", duration_s=0.01, rate_hz=1000.0)
    assert result["sample_count"] == 10
    assert len(result["preview"]) == 10


def test_digital_write_read_round_trip(monkeypatch):
    monkeypatch.setattr(server, "WRITES_ENABLED", True)

    written = server.write_digital("Dev1/port0/line6", True)
    assert written["value"] is True

    read_back = server.read_digital("Dev1/port0/line6")
    assert read_back["value"] is True

    server.write_digital("Dev1/port0/line6", False)
    assert server.read_digital("Dev1/port0/line6")["value"] is False


def test_input_channels_cannot_be_written(monkeypatch):
    """Buttons are readable but must never be driven: that is a short circuit."""
    monkeypatch.setattr(server, "WRITES_ENABLED", True)

    with pytest.raises(server.ChannelNotWritable):
        server.write_digital("Dev1/port0/line0", True)

    with pytest.raises(server.ChannelNotWritable):
        server.write_analog("Dev1/ai0", 1.0)

    # Reading the same line is still fine.
    assert server.read_digital("Dev1/port0/line0")["value"] in (True, False)


def test_dashboard_write_uses_the_same_gate(monkeypatch):
    """The browser must not get a laxer path to the pins than the model."""
    refused = server._dashboard_set_digital("Dev1/port0/line6", True)
    assert "error" in refused
    assert "DAQ_MCP_ALLOW_WRITE" in refused["error"]

    monkeypatch.setattr(server, "WRITES_ENABLED", True)

    blocked = server._dashboard_set_digital("Dev1/port0/line0", True)
    assert "error" in blocked
    assert "cannot be written" in blocked["error"]

    unlisted = server._dashboard_set_digital("Dev1/port0/line9", True)
    assert "error" in unlisted
    assert "allowlist" in unlisted["error"]

    ok = server._dashboard_set_digital("Dev1/port0/line6", True)
    assert ok["value"] is True


def test_ai0_and_ai1_signals_differ():
    """Simulated waveforms should be distinguishable for the model."""
    ai0 = server.read_analog("Dev1/ai0", samples=200, rate_hz=1000.0)
    ai1 = server.read_analog("Dev1/ai1", samples=200, rate_hz=1000.0)
    # ai1 is noisy DC around 1.65 V; ai0 is a bipolar sine — means should differ.
    assert abs(ai0["mean"] - ai1["mean"]) > 0.2


def test_uses_simulated_backend():
    assert type(get_backend()).__name__ == "SimulatedBackend"


# --------------------------------------------------------------------------
# Live acquisition
# --------------------------------------------------------------------------


def _wait_for_samples(min_count: int = 20, timeout_s: float = 3.0) -> dict:
    """Poll until the rolling window fills; streaming is inherently async."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snap = server.live_status()
        if snap["window_samples"] >= min_count:
            return snap
        time.sleep(0.05)
    return server.live_status()


def test_start_live_streams_and_stops():
    started = server.start_live("Dev1/ai0", rate_hz=1000.0)
    assert started["streaming"] is True
    assert server.live.is_streaming("Dev1/ai0")

    snap = _wait_for_samples()
    assert snap["window_samples"] > 0
    assert snap["latest"] is not None
    assert len(snap["trace"]) <= 50

    server.stop_live()
    assert not server.live.is_streaming()


def test_only_one_stream_at_a_time():
    server.start_live("Dev1/ai0", rate_hz=1000.0)
    second = server.start_live("Dev1/ai1", rate_hz=1000.0)
    assert "error" in second
    server.stop_live()


def test_start_live_respects_allowlist():
    with pytest.raises(server.ChannelNotAllowed):
        server.start_live("Dev1/ai42", rate_hz=1000.0)


def test_reads_come_from_buffer_while_streaming():
    """A streaming channel is reserved, so reads must not open a second task."""
    server.start_live("Dev1/ai0", rate_hz=1000.0)
    _wait_for_samples(min_count=50)

    result = server.read_analog("Dev1/ai0", samples=10)
    assert result["source"] == "live_buffer"
    assert len(result["samples"]) <= 10

    windowed = server.monitor_analog("Dev1/ai0", duration_s=0.05, rate_hz=1000.0)
    assert windowed["sample_count"] > 0
    assert len(windowed["preview"]) <= 50

    server.stop_live()

    # Once stopped, one-shot acquisition takes over again.
    after = server.read_analog("Dev1/ai0", samples=5)
    assert "source" not in after
    assert len(after["samples"]) == 5


def test_dashboard_config_reports_directions():
    cfg = server._dashboard_config()
    assert cfg["digital_outputs"] == ["Dev1/port0/line6"]
    assert "Dev1/port0/line0" in cfg["digital_inputs"]
    assert cfg["writes_enabled"] is False


def test_inventory_lists_simulated_channels():
    pack = server._dashboard_inventory("Dev1")
    assert pack["selected"] == "Dev1"
    inv = pack["inventory"]
    assert "Dev1/ai0" in inv["analog_input_channels"]
    assert any("port0/line" in ch for ch in inv["digital_input_channels"])


def test_wiring_picker_updates_allowlist(tmp_path, monkeypatch):
    """Dashboard save must change what the agent may touch, not just the UI."""
    monkeypatch.setattr(server, "WRITES_ENABLED", True)
    path = tmp_path / "wiring.json"
    monkeypatch.setattr("daq_mcp.wiring._DEFAULT_PATH", path)
    monkeypatch.setattr(server, "wiring_path", lambda: path)

    body = {
        "device": "Dev1",
        "live_channel": "Dev1/ai1",
        "live_rate_hz": 500.0,
        "analog_inputs": ["Dev1/ai1"],
        "analog_outputs": [],
        "digital_inputs": ["Dev1/port0/line1"],
        "digital_outputs": ["Dev1/port0/line0"],
    }
    result = server._dashboard_set_wiring(body)
    assert "error" not in result, result
    assert path.is_file()
    assert server.DIGITAL_OUTPUTS == {"Dev1/port0/line0"}
    assert server.DIGITAL_INPUTS == {"Dev1/port0/line1"}
    assert "Dev1/ai0" not in server.CHANNEL_ALLOWLIST
    assert "Dev1/ai1" in server.CHANNEL_ALLOWLIST

    # Old LED line is no longer allowlisted; new output is.
    with pytest.raises(server.ChannelNotAllowed):
        server.write_digital("Dev1/port0/line6", True)
    ok = server.write_digital("Dev1/port0/line0", True)
    assert ok["value"] is True

    # Overlap of DI and DO is refused.
    bad = dict(body)
    bad["digital_inputs"] = ["Dev1/port0/line0"]
    bad["digital_outputs"] = ["Dev1/port0/line0"]
    refused = server._dashboard_set_wiring(bad)
    assert "error" in refused


def test_validate_wiring_rejects_unknown_channel():
    from daq_mcp.wiring import Wiring, validate_wiring

    wiring = Wiring(
        analog_inputs=["Dev1/ai0"],
        live_channel="Dev1/ai0",
        digital_outputs=["Dev1/port0/line99"],
    )
    inv = {
        "name": "Dev1",
        "analog_input_channels": ["Dev1/ai0"],
        "analog_output_channels": [],
        "digital_input_channels": ["Dev1/port0/line0"],
        "digital_output_channels": ["Dev1/port0/line0"],
    }
    problems = validate_wiring(wiring, inv)
    assert any("line99" in p for p in problems)


def test_get_and_set_wiring_tools(tmp_path, monkeypatch):
    path = tmp_path / "wiring.json"
    monkeypatch.setattr("daq_mcp.wiring._DEFAULT_PATH", path)

    current = server.get_wiring()
    assert "wiring" in current
    assert "Dev1/ai0" in current["allowlist"]

    updated = server.set_wiring(
        device="Dev1",
        live_channel="Dev1/ai0",
        analog_inputs=["Dev1/ai0"],
        digital_inputs=["Dev1/port0/line0"],
        digital_outputs=["Dev1/port0/line7"],
        live_rate_hz=800.0,
    )
    assert "error" not in updated, updated
    assert server.DIGITAL_OUTPUTS == {"Dev1/port0/line7"}
    assert path.is_file()


def test_auth_token_helpers():
    from daq_mcp.auth import is_loopback_host, tokens_match

    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")
    assert tokens_match("secret", "secret")
    assert tokens_match("Bearer secret", "secret")
    assert not tokens_match("nope", "secret")


def test_non_loopback_requires_token(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setattr(server, "AUTH_TOKEN", None)
    assert server._dashboard_port_available() is False

    monkeypatch.setattr(server, "AUTH_TOKEN", "test-secret")
    # Port may or may not be free; we only care that the token gate passed.
    # If something is listening, still False — either way no crash.
    server._dashboard_port_available()
