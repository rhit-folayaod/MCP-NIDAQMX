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

"""Machine-local named wiring profiles.

NI-DAQmx lists every channel a device supports; it cannot tell you which pins
are LEDs vs buttons. You assert that. Profiles store those assertions under
names on disk (gitignored) so a demo bench, a lab bench, and a sim layout can
coexist and be switched from the dashboard or MCP tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
# Legacy single-file path (still loaded if no profiles exist yet).
_LEGACY_PATH = _ROOT / ".daq_mcp_wiring.json"
_PROFILES_DIR = _ROOT / ".daq_mcp_profiles"
_ACTIVE_NAME_FILE = _PROFILES_DIR / "_active"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


@dataclass
class Wiring:
    device: str = "Dev1"
    live_channel: str = "Dev1/ai0"
    live_rate_hz: float = 1000.0
    analog_inputs: list[str] = field(default_factory=lambda: ["Dev1/ai0", "Dev1/ai1"])
    analog_outputs: list[str] = field(default_factory=list)
    digital_inputs: list[str] = field(
        default_factory=lambda: ["Dev1/port0/line0", "Dev1/port0/line1"]
    )
    digital_outputs: list[str] = field(
        default_factory=lambda: ["Dev1/port0/line6", "Dev1/port0/line7"]
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Wiring:
        def _str_list(key: str, default: list[str]) -> list[str]:
            raw = data.get(key, default)
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list of channel names")
            out: list[str] = []
            for item in raw:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"{key} entries must be non-empty strings")
                out.append(item.strip())
            seen: set[str] = set()
            unique: list[str] = []
            for ch in out:
                if ch not in seen:
                    seen.add(ch)
                    unique.append(ch)
            return unique

        live = data.get("live_channel", "Dev1/ai0")
        if not isinstance(live, str) or not live.strip():
            raise ValueError("live_channel must be a non-empty string")
        rate = data.get("live_rate_hz", 1000.0)
        if not isinstance(rate, (int, float)) or float(rate) <= 0:
            raise ValueError("live_rate_hz must be a number > 0")
        device = data.get("device", "Dev1")
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device must be a non-empty string")

        return cls(
            device=device.strip(),
            live_channel=live.strip(),
            live_rate_hz=float(rate),
            analog_inputs=_str_list("analog_inputs", ["Dev1/ai0", "Dev1/ai1"]),
            analog_outputs=_str_list("analog_outputs", []),
            digital_inputs=_str_list(
                "digital_inputs", ["Dev1/port0/line0", "Dev1/port0/line1"]
            ),
            digital_outputs=_str_list(
                "digital_outputs", ["Dev1/port0/line6", "Dev1/port0/line7"]
            ),
        )


def default_wiring() -> Wiring:
    return Wiring()


def normalize_profile_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            "Profile name must be 1–64 chars, start with a letter or digit, "
            "and use only letters, digits, spaces, _ . -"
        )
    return cleaned


def _slug(name: str) -> str:
    """Filesystem-safe slug; display name is stored inside the JSON too."""
    return re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE)


def profiles_dir() -> Path:
    return _PROFILES_DIR


def profile_path(name: str) -> Path:
    return _PROFILES_DIR / f"{_slug(normalize_profile_name(name))}.json"


def ensure_profiles_dir() -> Path:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILES_DIR


def get_active_profile_name() -> str | None:
    if not _ACTIVE_NAME_FILE.is_file():
        return None
    try:
        name = _ACTIVE_NAME_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def set_active_profile_name(name: str | None) -> None:
    ensure_profiles_dir()
    if name is None:
        if _ACTIVE_NAME_FILE.is_file():
            _ACTIVE_NAME_FILE.unlink()
        return
    _ACTIVE_NAME_FILE.write_text(normalize_profile_name(name) + "\n", encoding="utf-8")


def list_profiles() -> list[dict[str, Any]]:
    """Named profiles on disk, active one marked."""
    ensure_profiles_dir()
    active = get_active_profile_name()
    out: list[dict[str, Any]] = []
    for path in sorted(_PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name") or path.stem
        if not isinstance(name, str):
            name = path.stem
        out.append(
            {
                "name": name,
                "path": str(path),
                "active": active == name,
                "device": data.get("device"),
                "live_channel": data.get("live_channel"),
            }
        )
    return out


def load_profile(name: str) -> Wiring:
    path = profile_path(name)
    if not path.is_file():
        raise ValueError(f"No profile named {name!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Profile {name!r} is not a JSON object")
    # name is metadata, not a Wiring field
    data.pop("name", None)
    return Wiring.from_dict(data)


def save_profile(name: str, wiring: Wiring, *, make_active: bool = True) -> Path:
    ensure_profiles_dir()
    clean = normalize_profile_name(name)
    path = profile_path(clean)
    payload = wiring.to_dict()
    payload["name"] = clean
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if make_active:
        set_active_profile_name(clean)
    return path


def delete_profile(name: str) -> dict[str, Any]:
    clean = normalize_profile_name(name)
    path = profile_path(clean)
    if not path.is_file():
        raise ValueError(f"No profile named {clean!r}")
    path.unlink()
    active = get_active_profile_name()
    was_active = active == clean
    if was_active:
        set_active_profile_name(None)
    return {"deleted": clean, "was_active": was_active}


def load_wiring(path: Path | None = None) -> Wiring:
    """Load active profile, else legacy file, else defaults."""
    if path is not None:
        return _read_wiring_file(path)

    active = get_active_profile_name()
    if active:
        try:
            return load_profile(active)
        except (ValueError, OSError, json.JSONDecodeError):
            pass

    if _LEGACY_PATH.is_file():
        return _read_wiring_file(_LEGACY_PATH)

    return default_wiring()


def save_wiring(wiring: Wiring, path: Path | None = None) -> Path:
    """Persist as the active named profile, or to an explicit path."""
    if path is not None:
        path.write_text(
            json.dumps(wiring.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    name = get_active_profile_name() or "default"
    return save_profile(name, wiring, make_active=True)


def _read_wiring_file(target: Path) -> Wiring:
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read wiring file {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Wiring file must contain a JSON object")
    data.pop("name", None)
    return Wiring.from_dict(data)


def validate_wiring(
    wiring: Wiring,
    inventory: dict[str, Any] | None = None,
) -> list[str]:
    """Return human-readable problems. Empty list means OK to apply."""
    errors: list[str] = []

    di = set(wiring.digital_inputs)
    do = set(wiring.digital_outputs)
    overlap = di & do
    if overlap:
        errors.append(
            "A digital line cannot be both input and output: "
            + ", ".join(sorted(overlap))
        )

    ai = set(wiring.analog_inputs)
    ao = set(wiring.analog_outputs)
    if ai & ao:
        errors.append(
            "A channel cannot be both analog input and output: "
            + ", ".join(sorted(ai & ao))
        )

    if not wiring.analog_inputs:
        errors.append("Select at least one analog input")
    if wiring.live_channel not in wiring.analog_inputs:
        errors.append(
            f"live_channel {wiring.live_channel!r} must be one of the selected "
            "analog inputs"
        )

    if inventory is None:
        return errors

    known_ai = set(inventory.get("analog_input_channels") or [])
    known_ao = set(inventory.get("analog_output_channels") or [])
    known_di = set(inventory.get("digital_input_channels") or [])
    known_do = set(inventory.get("digital_output_channels") or [])
    known_dio = known_di | known_do

    for ch in wiring.analog_inputs:
        if known_ai and ch not in known_ai:
            errors.append(f"{ch} is not an analog input on this device")
    for ch in wiring.analog_outputs:
        if known_ao and ch not in known_ao:
            errors.append(f"{ch} is not an analog output on this device")
    for ch in wiring.digital_inputs:
        if known_dio and ch not in known_dio:
            errors.append(f"{ch} is not a digital line on this device")
    for ch in wiring.digital_outputs:
        if known_dio and ch not in known_dio:
            errors.append(f"{ch} is not a digital line on this device")

    inv_name = inventory.get("name")
    # Merged / chassis inventories list pins from many modules; the profile's
    # device field is just a label in that case.
    if (
        inv_name
        and wiring.device
        and wiring.device != inv_name
        and inv_name not in {"all", "*", "merged"}
        and inventory.get("product_type") != "merged"
    ):
        errors.append(
            f"device {wiring.device!r} does not match inventory device {inv_name!r}"
        )

    return errors


def wiring_path() -> Path:
    """Path of the active profile file, or the legacy file if none active."""
    active = get_active_profile_name()
    if active:
        return profile_path(active)
    return _LEGACY_PATH
