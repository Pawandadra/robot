"""Direct USB serial to Arduino Giga (HOLD/RUN). No ROS."""

from __future__ import annotations

import glob
import os
import time
from typing import List, Optional, Tuple

try:
    import serial
    import serial.tools.list_ports
except ImportError as e:
    raise SystemExit(
        "pyserial required for Giga USB. pip install pyserial  (or apt install python3-serial)"
    ) from e


def _score_port(description: str, manufacturer: str | None) -> int:
    d = f"{description or ''} {manufacturer or ''}".lower()
    score = 0
    if "arduino" in d:
        score += 50
    if "giga" in d:
        score += 45
    if "mbed" in d or "arm mbed" in d:
        score += 25
    if "ch340" in d or "cp210" in d or "ft232" in d:
        score += 5
    if "ttyacm" in d or "acm" in d:
        score += 3
    return score


def _list_ranked_ports() -> List[Tuple[int, str]]:
    rows: List[Tuple[int, str]] = []
    for p in serial.tools.list_ports.comports():
        score = _score_port(p.description, p.manufacturer)
        rows.append((score, p.device))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def _best_device_from_serial_by_id() -> Optional[str]:
    """
    Giga R1 often creates two ttyACM devices; movement.ino listens on the main CDC (usually if00).
    Prefer a by-id symlink containing GIGA and 'if00' over 'if01'.
    """
    by_id = "/dev/serial/by-id"
    if not os.path.isdir(by_id):
        return None
    rows: List[Tuple[int, str, str]] = []
    for name in sorted(os.listdir(by_id)):
        full = os.path.join(by_id, name)
        if not os.path.islink(full):
            continue
        try:
            target = os.path.realpath(full)
        except OSError:
            continue
        if not (target.startswith("/dev/ttyACM") or target.startswith("/dev/ttyUSB")):
            continue
        nu = name.upper()
        if "GIGA" not in nu and "ARDUINO" not in nu and "MBED" not in nu:
            continue
        if "GIGA" in nu:
            prio = 0 if "IF00" in nu else (1 if "IF01" in nu else 2)
        elif "ARDUINO" in nu:
            prio = 2 if "IF00" in nu else 3
        else:
            prio = 2 if "IF00" in nu else 3
        rows.append((prio, name, target))
    if not rows:
        return None
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows[0][2]


def resolve_giga_port(port: str) -> Optional[str]:
    """auto/scan/empty -> best guess; else return path as-is."""
    key = port.strip().lower()
    if key and key not in ("auto", "scan", "detect", ""):
        return port.strip()

    by_id_dev = _best_device_from_serial_by_id()
    if by_id_dev:
        return by_id_dev

    ranked = _list_ranked_ports()
    if not ranked:
        for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
            for path in sorted(glob.glob(pattern)):
                if os.path.exists(path):
                    return path
        return None

    for score, device in ranked:
        if score > 0:
            return device
    for _score, device in ranked:
        if device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB"):
            return device
    return ranked[0][1]


class GigaSerial:
    """
    Send HOLD/RUN only when the desired state changes.
    Uses CRLF so the Giga line parser always sees a complete line (same as typing Enter).
    No blocking reads — works even if the sketch prints TICK spam.
    """

    def __init__(
        self,
        device: str,
        baud: int = 115200,
        boot_delay_sec: float = 2.0,
        *,
        debug: bool = False,
    ) -> None:
        self._debug = debug
        # dsrdtr/rtscts False: avoid extra control-line toggles on mbed CDC (can confuse the Giga).
        self._ser = serial.Serial(
            device,
            baud,
            timeout=0.2,
            write_timeout=2.0,
            dsrdtr=False,
            rtscts=False,
        )
        if boot_delay_sec > 0:
            time.sleep(boot_delay_sec)
        self._drain_rx()
        self._last_hold: bool | None = None

    def _drain_rx(self) -> None:
        old = self._ser.timeout
        self._ser.timeout = 0.05
        try:
            for _ in range(200):
                chunk = self._ser.read(512)
                if not chunk:
                    break
        finally:
            self._ser.timeout = old

    def set_hold(self, want_hold: bool, *, resume_with_90_turn: bool = False) -> None:
        if want_hold:
            if self._last_hold is True:
                return
            self._ser.write(b"HOLD\r\n")
            self._ser.flush()
            self._last_hold = True
            if self._debug:
                print(f"[Giga] HOLD -> {self._ser.port}")
            return
        # release (RUN or RUN90 — RUN90 pivots ~90° on firmware before cruise)
        if resume_with_90_turn:
            self._ser.write(b"RUN90\r\n")
            self._ser.flush()
            self._last_hold = False
            if self._debug:
                print(f"[Giga] RUN90 -> {self._ser.port}")
            return
        if self._last_hold is False:
            return
        self._ser.write(b"RUN\r\n")
        self._ser.flush()
        self._last_hold = False
        if self._debug:
            print(f"[Giga] RUN -> {self._ser.port}")

    def pulse_hold(self) -> None:
        """Send HOLD again (already in hold state). Recovers from missed USB lines."""
        if self._last_hold is not True:
            return
        self._ser.write(b"HOLD\r\n")
        self._ser.flush()
        if self._debug:
            print(f"[Giga] HOLD (pulse) -> {self._ser.port}")

    def close(self) -> None:
        if self._ser.is_open:
            self._ser.close()


def open_giga_optional(
    port_raw: str,
    baud: int,
    boot_delay_sec: float,
    *,
    debug: bool = False,
) -> Optional[GigaSerial]:
    path = resolve_giga_port(port_raw)
    if not path:
        print("⚠️ Giga: no serial port (set GIGA_SERIAL_PORT=/dev/ttyACM0 or /dev/serial/by-id/...)")
        return None
    try:
        g = GigaSerial(path, baud=baud, boot_delay_sec=boot_delay_sec, debug=debug)
        hint = ""
        if port_raw.strip().lower() in ("auto", "scan", "detect", ""):
            hint = " (auto: prefer /dev/serial/by-id/*GIGA*if00*; if still no stop, try ttyACM1)"
        print(f"✅ Giga USB: {path} @ {baud}{hint}")
        return g
    except serial.SerialException as e:
        print(f"⚠️ Giga serial not opened: {e}")
        return None
