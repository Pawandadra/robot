"""ROS 2 node: set_hold (Bool) -> USB HOLD/RUN; publishes hold_active (latched)."""

import re
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

try:
    import serial
except ImportError as e:
    raise SystemExit(
        "python3-serial is required. On Ubuntu: sudo apt install python3-serial"
    ) from e

from giga_serial_bridge.port_discovery import resolve_port


class GigaSerialNode(Node):
    def __init__(self) -> None:
        # Topics: /giga/set_hold, /giga/hold_active; services under /giga/*
        super().__init__("serial_bridge", namespace="/giga")

        self.declare_parameter("port", "auto")
        self.declare_parameter("baud_rate", 115200)
        self.declare_parameter("read_timeout_sec", 0.4)
        # After open, many boards reset; sketch + Serial.begin need time before STATUS works.
        self.declare_parameter("serial_boot_delay_sec", 2.0)
        self.declare_parameter("sync_attempts", 6)
        self.declare_parameter("sync_response_timeout_sec", 3.0)
        # Short per-line read during STATUS scan (full read_timeout is too slow × many lines).
        self.declare_parameter("sync_line_poll_sec", 0.05)
        # Run first STATUS sync after spin() starts (otherwise set_hold is ignored during long __init__).
        self.declare_parameter("startup_sync_delay_sec", 0.15)

        port_raw = self.get_parameter("port").get_parameter_value().string_value
        port = resolve_port(port_raw)
        if not port:
            self.get_logger().fatal(
                "No serial port found. Plug in the Giga, then run:\n"
                "  ros2 run giga_serial_bridge list_giga_ports\n"
                "Or set parameter port:=/dev/ttyACM0 (or ttyACM1, …)."
            )
            raise RuntimeError("giga_serial_bridge: no serial port")
        if port_raw.strip().lower() in ("auto", "scan", "detect", ""):
            self.get_logger().info(f"port auto-selected: {port}")
        baud = self.get_parameter("baud_rate").get_parameter_value().integer_value
        if baud <= 0:
            baud = 115200
        self._read_timeout = float(
            self.get_parameter("read_timeout_sec").get_parameter_value().double_value
        )
        if self._read_timeout <= 0.0:
            self._read_timeout = 0.4

        self._boot_delay = max(
            0.0,
            float(
                self.get_parameter("serial_boot_delay_sec").get_parameter_value().double_value
            ),
        )
        self._sync_attempts = max(
            1, int(self.get_parameter("sync_attempts").get_parameter_value().integer_value)
        )
        self._sync_response_timeout = max(
            0.5,
            float(
                self.get_parameter("sync_response_timeout_sec").get_parameter_value().double_value
            ),
        )
        self._sync_line_poll = max(
            0.01,
            float(
                self.get_parameter("sync_line_poll_sec").get_parameter_value().double_value
            ),
        )
        self._startup_sync_delay = max(
            0.0,
            float(
                self.get_parameter("startup_sync_delay_sec").get_parameter_value().double_value
            ),
        )

        try:
            self._ser = serial.Serial(port, baud, timeout=self._read_timeout)
        except serial.SerialException as e:
            self.get_logger().fatal(
                f"Could not open {port}: {e}. "
                "Run: ros2 run giga_serial_bridge list_giga_ports"
            )
            raise
        self._lock = threading.Lock()
        self._hold_active = False
        self._startup_thread: threading.Thread | None = None
        # Latest command from /giga/set_hold; worker applies so the executor never blocks on serial.
        self._want_hold_lock = threading.Lock()
        self._want_hold: bool | None = None
        self._hold_worker_stop = threading.Event()
        self._hold_worker_thread = threading.Thread(
            target=self._hold_worker_loop, daemon=True, name="giga-hold-serial"
        )

        # Reduce chance of USB-serial auto-reset glitches; then wait for sketch boot.
        try:
            self._ser.setDTR(False)
        except (AttributeError, OSError, serial.SerialException):
            pass
        if self._boot_delay > 0.0:
            self.get_logger().info(
                f"Waiting {self._boot_delay:.1f}s for Giga sketch after USB open (serial_boot_delay_sec)"
            )
            time.sleep(self._boot_delay)
        self._drain_input()

        qos_latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._pub_hold = self.create_publisher(Bool, "hold_active", qos_latched)
        qos_cmd = QoSProfile(depth=50)
        self.create_subscription(Bool, "set_hold", self._on_set_hold, qos_cmd)

        self._srv_hold = self.create_service(Trigger, "trigger_hold", self._srv_hold_cb)
        self._srv_run = self.create_service(Trigger, "trigger_run", self._srv_run_cb)
        self._srv_sync = self.create_service(Trigger, "sync_status", self._srv_sync_cb)

        self._publish_hold_state()

        self.get_logger().info(
            f"Giga serial open {port} @ {baud}. Subscriptions active; "
            f"startup STATUS sync in {self._startup_sync_delay:.2f}s after spin."
        )
        self._startup_timer = self.create_timer(
            self._startup_sync_delay, self._startup_sync_callback
        )
        self._hold_worker_thread.start()

    def destroy_node(self) -> bool:
        self._hold_worker_stop.set()
        if self._hold_worker_thread.is_alive():
            self._hold_worker_thread.join(timeout=3.0)
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
        return super().destroy_node()

    def _publish_hold_state(self) -> None:
        m = Bool()
        m.data = self._hold_active
        self._pub_hold.publish(m)

    def _write_line(self, line: str) -> None:
        with self._lock:
            self._ser.write(f"{line}\n".encode("ascii"))
            self._ser.flush()

    def _read_line(self) -> str:
        with self._lock:
            raw = self._ser.readline()
        return raw.decode("utf-8", errors="replace").strip()

    def _read_line_with_timeout(self, timeout_sec: float) -> str:
        """Single readline with a bounded wait (seconds)."""
        t = max(0.001, float(timeout_sec))
        with self._lock:
            prev = self._ser.timeout
            self._ser.timeout = t
            try:
                raw = self._ser.readline()
            finally:
                self._ser.timeout = prev
        return raw.decode("utf-8", errors="replace").strip()

    def _drain_input(self) -> None:
        """Drop buffered RX (boot banner, TICK lines) so STATUS ack is readable."""
        with self._lock:
            old_timeout = self._ser.timeout
            self._ser.timeout = 0.05
            try:
                for _ in range(200):
                    chunk = self._ser.read(512)
                    if not chunk:
                        break
            finally:
                self._ser.timeout = old_timeout

    def _read_lines_until(
        self,
        predicate,
        overall_timeout_sec: float = 1.25,
        max_lines: int = 48,
        line_poll_sec: float | None = None,
    ) -> str:
        """
        Read lines until predicate matches or wall time / line budget exceeded.
        line_poll_sec: max wait per readline (default: self._read_timeout).
        """
        poll = (
            float(line_poll_sec)
            if line_poll_sec is not None
            else float(self._read_timeout)
        )
        poll = max(0.01, poll)
        end = time.monotonic() + max(0.05, float(overall_timeout_sec))
        last_match = ""
        for _ in range(max(1, int(max_lines))):
            now = time.monotonic()
            if now >= end:
                break
            # Do not block longer than remaining wall time
            line = self._read_line_with_timeout(min(poll, end - now))
            if predicate(line):
                last_match = line
                break
        return last_match

    def _sync_from_device(self) -> None:
        """Send STATUS and parse EXT_HOLD 0|1; retry (USB CDC / boot banner)."""

        def is_status(line: str) -> bool:
            return bool(re.match(r"EXT_HOLD\s+([01])", line))

        last_reply = ""
        try:
            for attempt in range(self._sync_attempts):
                self.get_logger().info(
                    f"sync attempt {attempt + 1}/{self._sync_attempts}: STATUS -> Giga"
                )
                self._drain_input()
                self._write_line("STATUS")
                reply = self._read_lines_until(
                    is_status,
                    overall_timeout_sec=self._sync_response_timeout,
                    max_lines=120,
                    line_poll_sec=self._sync_line_poll,
                )
                last_reply = reply
                m = re.match(r"EXT_HOLD\s+([01])", reply)
                if m:
                    self._hold_active = m.group(1) == "1"
                    if attempt == 0:
                        self.get_logger().info(
                            f"sync STATUS -> hold_active={self._hold_active}"
                        )
                    else:
                        self.get_logger().info(
                            f"sync STATUS ok on attempt {attempt + 1} -> "
                            f"hold_active={self._hold_active}"
                        )
                    return
                time.sleep(0.12)

            self.get_logger().warn(
                f"sync: no EXT_HOLD after {self._sync_attempts} attempts "
                f"(last={last_reply!r}). Robot may still run; use "
                "`ros2 service call /giga/sync_status std_srvs/srv/Trigger` "
                "or raise serial_boot_delay_sec / sync_response_timeout_sec."
            )
        except Exception as e:
            self.get_logger().error(f"sync failed: {e}")

    def _startup_sync_callback(self) -> None:
        try:
            self._startup_timer.cancel()
        except Exception:
            pass
        self._startup_thread = threading.Thread(
            target=self._startup_sync_thread_main,
            daemon=True,
            name="giga-startup-sync",
        )
        self._startup_thread.start()

    def _startup_sync_thread_main(self) -> None:
        """STATUS sync can take many seconds; must not block the executor."""
        self.get_logger().info("Running startup STATUS sync (serial)…")
        try:
            self._sync_from_device()
            self._publish_hold_state()
        except Exception as e:
            self.get_logger().error(f"startup sync thread: {e}")
        self.get_logger().info(
            "Giga bridge ready: /giga/set_hold, /giga/hold_active, "
            "trigger_hold | trigger_run | sync_status"
        )

    def _hold_worker_loop(self) -> None:
        while not self._hold_worker_stop.is_set():
            with self._want_hold_lock:
                want = self._want_hold
            if want is None:
                time.sleep(0.02)
                continue
            ok = False
            try:
                ok, detail = self._send_hold(want)
                if not ok:
                    self.get_logger().warn(f"set_hold({want}) incomplete: {detail}")
            except Exception as e:
                self.get_logger().error(f"hold worker: {e}")
            if ok:
                with self._want_hold_lock:
                    if self._want_hold == want:
                        self._want_hold = None
            else:
                time.sleep(0.15)

    def _send_hold(self, want_hold: bool) -> tuple[bool, str]:
        if want_hold == self._hold_active:
            return True, "already"
        cmd = "HOLD" if want_hold else "RUN"
        try:
            self._write_line(cmd)

            def is_ack(line: str) -> bool:
                u = line.upper()
                if want_hold:
                    return u.startswith("OK HOLD") or u.startswith("ERR")
                return u.startswith("OK RUN") or u.startswith("ERR")

            reply = self._read_lines_until(
                is_ack,
                overall_timeout_sec=2.5,
                max_lines=80,
                line_poll_sec=max(self._sync_line_poll, 0.08),
            )
            u = reply.upper()
            if want_hold and u.startswith("OK HOLD"):
                self._hold_active = True
                self._publish_hold_state()
                return True, reply
            if not want_hold and u.startswith("OK RUN"):
                self._hold_active = False
                self._publish_hold_state()
                return True, reply
            if u.startswith("ERR"):
                return False, reply
            self.get_logger().warn(f"{cmd} unexpected ack: {reply!r}; re-syncing")
            self._sync_from_device()
            self._publish_hold_state()
            return False, reply or "no ack"
        except Exception as e:
            self.get_logger().error(f"{cmd} failed: {e}")
            return False, str(e)

    def _on_set_hold(self, msg: Bool) -> None:
        with self._want_hold_lock:
            self._want_hold = bool(msg.data)

    def _srv_hold_cb(self, _req: Trigger.Request, resp: Trigger.Response) -> Trigger.Response:
        ok, detail = self._send_hold(True)
        resp.success = ok
        resp.message = detail
        return resp

    def _srv_run_cb(self, _req: Trigger.Request, resp: Trigger.Response) -> Trigger.Response:
        ok, detail = self._send_hold(False)
        resp.success = ok
        resp.message = detail
        return resp

    def _srv_sync_cb(self, _req: Trigger.Request, resp: Trigger.Response) -> Trigger.Response:
        self._sync_from_device()
        self._publish_hold_state()
        resp.success = True
        resp.message = f"hold_active={self._hold_active}"
        return resp


def main() -> None:
    from rclpy.executors import MultiThreadedExecutor

    rclpy.init()
    node = GigaSerialNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.remove_node(node)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
