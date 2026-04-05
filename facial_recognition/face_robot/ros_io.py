"""
Optional ROS 2 publishers for the vision loop (robot supervisor + Giga bridge).

Enable with ENABLE_ROS=1 in .env or environment. Requires rclpy on PYTHONPATH
(see ros2_ws/README.md: source Jazzy, add /opt/ros/jazzy/lib/python3.x/site-packages).
"""

from __future__ import annotations

from face_robot import config

_node = None
_pub_faces: object | None = None
_pub_busy: object | None = None
_ros_inited = False


def enabled() -> bool:
    return bool(config.ENABLE_ROS)


def init() -> None:
    global _node, _pub_faces, _pub_busy, _ros_inited
    if not enabled() or _ros_inited:
        return
    try:
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import Bool, Int32
    except ImportError as e:
        print(f"⚠️ ENABLE_ROS=1 but rclpy import failed: {e}")
        print("  Source ROS 2 and extend PYTHONPATH to Jazzy site-packages (see ros2_ws/README.md).")
        return

    if not rclpy.ok():
        rclpy.init(args=None)
    _node = Node("face_reporter", namespace="/face")
    _pub_faces = _node.create_publisher(Int32, "faces_detected", 10)
    _pub_busy = _node.create_publisher(Bool, "interaction_active", 10)
    _ros_inited = True
    print("✅ ROS 2 publishers: /face/faces_detected, /face/interaction_active")


def shutdown() -> None:
    global _node, _pub_faces, _pub_busy, _ros_inited
    if not _ros_inited:
        return
    try:
        import rclpy

        if _node is not None:
            _node.destroy_node()
            _node = None
        _pub_faces = None
        _pub_busy = None
        if rclpy.ok():
            rclpy.shutdown()
    except Exception as e:
        print(f"⚠️ ros_io shutdown: {e}")
    _ros_inited = False


def spin_once() -> None:
    if not _ros_inited or _node is None:
        return
    import rclpy

    rclpy.spin_once(_node, timeout_sec=0.0)


def publish_face_count(count: int) -> None:
    if not _ros_inited or _pub_faces is None:
        return
    from std_msgs.msg import Int32

    m = Int32()
    m.data = int(count)
    _pub_faces.publish(m)


def publish_interaction_busy(busy: bool) -> None:
    if not _ros_inited or _pub_busy is None:
        return
    from std_msgs.msg import Bool

    m = Bool()
    m.data = bool(busy)
    _pub_busy.publish(m)
