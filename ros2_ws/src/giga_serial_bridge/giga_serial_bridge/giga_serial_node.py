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
        self.create_subscription(Bool, "set_hold", self._on_set_hold, 10)

        self._srv_hold = self.create_service(Trigger, "trigger_hold", self._srv_hold_cb)
        self._srv_run = self.create_service(Trigger, "trigger_run", self._srv_run_cb)
        self._srv_sync = self.create_service(Trigger, "sync_status", self._srv_sync_cb)

        self._sync_from_device()
        self._publish_hold_state()

        self.get_logger().info(
            f"Giga serial open {port} @ {baud}; topics: set_hold, hold_active; "
            "services: trigger_hold, trigger_run, sync_status"
        )

    def destroy_node(self) -> bool:
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
    ) -> str:
        """Drain serial until predicate(line) or timeout. Returns last matching line or ""."""
        end = time.monotonic() + overall_timeout_sec
        last_match = ""
        for _ in range(max_lines):
            if time.monotonic() > end:
                break
            line = self._read_line()
            if not line:
                continue
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
                self._drain_input()
                self._write_line("STATUS")
                reply = self._read_lines_until(
                    is_status,
                    overall_timeout_sec=self._sync_response_timeout,
                    max_lines=96,
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

            reply = self._read_lines_until(is_ack)
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
        ok, detail = self._send_hold(msg.data)
        if not ok:
            self.get_logger().warn(f"set_hold({msg.data}) incomplete: {detail}")

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
    rclpy.init()
    node = GigaSerialNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
