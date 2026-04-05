# ROS 2 workspace (Jazzy)

Packages:

- **giga_serial_bridge** — USB serial to the Giga (`HOLD` / `RUN` / `STATUS`).
- **robot_supervisor** — Listens to face topics, publishes `/giga/set_hold` so the robot stops for people and drives again after interaction.

## Build

```bash
cd ~/Documents/robot/ros2_ws   # or your clone path
source /opt/ros/jazzy/setup.bash
sudo apt install -y python3-colcon-common-extensions python3-serial
rosdep install --from-paths src --ignore-src -y --skip-keys ament_python
colcon build --symlink-install
source install/setup.bash
```

If `python3-serial` is missing: `sudo apt install -y python3-serial`.

## Full stack (three terminals)

**1 — Giga serial bridge** (USB free, `SERIAL_LOG_VERBOSE 0` on the firmware):

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run giga_serial_bridge giga_serial_node --ros-args -p port:=/dev/ttyACM0
```

**2 — Supervisor** (debounces faces + interaction → `/giga/set_hold`):

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run robot_supervisor supervisor_node
```

**3 — Face recognition with ROS publishers** (`ENABLE_ROS=1` + `rclpy` on `PYTHONPATH`):

```bash
source /opt/ros/jazzy/setup.bash
cd /path/to/robot/facial_recognition
source .venv/bin/activate   # if you use a venv
# Jazzy Python site-packages (adjust python3.12 if your ROS uses another version):
export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH
export ENABLE_ROS=1
# Optional: echo ENABLE_ROS=1 >> .env
python main.py
```

If `import rclpy` fails, list the correct folder:

```bash
ls /opt/ros/jazzy/lib/
```

## Topics

| Topic | Type | Publisher | Purpose |
|-------|------|-----------|---------|
| `/face/faces_detected` | `Int32` | Face app | Number of faces this frame (after detection stride). |
| `/face/interaction_active` | `Bool` | Face app | `true` during name capture / enrollment. |
| `/giga/set_hold` | `Bool` | **supervisor** | `true` = HOLD, `false` = RUN (bridge forwards to USB). |
| `/giga/hold_active` | `Bool` | **giga_serial_bridge** | Echo of Giga state (after commands). |

## Supervisor parameters

```bash
ros2 run robot_supervisor supervisor_node --ros-args \
  -p present_streak:=2 \
  -p absent_streak:=4 \
  -p post_interaction_grace_sec:=4.0
```

- **present_streak** — consecutive messages with `faces_detected > 0` before treating “person present”.
- **absent_streak** — consecutive messages with `0` before “person gone”.
- **post_interaction_grace_sec** — after enrollment/STT ends, do not re-HOLD from presence alone for this many seconds (lets the robot drive away even if the camera still sees the user).

## Manual tests (bridge only)

```bash
ros2 topic echo /giga/hold_active
ros2 topic pub --once /giga/set_hold std_msgs/Bool "{data: true}"
ros2 topic pub --once /giga/set_hold std_msgs/Bool "{data: false}"
```

## Firmware

Use `movement/movement.ino` with **`SERIAL_LOG_VERBOSE 0`** while the bridge owns USB.
