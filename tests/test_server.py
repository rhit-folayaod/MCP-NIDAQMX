"""Tests against the simulated backend (no NI drivers required)."""

from __future__ import annotations

import os

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
    monkeypatch.setattr(
        server,
        "CHANNEL_ALLOWLIST",
        {"Dev1/ai0", "Dev1/ai1", "Dev1/ao0", "Dev1/port0/line0", "Dev1/port0/line1"},
    )
    monkeypatch.setattr(server, "AO_VOLTAGE_LIMITS", (-5.0, 5.0))
    reset_backend()
    yield
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

    written = server.write_digital("Dev1/port0/line0", True)
    assert written["value"] is True

    read_back = server.read_digital("Dev1/port0/line0")
    assert read_back["value"] is True

    server.write_digital("Dev1/port0/line0", False)
    assert server.read_digital("Dev1/port0/line0")["value"] is False


def test_ai0_and_ai1_signals_differ():
    """Simulated waveforms should be distinguishable for the model."""
    ai0 = server.read_analog("Dev1/ai0", samples=200, rate_hz=1000.0)
    ai1 = server.read_analog("Dev1/ai1", samples=200, rate_hz=1000.0)
    # ai1 is noisy DC around 1.65 V; ai0 is a bipolar sine — means should differ.
    assert abs(ai0["mean"] - ai1["mean"]) > 0.2


def test_uses_simulated_backend():
    assert type(get_backend()).__name__ == "SimulatedBackend"
