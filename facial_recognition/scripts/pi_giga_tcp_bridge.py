#!/usr/bin/env python3
"""
Run on the Raspberry Pi: expose the Giga USB serial on TCP so the laptop can use
GIGA_SERIAL_PORT=socket://raspberrypi.local:7000

Requires on the Pi: pip install pyserial (or apt install python3-serial)

One client at a time (only one main.py should connect).
"""

from __future__ import annotations

import argparse
import socket
import threading
import time

import serial


def _pump_net_to_serial(conn: socket.socket, ser: serial.Serial, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            data = conn.recv(4096)
            if not data:
                break
            ser.write(data)
            ser.flush()
    except OSError:
        pass
    finally:
        stop.set()


def _pump_serial_to_net(conn: socket.socket, ser: serial.Serial, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            n = ser.in_waiting
            if n > 0:
                chunk = ser.read(n)
                if chunk:
                    conn.sendall(chunk)
            else:
                time.sleep(0.005)
    except OSError:
        pass
    finally:
        stop.set()


def _handle_client(conn: socket.socket, ser: serial.Serial) -> None:
    stop = threading.Event()
    t_in = threading.Thread(
        target=_pump_net_to_serial, args=(conn, ser, stop), daemon=True
    )
    t_out = threading.Thread(
        target=_pump_serial_to_net, args=(conn, ser, stop), daemon=True
    )
    t_in.start()
    t_out.start()
    while t_in.is_alive() and t_out.is_alive():
        time.sleep(0.05)
    stop.set()
    t_in.join(timeout=1.0)
    t_out.join(timeout=1.0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge Arduino Giga serial (USB) to TCP for a remote laptop."
    )
    parser.add_argument("--device", default="/dev/ttyACM0", help="Serial device on the Pi")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--listen", default="0.0.0.0", help="Listen address (0.0.0.0 = all interfaces)"
    )
    parser.add_argument("--port", type=int, default=7000, help="TCP port")
    args = parser.parse_args()

    try:
        ser = serial.Serial(
            args.device,
            args.baud,
            timeout=0,
            write_timeout=2,
            dsrdtr=False,
            rtscts=False,
        )
    except serial.SerialException as e:
        print(f"Cannot open {args.device}: {e}")
        raise SystemExit(1) from e

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.listen, args.port))
    srv.listen(1)
    print(
        f"Giga bridge: {args.device} @ {args.baud} baud | TCP {args.listen}:{args.port}\n"
        f"Laptop .env: GIGA_SERIAL_PORT=socket://<this-pi-ip>:{args.port}\n"
        f"             GIGA_BOOT_DELAY_SEC=0"
    )
    try:
        while True:
            conn, addr = srv.accept()
            print(f"Client connected: {addr}")
            try:
                _handle_client(conn, ser)
            finally:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
            print("Client disconnected; waiting for next…")
    finally:
        srv.close()
        ser.close()


if __name__ == "__main__":
    main()
