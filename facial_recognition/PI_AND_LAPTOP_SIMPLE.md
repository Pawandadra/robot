# Simple guide: Raspberry Pi on the robot + laptop does the thinking

## What you are building

- **Raspberry Pi** sits on the robot. It is plugged into: the **camera (with mic)**, and the **Arduino Giga** (motors).
- **Your laptop** runs the face app (`main.py`), talks to you (Piper), and listens for your name. It **does not** need the camera or Giga plugged in.

The laptop must **see** the camera picture over Wi‑Fi or Ethernet, **hear** the mic over the network, and **send motor commands** (stop / go) to the Giga through the Pi.

---

## What runs where


| Thing                             | Where                                                         |
| --------------------------------- | ------------------------------------------------------------- |
| Camera picture                    | Pi sends a **video stream** → laptop reads it                 |
| Microphone                        | Pi’s mic is **copied** to the laptop as a “virtual mic”       |
| Motors (HOLD / RUN)               | Laptop sends commands → **small program on Pi** → USB to Giga |
| Face recognition, database, voice | **Laptop only**                                               |


---

# Part 1 — On the Raspberry Pi

Do these on the Pi (keyboard + screen, or SSH).

### Step 1: Find the Pi’s IP address

On the Pi, run:

```bash
hostname -I
```

Write down something like `192.168.1.50`. Your **laptop** will use this everywhere below.

### Step 2: Put the Giga on the network (motor USB)

1. Install Python serial support on the Pi:
  ```bash
   sudo apt update
   sudo apt install -y python3-serial
  ```
2. Copy `scripts/pi_giga_tcp_bridge.py` from this project onto the Pi (USB stick, `scp`, or git clone the repo on the Pi).
3. Plug the **Giga** into the Pi. Find the port (often `/dev/ttyACM0`):
  ```bash
   ls /dev/ttyACM*
  ```
4. Start the bridge (change the path if your script is elsewhere):
  ```bash
   python3 pi_giga_tcp_bridge.py --device /dev/ttyACM0 --port 7000
  ```
   Leave this window open. You should see text like “waiting for client”.

### Step 3: Send the camera picture to the laptop (video)

You need a **small web video stream** from the Pi. Many people use **mjpg-streamer**.

1. Install it on the Pi (exact package name can differ; if this fails, search “mjpg-streamer Raspberry Pi” for your OS version):
  ```bash
   sudo apt install -y mjpg-streamer
  ```
2. Start it with your camera device (often `/dev/video0`):
  ```bash
   ustreamer --device=/dev/video0 --resolution=640x480 --desired-fps=15 --port=8080 --host=0.0.0.0
  ```
3. On the **laptop web browser**, open:
  `http://192.168.1.50:8080/?action=stream`  
   (use **your** Pi IP.)
   If you see a moving picture, video is OK.

### Step 4: Let the laptop “hear” the Pi microphone (audio)

The face app on the laptop uses a **microphone on the laptop**. Your mic is on the Pi, so we **mirror** that mic to the laptop using **PulseAudio** (standard on many Linux desktops).

**On the Pi**

1. Install PulseAudio tools if needed:
  ```bash
   sudo apt install -y pulseaudio pulseaudio-utils
  ```
2. Open the Pulse config for your user:
  ```bash
   mkdir -p ~/.config/pulse
   nano ~/.config/pulse/default.pa
  ```
3. Add **this one line** at the **end** of the file (change `192.168.1.0/24` to your home network if different):
  ```text
   load-module module-native-protocol-tcp auth-ip-acl=127.0.0.1;192.168.1.0/24 auth-anonymous=1
  ```
4. Save, then restart Pulse on the Pi:
  ```bash
   pulseaudio -k
   pulseaudio --start
  ```
5. Find the **name** of your webcam microphone:
  ```bash
   pactl list sources short
  ```
   You will see lines like `alsa_input.usb-.....`. **Copy the full name** of the one that is your webcam.

**On the laptop**

1. Open a terminal and run (replace IP and `source=` with your values):
  ```bash
   pactl load-module module-tunnel-source \
     server=tcp:192.168.1.50:4713 \
     source=PASTE_THE_FULL_NAME_FROM_PI_HERE
  ```
2. List microphones the laptop sees now. Easiest: open the project venv and run:
  ```bash
   python3 -c "import pyaudio; p=pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count()) if p.get_device_info_by_index(i).get('maxInputChannels',0)>=1]"
  ```
3. Pick the new line that looks like a **tunnel** or **remap** to the Pi. Set in `.env` either:
  - `MIC_NAME_HINT=part-of-that-name`, or  
  - `MIC_INDEX=number` from the left column.

**Firewall:** If it does not work, on the Pi allow ports **7000** (Giga bridge) and **8080** (video) and **4713** (Pulse) from the laptop. How you do that depends on your router / `ufw` — that is a separate small step if needed.

---

## Persistent Pi mic on the laptop (survives reboot)

**What each file does**


| File (in the repo)                                                   | Where it runs                                    | Purpose                                                                    |
| -------------------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------- |
| *(you create on Pi)* `/etc/pipewire/.../99-native-protocol-tcp.conf` | **Raspberry Pi**                                 | Opens TCP **4713** so the laptop may pull audio from the Pi                |
| `scripts/robot-pi-mic.env.example`                                   | **Laptop** (copy → `~/.config/robot-pi-mic.env`) | Pi IP, port, and **exact** mic `source=` name from the Pi                  |
| `scripts/laptop_pi_mic_tunnel.sh`                                    | **Laptop**                                       | Loads Pulse tunnel + `pactl set-default-source` so “default” mic is the Pi |
| `scripts/laptop-pi-mic-tunnel.service`                               | **Laptop** (`~/.config/systemd/user/`)           | Runs that script once after you log in (PipeWire session)                  |
| `scripts/laptop_pi_speaker_tunnel.sh`                                | **Laptop**                                       | Optional: tunnel **playback** to the Pi so Piper/paplay play on the robot  |
| `scripts/laptop-pi-speaker-tunnel.service`                           | **Laptop**                                       | Optional: run the speaker tunnel at login                                  |


Set `**PROJECT`** once to your clone path (adjust if yours differs):

```bash
export PROJECT="$HOME/Documents/robot/facial_recognition"
```

---

### A — Raspberry Pi (one-time, persistent)

1. On the Pi, see whether audio is **PipeWire** (usual on recent Pi OS):
  ```bash
   pactl list sources short | head -3
  ```
   If the line ends with `**PipeWire**`, use **step 2** below.  
   If you truly run **only** classic PulseAudio (no PipeWire), use **Step 4** in “Part 1” above (`default.pa` + `load-module module-native-protocol-tcp ...`) instead of step 2 here.
2. **PipeWire — open port 4713** (create system drop-in):
  ```bash
   sudo mkdir -p /etc/pipewire/pipewire-pulse.conf.d
   sudo nano /etc/pipewire/pipewire-pulse.conf.d/99-native-protocol-tcp.conf
  ```
   Paste (edit subnets to match **your** network: home Wi‑Fi, phone hotspot `172.20.10.0/24`, etc.):
3. **Reboot the Pi**, then check:
  ```bash
   ss -tlnp | grep 4713
  ```
   You should see `**pipewire-pulse**` (or similar) **listening on 0.0.0.0:4713**.
4. **Copy the mic source name** (second column) for your webcam:
  ```bash
   pactl list sources short
  ```
   Example: `alsa_input.usb-FINGERS_FINGERS_1080_Hi-Res_Webcam_....mono-fallback` — you will paste this into the laptop env file.

---

### B — Laptop (one-time files + login automation)

1. **Create the laptop config** from the example (uses `**$PROJECT`**):
  ```bash
   mkdir -p ~/.config
   cp "$PROJECT/scripts/robot-pi-mic.env.example" ~/.config/robot-pi-mic.env
   nano ~/.config/robot-pi-mic.env
  ```
   Set `**ROBOT_PI_HOST**` to your Pi’s IP (e.g. `192.168.1.50` or `172.20.10.2`).  
   Set `**ROBOT_PI_MIC_SOURCE**` to the **exact** string from Pi `pactl list sources short` (webcam line).  
   Leave `**ROBOT_PI_PULSE_PORT=4713`** unless you changed the Pi.
2. **Test manually** (script must be executable: `chmod +x "$PROJECT/scripts/laptop_pi_mic_tunnel.sh"`):
  ```bash
   chmod +x "$PROJECT/scripts/laptop_pi_mic_tunnel.sh"
   "$PROJECT/scripts/laptop_pi_mic_tunnel.sh"
  ```
   You should see a line like `**Default source: robot_pi_webcam_mic**` (or a tunnel name).  
   Check: `pactl info | grep -i "Default Source"`.
3. **PyAudio / `.env`**: run the one-liner from Step 4 (Part 1) and note the index for `**default**` (often **17**). In `**facial_recognition/.env`** set `**MIC_INDEX=**` that number and `**MIC_NAME_HINT=**` empty (or delete the hint line).
4. **Start the tunnel automatically on login**:
  ```bash
   mkdir -p ~/.config/systemd/user
   cp "$PROJECT/scripts/laptop-pi-mic-tunnel.service" ~/.config/systemd/user/laptop-pi-mic-tunnel.service
  ```
   Open the copied unit and set `**ExecStart=**` to your real script path if you are **not** using `~/Documents/robot/facial_recognition`:
   Then:
   **Note:** This only runs when **your user** has a session (graphical login or **user lingering**). Headless SSH-only with no audio session may need extra setup.
5. If the **Pi boots after** the laptop, run:
  ```bash
   systemctl --user restart laptop-pi-mic-tunnel.service
  ```

**Firewall:** the laptop must reach the Pi on **TCP 4713** (and **8080** / **7000** for camera and Giga bridge).

---

### C — TTS on the Pi (optional: audio **out** on the robot)

The face app runs Piper on the **laptop**, but `**paplay`** uses your **default Pulse/PipeWire sink**. If that sink is a **tunnel to the Pi**, speech comes out of the Pi’s **headphone jack / HDMI / USB speaker** (whatever is the Pi’s default output).

**No Python changes** — same `voice.py` and Piper; only Pulse routing changes.

1. **On the Pi**, get the default **playback** device name:
  ```bash
   pactl get-default-sink
   # or full list:
   pactl list sinks short
  ```
   Copy the **sink name** (second column), e.g. `alsa_output.platform-bcm2835_audio-analog-stereo` or an HDMI sink.
2. **On the laptop**, add to `**~/.config/robot-pi-mic.env`** (same file as the mic tunnel):
  ```bash
   ROBOT_PI_SINK=alsa_output.platform-bcm2835_audio-analog-stereo
   ROBOT_PI_SPEAKER_LOCAL_NAME=robot_pi_speaker
  ```
   (Use **your** sink string from step 1.)
3. **Test:**
  ```bash
   chmod +x "$PROJECT/scripts/laptop_pi_speaker_tunnel.sh"
   "$PROJECT/scripts/laptop_pi_speaker_tunnel.sh"
   pactl info | grep -i "Default Sink"
  ```
   You should hear a short test if you run `paplay` on a WAV file on the laptop.
4. **Login service (optional):**
  ```bash
   cp "$PROJECT/scripts/laptop-pi-speaker-tunnel.service" ~/.config/systemd/user/
   # Edit ExecStart path if needed (same as mic service)
   systemctl --user daemon-reload
   systemctl --user enable --now laptop-pi-speaker-tunnel.service
  ```
5. **Pi boots after laptop:** `systemctl --user restart laptop-pi-speaker-tunnel.service`

**Note:** Bluetooth speakers on the Pi are possible but add latency; wired analog or HDMI is simplest. If `**ROBOT_PI_SINK`** is not set, `laptop_pi_speaker_tunnel.sh` exits successfully and does nothing (mic-only setups).

**Louder voice on the Pi**

1. **Laptop** — in `**~/.config/robot-pi-mic.env`** raise `**ROBOT_PI_SPEAKER_VOLUME_PERCENT**` (default **150** after this change; try **180**–**200**). Re-run `laptop_pi_speaker_tunnel.sh` (or restart the systemd user service).
2. **Laptop** — optional extra boost for `**paplay`**: in `**facial_recognition/.env**` set `**PAPLAY_VOLUME=98304**` (~150% of Pulse’s stream scale; `**65536**` = 100%).
3. **On the Pi** — boost physical output: `pactl set-sink-volume @DEFAULT_SINK@ 150%` and/or use `**alsamixer`** for the sound card. Un-mute: `pactl set-sink-mute @DEFAULT_SINK@ 0`.

---

# Part 2 — On the laptop

### Step 1: Edit `.env`

Use your real Pi IP:

```env
CAMERA_URL=http://192.168.1.50:8080/?action=stream
GIGA_SERIAL_PORT=socket://192.168.1.50:7000
GIGA_BOOT_DELAY_SEC=0
```

Set `MIC_NAME_HINT` or `MIC_INDEX` from Part 1, Step 4.

Keep `DB_HOST=localhost` if MySQL runs on the laptop.

### Step 2: Start everything in order

1. Pi: **Giga bridge** running (`pi_giga_tcp_bridge.py`).
2. Pi: **mjpg-streamer** (or your video stream) running.
3. Pi: PipeWire/Pulse **TCP on 4713** (persistent drop-in above). Laptop: **mic tunnel** (script or systemd — see “Persistent audio”). Optional: **speaker tunnel** so TTS plays on the Pi (section **C**).
4. Laptop: activate venv, run `python main.py`.

---

# Part 3 — Quick checks


| Check  | What to do                                                                                                                |
| ------ | ------------------------------------------------------------------------------------------------------------------------- |
| Video  | Browser on laptop opens the `CAMERA_URL` and shows the robot’s view.                                                      |
| Mic    | In `.env`, `MIC_*` matches the tunnel device; say something during “what is your name”.                                   |
| Motors | With a face in view, robot should **stop** (HOLD); when you finish enrollment it should **move** again per your firmware. |


---

# If something fails

- **No picture:** Wrong URL or mjpg-streamer not running; try `?action=stream` vs your program’s help.  
- **No sound on laptop:** Tunnel command wrong `source=` name, or Pi firewall blocks **4713**.  
- **Motors never react:** Bridge not running on Pi, or `GIGA_SERIAL_PORT` IP/port wrong, or laptop can’t reach Pi on **7000**.

---

# Pi + Arduino Giga: separate power (no more blinking / brownouts)

## Why it blinks when everything is on the Pi

The Pi 3B+ can only supply **limited USB power**. A webcam + Arduino Giga together often need **more** than the Pi can give. Then the voltage dips and the Pi or the Giga **resets** → **blinking / disconnects**.

**Using two power supplies is fine.** The important part is **how you connect the wires** so they can still **talk** to each other.

## Rule you must follow (any wired setup)

If the Pi and the Giga use **different** power bricks:

- You still need **one electrical reference** between them.
- Connect **ground (GND) together**: Pi GND and Giga GND must be tied (USB shield often does this if data USB is used; for UART you **must** add a **GND wire** between Pi and Giga).

Never connect **5V from two supplies** to the same 5V rail in a way that fights—follow one method below.

---

## Way 1 — Easiest: **powered USB hub** (still USB serial)

1. Plug a **powered USB hub** into the Pi (hub has its **own** wall adapter).
2. Plug the **webcam** and the **Giga** into the **hub**, not directly into the Pi.
3. The hub feeds the heavy USB devices; the Pi mostly sees **data**.

Your software stays the same: on the Pi, the Giga is still `/dev/ttyACM0` (or similar), and `pi_giga_tcp_bridge.py` still works.

---

## Way 2 — Giga on its **own** USB power, **data** to the Pi only

Goal: Giga gets 5V from a **phone charger / USB supply**, Pi only uses **D+ / D− / GND** (no 5V from Pi to Giga).

- Some people use a **split cable** or a cable where **5V (red) is cut** on the side that goes to the Pi, while the Giga is powered from its **other** USB port or **barrel jack** (if your board allows both at once—check Arduino docs for **Giga R1** so you don’t back-feed power wrong).
- **GND** must still be common (USB ground does that if the cable is wired correctly).

This is a bit **hardware-specific**; if unsure, **Way 1 (powered hub)** is simpler.

---

## Way 3 — **Serial wires** (UART) instead of USB serial

1. Power **Pi** with its adapter, **Giga** with its adapter (or USB charger to Giga only).
2. Connect **three wires** (both boards **off** while wiring):
  - Pi **GND** ↔ Giga **GND**
  - Pi **UART TX** ↔ Giga **UART RX** (receive pin)
  - Pi **UART RX** ↔ Giga **UART TX** (send pin)  
   Use **3.3 V** UART pins only (Pi GPIO is 3.3 V; check Giga pinout for the right **Serial** pins, e.g. `Serial1`).
   **Pi 3B+ / Pi 4 — 40-pin header, physical pin numbers** (no GPIO labels on the board): find **pin 1** at the corner of the header (often marked with a **square** copper pad on the PCB). Odd pins are in the row **toward the inside** of the board; even pins are in the row **toward the edge**.

  | Signal       | GPIO name    | **Physical pin #**                                                     | Connect to Giga     |
  | ------------ | ------------ | ---------------------------------------------------------------------- | ------------------- |
  | Pi transmits | GPIO14 (TXD) | **8**                                                                  | **RX** of `Serial1` |
  | Pi receives  | GPIO15 (RXD) | **10**                                                                 | **TX** of `Serial1` |
  | Ground       | GND          | **6**, **9**, **14**, **20**, **25**, **30**, **34**, **39** (any one) | Giga **GND**        |

   **Do not** use **pin 1 or 17** (3.3 V) or **2 or 4** (5 V) for UART data — only **8**, **10**, and **GND**.
   **Giga R1 WiFi** (silkscreen **TX0/RX0**, **TX1/RX1**, … per [official pinout PDF](https://content.arduino.cc/assets/ABX00063-full-pinout.pdf)): use `**Serial1`** → **TX1** and **RX1** only (digital pins **D18** and **D19**). Do **not** use TX0/RX0 for this sketch unless you change the code to another `Serial` port.

  | Pi (physical pin) | →   | Giga R1                    |
  | ----------------- | --- | -------------------------- |
  | Pin **8** (TX)    | →   | **RX1** (receives from Pi) |
  | Pin **10** (RX)   | ←   | **TX1** (sends to Pi)      |
  | GND               | ↔   | **GND**                    |

3. On the Pi, enable the serial port (disable serial console on that UART if needed—Pi OS docs: “UART on Raspberry Pi”).
4. On the Giga, your sketch must read commands on `**Serial1`** (or another UART), not only `Serial` USB—or **duplicate** the same line parser on `Serial1`.
5. On the Pi, the device is often `**/dev/serial0`** or `**/dev/ttyAMA0`** instead of `/dev/ttyACM0`. Run:
  ```bash
   python3 pi_giga_tcp_bridge.py --device /dev/serial0 --port 7000
  ```
   (Use whatever device `ls /dev/serial*` shows after setup.)

This avoids USB power to the Giga from the Pi completely, but you **change firmware** to use UART.

### After UART is wired — checklist

1. **Firmware (`movement.ino`)**
  Near the top of the sketch, set `**HOST_USE_SERIAL1` to `1`** (or add build flag `-DHOST_USE_SERIAL1=1`).  
   Re-upload to the Giga. Host replies (`OK HOLD`, etc.) go on `**Serial1`**; USB `**Serial**` is still for boot / debug text only.
2. **Pi: enable the UART on the GPIO header**
  - `sudo raspi-config` → **Interface Options** → **Serial Port**.  
  - **Login shell over serial:** **No**.  
  - **Serial port hardware:** **Yes**.  
  - Reboot.
3. **Pi 3B+ only — stable 115200 on GPIO 14/15**
  By default Bluetooth can steal the good UART. Add **one** of these to `/boot/firmware/config.txt` (or `/boot/config.txt` on older images), then reboot:  
  - `dtoverlay=disable-bt` — disables Bluetooth, main UART on GPIO 14/15; **or**  
  - `dtoverlay=miniuart-bt` — keeps Bluetooth on a slower UART; main UART on GPIO 14/15.
4. **Find the device node**
  After reboot: `ls -l /dev/serial0` (often a link to `ttyAMA0`). Your user should be in group `**dialout`**: `sudo usermod -aG dialout $USER` then log out/in.
5. **Quick loopback-style test** (optional)
  With the bridge **stopped**, from the Pi:  
   `echo -e 'status\r' | sudo tee /dev/serial0`  
   You should see something like `EXT_HOLD 0` if the Giga is running and wired correctly (permissions may require `sudo` or `dialout`).
6. **Run the TCP bridge on the Pi** (same as USB, different device):
  ```bash
   cd /path/to/facial_recognition/scripts
   python3 pi_giga_tcp_bridge.py --device /dev/serial0 --baud 115200 --port 7000
  ```
7. **Laptop `.env`**
  Unchanged: `GIGA_SERIAL_PORT=socket://<pi-ip>:7000` and `GIGA_BOOT_DELAY_SEC=0`.

---

## Giga **Wi‑Fi** (same board, wireless)

The Giga R1 can use Wi‑Fi, but your `**movement.ino`** today speaks **serial text** (`HOLD` / `RUN`), not a network protocol. To use Wi‑Fi you would add a **small server** on the Giga and change the Pi/laptop to send commands over **TCP**—that is a **bigger software project** than USB or UART.

---

## Short pick list


| Situation                         | Suggestion                                                |
| --------------------------------- | --------------------------------------------------------- |
| Want least wiring change          | **Powered USB hub**                                       |
| Giga already on wall USB          | **Data-only / shared GND** (Way 2) if you know the wiring |
| Want Pi USB free / max separation | **UART** (Way 3) + GND tie + firmware `Serial1`           |


---

# One-page reminder

1. Pi: bridge on **7000**, video on **8080**, Pulse TCP on **4713**.
2. Laptop: tunnel mic, then `.env` with `CAMERA_URL` + `socket://…:7000`.
3. Laptop: `python main.py`.

More detail (same ideas, more technical) is in **INSTALL.md**, section 8.