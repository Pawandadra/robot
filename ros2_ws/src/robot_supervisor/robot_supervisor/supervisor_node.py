"""
Subscribe to /face/faces_detected and /face/interaction_active; publish /giga/set_hold.

HOLD when: interaction is active OR (debounced face present and post-interaction grace elapsed).
RUN when: not the above (debounced no face, or inside grace after interaction ended).
"""

from __future__ import annotations

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32


class SupervisorNode(Node):
    def __init__(self) -> None:
        super().__init__("robot_supervisor", namespace="")

        self.declare_parameter("present_streak", 2)
        self.declare_parameter("absent_streak", 4)
        self.declare_parameter("post_interaction_grace_sec", 4.0)
        self.declare_parameter("control_hz", 10.0)

        self._present_streak_need = max(
            1, int(self.get_parameter("present_streak").get_parameter_value().integer_value)
        )
        self._absent_streak_need = max(
            1, int(self.get_parameter("absent_streak").get_parameter_value().integer_value)
        )
        self._grace_sec = max(
            0.0,
            float(
                self.get_parameter("post_interaction_grace_sec").get_parameter_value().double_value
            ),
        )
        hz = float(self.get_parameter("control_hz").get_parameter_value().double_value)
        if hz <= 0.0:
            hz = 10.0
        self._timer_period = 1.0 / hz

        self._faces_count = 0
        self._interaction_busy = False
        self._streak_present = 0
        self._streak_absent = 0
        self._debounced_present = False
        self._grace_until = 0.0
        self._last_busy = False
        self._last_hold: bool | None = None

        self.create_subscription(Int32, "/face/faces_detected", self._on_faces, 10)
        self.create_subscription(Bool, "/face/interaction_active", self._on_busy, 10)

        self._pub_hold = self.create_publisher(Bool, "/giga/set_hold", 10)

        self._timer = self.create_timer(self._timer_period, self._tick)

        self.get_logger().info(
            f"Supervisor: present_streak={self._present_streak_need} "
            f"absent_streak={self._absent_streak_need} grace={self._grace_sec}s -> /giga/set_hold"
        )

    def _on_faces(self, msg: Int32) -> None:
        self._faces_count = int(msg.data)
        if self._faces_count > 0:
            self._streak_present += 1
            self._streak_absent = 0
        else:
            self._streak_absent += 1
            self._streak_present = 0

        if self._streak_present >= self._present_streak_need:
            self._debounced_present = True
        if self._streak_absent >= self._absent_streak_need:
            self._debounced_present = False

    def _on_busy(self, msg: Bool) -> None:
        busy = bool(msg.data)
        if self._last_busy and not busy:
            self._grace_until = time.monotonic() + self._grace_sec
            self.get_logger().info(
                f"interaction ended -> grace {self._grace_sec}s before HOLD from presence only"
            )
        self._last_busy = busy
        self._interaction_busy = busy

    def _tick(self) -> None:
        now = time.monotonic()
        allow_hold_from_presence = now >= self._grace_until

        want_hold = self._interaction_busy or (
            allow_hold_from_presence and self._debounced_present
        )

        # Always publish each tick: default QoS is volatile; messages sent before the
        # giga_serial_bridge subscribes are dropped. Republishing is cheap and the
        # bridge skips duplicate HOLD/RUN when state already matches.
        m = Bool()
        m.data = want_hold
        self._pub_hold.publish(m)

        if self._last_hold is None or want_hold != self._last_hold:
            self._last_hold = want_hold
            self.get_logger().info(
                f"set_hold={want_hold} busy={self._interaction_busy} "
                f"debounced_present={self._debounced_present} faces={self._faces_count}"
            )


def main() -> None:
    rclpy.init()
    node = SupervisorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
