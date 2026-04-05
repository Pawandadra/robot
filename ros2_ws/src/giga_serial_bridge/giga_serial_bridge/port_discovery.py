"""
Find USB serial devices (Arduino Giga R1, mbed CDC, etc.) for the Giga bridge.

CLI: ros2 run giga_serial_bridge list_giga_ports [--best]
Code: resolve_port("auto") -> device path or None
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List, Optional, Tuple

try:
    import serial.tools.list_ports
except ImportError as e:
    raise SystemExit(
        "pyserial is required. On Ubuntu: sudo apt install python3-serial"
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


def list_ranked_ports() -> List[Tuple[int, str, str, str | None, int | None, int | None]]:
    """Return rows: (score, device, description, manufacturer, vid, pid), best first."""
    rows: List[Tuple[int, str, str, str | None, int | None, int | None]] = []
    for p in serial.tools.list_ports.comports():
        score = _score_port(p.description, p.manufacturer)
        rows.append(
            (score, p.device, p.description or "", p.manufacturer, p.vid, p.pid)
        )
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def resolve_port(port: str) -> Optional[str]:
    """
    If port is empty, 'auto', 'scan', or 'detect', pick the best matching USB serial device.
    Otherwise return the given path unchanged (e.g. /dev/ttyACM1).
    """
    key = port.strip().lower()
    if key and key not in ("auto", "scan", "detect", ""):
        return port.strip()

    ranked = list_ranked_ports()
    if not ranked:
        # Fallback: common Linux globs when pyserial sees nothing
        for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
            for path in sorted(glob.glob(pattern)):
                if os.path.exists(path):
                    return path
        return None

    for score, device, *_ in ranked:
        if score > 0:
            return device
    for _score, device, *_ in ranked:
        if device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB"):
            return device
    return ranked[0][1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List USB serial ports and highlight Arduino / Giga / mbed candidates."
    )
    parser.add_argument(
        "--best",
        action="store_true",
        help="Print only the auto-selected device path (exit 1 if none).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print VID/PID hex.",
    )
    args = parser.parse_args()

    rows = list_ranked_ports()
    if args.best:
        path = resolve_port("auto")
        if path:
            print(path)
            sys.exit(0)
        sys.exit(1)

    if not rows:
        print("No serial ports found by pyserial.", file=sys.stderr)
        print("Check USB cable and permissions (dialout group).", file=sys.stderr)
        sys.exit(1)

    print("Ranked USB serial devices (best guess for Giga first):\n")
    for i, (score, dev, desc, manuf, vid, pid) in enumerate(rows, 1):
        manuf_s = manuf or "?"
        extra = ""
        if args.verbose and vid is not None:
            pvv = pid if pid is not None else 0
            extra = f"  vid=0x{vid:04x} pid=0x{pvv:04x}"
        print(f"  {i}. {dev}")
        print(f"      score={score}  {desc}")
        print(f"      mfg={manuf_s}{extra}\n")

    best = resolve_port("auto")
    if best:
        print("Suggested for ROS / Giga:")
        print(f"  ros2 run giga_serial_bridge giga_serial_node --ros-args -p port:={best}")
        print("  # or leave default:  -p port:=auto")
    else:
        print("Could not auto-pick a port; set -p port:=/dev/ttyACM0 manually.", file=sys.stderr)
        sys.exit(1)

    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        print("Stable udev symlinks (often survive reboot / replug order):")
        try:
            for name in sorted(os.listdir(by_id)):
                full = os.path.join(by_id, name)
                if os.path.islink(full):
                    try:
                        tgt = os.path.realpath(full)
                        print(f"  {full} -> {tgt}")
                    except OSError:
                        print(f"  {full}")
        except OSError as e:
            print(f"  (could not list {by_id}: {e})", file=sys.stderr)


if __name__ == "__main__":
    main()
