# ROS 2 workspace (Jazzy)

Contains the **giga_serial_bridge** package: talks to `movement.ino` on the Arduino Giga over USB (`HOLD` / `RUN` / `STATUS`).

## Build

```bash
cd ~/Documents/robot/ros2_ws   # or your clone path
source /opt/ros/jazzy/setup.bash
sudo apt install -y python3-colcon-common-extensions python3-serial
rosdep install --from-paths src --ignore-src -y
colcon build --symlink-install
source install/setup.bash
```

## Run the bridge

Giga connected, port usually `/dev/ttyACM0`:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run giga_serial_bridge giga_serial_node --ros-args -p port:=/dev/ttyACM0 -p baud_rate:=115200
```

## Interface (namespace `/giga`)

| Kind | Name | Type | Purpose |
|------|------|------|---------|
| Subscribe | `/giga/set_hold` | `std_msgs/Bool` | `true` → HOLD, `false` → RUN |
| Publish | `/giga/hold_active` | `std_msgs/Bool` | Latched; current hold state |
| Service | `/giga/trigger_hold` | `std_srvs/Trigger` | Same as `set_hold=true` |
| Service | `/giga/trigger_run` | `std_srvs/Trigger` | Same as `set_hold=false` |
| Service | `/giga/sync_status` | `std_srvs/Trigger` | Sends `STATUS` to Giga, updates `hold_active` |

## Quick tests (second terminal, workspace sourced)

```bash
ros2 topic echo /giga/hold_active
```

```bash
ros2 topic pub --once /giga/set_hold std_msgs/Bool "{data: true}"
ros2 topic pub --once /giga/set_hold std_msgs/Bool "{data: false}"
```

```bash
ros2 service call /giga/trigger_hold std_srvs/srv/Trigger
ros2 service call /giga/trigger_run std_srvs/srv/Trigger
```

If the serial port is busy, close minicom / Arduino Serial Monitor and consider `sudo systemctl stop ModemManager`.

## Firmware

Use `movement/movement.ino` with **`SERIAL_LOG_VERBOSE 0`** while this node owns the USB port (reduces extra serial traffic).
