# Installation and configuration

This project runs a face-recognition loop: known people are greeted by name; unknown faces trigger a spoken prompt and microphone capture to enroll a name. Settings are loaded from a `.env` file (recommended) or the shell environment.

## Requirements

- **Python** 3.10+ (3.13 works if `setuptools<81` is installed as in `requirements.txt`, for `face_recognition` compatibility).
- **MySQL** server for storing names and face encodings.
- **Webcam** (V4L2 on Linux).
- **Microphone** for name capture.
- **Optional:** NVIDIA GPU only helps if you use `FACE_DETECTION_MODEL=cnn`; the default `hog` model is CPU-based.

## 1. System packages (Debian / Ubuntu)

Install build tools and libraries commonly needed for `dlib` / `face_recognition`, OpenCV, and PyAudio:

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake pkg-config \
  libopenblas-dev liblapack-dev libjpeg-dev \
  libx11-dev libgtk-3-dev \
  ffmpeg \
  python3-venv \
  portaudio19-dev libportaudio2 \
  mysql-client
```

For **audio output** (TTS):

- **Piper** (recommended): install [`piper`](https://github.com/rhasspy/piper/releases) on your `PATH` and download a voice `.onnx` + matching `.onnx.json` (e.g. from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)). A WAV player such as `paplay` (PulseAudio) or `aplay` is used to play synthesized audio.
- **Fallback:** `espeak-ng` — `sudo apt install -y espeak-ng` (robotic but simple).

## 2. MySQL database

1. Create the database and table:

   ```bash
   mysql -u root -p < init_mysql.sql
   ```

2. Create an application user and grant access (adjust password and host as needed):

   ```sql
   CREATE USER 'robot'@'localhost' IDENTIFIED BY 'your_secure_password';
   GRANT ALL PRIVILEGES ON robot.* TO 'robot'@'localhost';
   FLUSH PRIVILEGES;
   ```

3. If the `users` table already existed **without** the `face_file_hash` column, add it:

   ```sql
   ALTER TABLE robot.users
     ADD COLUMN face_file_hash VARCHAR(64) NULL
     COMMENT 'SHA-256 hex of enrolled face JPEG';
   ```

## 3. Python environment

From the project directory (`robot-project`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If `pip install PyAudio` fails, ensure `portaudio19-dev` is installed (see system packages above).

## 4. Configuration (`.env`)

1. Copy the template:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. **Do not commit `.env`** — it may contain `DB_PASSWORD` and is listed in `.gitignore`.

At minimum, set:

- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` to match your MySQL user and database.
- `CAMERA_INDEX` (and optionally `ALLOW_CAMERA_FALLBACK=1`) so the correct webcam opens.
- `MIC_INDEX` or `MIC_NAME_HINT` so the correct microphone is selected.
- `PIPER_MODEL` (and `PIPER_BINARY` if `piper` is not on `PATH`) for neural TTS, or use `TTS_ENGINE=espeak`.

### Environment variables reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `SUPPRESS_AUDIO_BACKEND_NOISE` | `1` | Hide noisy stderr from some audio backends when `1`. |
| `ENABLE_VOICE` | `1` | Disable all TTS when `0`. |
| **Camera / mic** | | |
| `CAMERA_URL` | *(empty)* | HTTP/RTSP URL for a remote stream (e.g. Pi MJPEG); if set, tried before local `CAMERA_INDEX`. |
| `CAMERA_INDEX` | `2` | OpenCV camera index. |
| `ALLOW_CAMERA_FALLBACK` | `1` | Try other indices if the configured camera fails. |
| `CAMERA_WIDTH` | `640` | Capture width. |
| `CAMERA_HEIGHT` | `480` | Capture height. |
| `MIC_INDEX` | `1` | Microphone device index (PyAudio / SpeechRecognition). |
| `MIC_NAME_HINT` | `fingers` | Substring matched against mic names to auto-pick a device. |
| `MIC_SAMPLE_RATE` | `16000` | Sample rate for Vosk / capture. |
| `MIC_CHUNK_SIZE` | `1024` | Audio chunk size. |
| `MIC_AMBIENT_DURATION` | `0.1` | Ambient noise calibration for Google STT (`0` to skip). |
| `MIC_MIN_ENERGY` | `120` | Base energy threshold for the recognizer. |
| **Face recognition** | | |
| `FACE_TOLERANCE` | `0.42` | Distance threshold for a match (lower = stricter). |
| `FRAME_SCALE` | `0.5` | Scale factor for the main detection pass. |
| `FAR_FRAME_SCALE` | `0.75` | Scale when `DISTANT_FACE_RETRY=1`. |
| `PROCESS_EVERY_N_FRAMES` | `3` | Run detection every N frames (higher = less CPU). |
| `FACE_ENCODING_JITTERS` | `2` | dlib jitter passes (accuracy vs speed). |
| `MIN_FACE_SIZE` | `35` | Min face height/width in scaled coordinates. |
| `MIN_FAR_FACE_SIZE` | `24` | Min size on the distant-face retry path. |
| `FACE_DETECTION_MODEL` | `hog` | `hog` (CPU) or `cnn` (GPU-capable, heavier). |
| `DISTANT_FACE_RETRY` | `0` | Set `1` to retry detection at `FAR_FRAME_SCALE` if no face. |
| `MIN_BLUR_SCORE` | `70` | Laplacian variance minimum for enrollment frames. |
| **Presence / greeting** | | |
| `TRACK_CACHE_SECONDS` | `1.0` | How long to reuse a face box→name association. |
| `TRACK_IOU_THRESHOLD` | `0.45` | IoU threshold to match the same face across frames. |
| `EXIT_RESET_SECONDS` | `5.0` | After this many seconds unseen, greet again on return. |
| `RECOGNITION_STREAK` | `2` | Consecutive frames before treating a match as stable. |
| `UNKNOWN_STREAK` | `2` | Frames of unknown before enrollment flow. |
| `UNKNOWN_COOLDOWN` | `15` | Seconds before retrying unknown enrollment. |
| `ENROLLMENT_GRACE_PERIOD` | `30` | Seconds after last enrollment before another. |
| `ENROLLMENT_SAMPLES` | `3` | Number of encodings saved per enrollment (loop stops early when enough). |
| `ENROLLMENT_JITTERS` | `1` | dlib jitters during enrollment only (lower = faster). |
| `ENROLLMENT_FRAME_SLEEP_SEC` | `0.08` | Pause between enrollment camera grabs. |
| `ENROLLMENT_MAX_TRIES` | `18` | Max capture attempts before giving up. |
| **Speech-to-text** | | |
| `STT_ENGINE` | `google` | `google` (online) or `vosk` (offline). |
| `SPEECH_LANGUAGE` | `en-IN` | BCP-47 language for Google STT. |
| `SPEECH_ALT_LANGUAGE` | *(empty)* | Fallback language on Google network errors only. |
| `NAME_TIMEOUT` | `10` | Seconds to wait for speech to start. |
| `NAME_PHRASE_TIME_LIMIT` | `8` | Max seconds of audio for one utterance. |
| `VOSK_MODEL_PATH` | `/opt/vosk/models` | Directory containing the model or a single model subfolder. |
| `VOSK_MAX_SECONDS` | `5.0` | Max listening time for Vosk. |
| `VOSK_SILENCE_TIMEOUT` | `1.5` | End capture after this much silence (Vosk). |
| `VOSK_GRAMMAR` | *(empty)* | Optional JSON list of phrases to bias recognition, e.g. `["pawan","navjot"]`. |
| **TTS** | | |
| `TTS_ENGINE` | `piper` | `piper` or `espeak`. |
| `PIPER_BINARY` | *(empty)* | Full path to `piper` if not on `PATH`. |
| `PIPER_MODEL` | `/opt/piper/models/en_US-lessac-medium.onnx` | ONNX model path. |
| `PIPER_LENGTH_SCALE` | `0.9` | Speaking rate (`<1` faster, model-dependent). |
| `ESPEAK_VOICE` | `en` | eSpeak voice name. |
| `ESPEAK_WPM` | `150` | Words per minute for eSpeak. |
| **Database / files** | | |
| `DB_HOST` | `localhost` | MySQL host. |
| `DB_USER` | `robot` | MySQL user. |
| `DB_PASSWORD` | *(empty)* | MySQL password. |
| `DB_NAME` | `robot` | Database name. |
| `FACE_FILES_DIR` | `data/faces` | Directory for enrolled face JPEG crops. |
| **Giga (split setup)** | | |
| `GIGA_SERIAL_PORT` | `auto` | Local device or `socket://host:port` when a Pi runs `pi_giga_tcp_bridge.py`. |
| `GIGA_BOOT_DELAY_SEC` | `2.0` | After USB open (local only); use `0` with `socket://`. |

## 5. Speech-to-text notes

- **`STT_ENGINE=google`** uses the public Google Web Speech API via the `SpeechRecognition` package. It needs **internet** from the machine running the app.
- **`STT_ENGINE=vosk`** is fully offline. Download a model (for example from [alphacephei/vosk-api](https://alphacephei.com/vosk/models)), extract it, and point `VOSK_MODEL_PATH` at that folder (or a parent folder with a single model inside—the app can auto-detect).

## 6. Run the application

From the project directory, with the virtual environment activated:

```bash
python main.py
```

Stop with **Ctrl+C** (exit should be clean).

Remove a user from the database and delete their saved face images under `FACE_FILES_DIR`:

```bash
python main.py remove-user "FirstName"
```

## 7. Troubleshooting

| Issue | What to check |
|-------|----------------|
| No camera | Lower or raise `CAMERA_INDEX`; try `ALLOW_CAMERA_FALLBACK=1`. |
| Wrong mic | Set `MIC_NAME_HINT` to a unique substring of the device name, or set `MIC_INDEX` after listing devices. |
| `face_recognition` / `dlib` build errors | Install the system dev packages in section 1; ensure `cmake` is present. |
| `pkg_resources` / setuptools errors on Python 3.12+ | Keep `setuptools<81` from `requirements.txt`. |
| Piper silent or errors | Confirm `piper --version`, model path, and that `.onnx` and `.onnx.json` sit beside each other; set `PIPER_BINARY` if needed. |
| MySQL connection errors | `DB_*` in `.env`, user grants, and that MySQL is running. |
| Split setup: Giga not stopping | Pi bridge running? `GIGA_SERIAL_PORT=socket://pi-ip:7000`, firewall open, `GIGA_BOOT_DELAY_SEC=0`. |

## 8. Laptop + Raspberry Pi (processing on laptop, hardware on robot)

Keep **MySQL**, **Python face app**, **Piper TTS**, and **`main.py`** on the **laptop**. On the **Pi** you only need: TCP → Giga serial, optional MJPEG camera stream, and (if you use it) network audio.

### Motor control (Giga USB on the Pi)

1. On the Pi, install pyserial: `pip install pyserial` or `sudo apt install python3-serial`.
2. Copy `scripts/pi_giga_tcp_bridge.py` to the Pi (same repo path is fine).
3. Run (adjust device if needed):

   ```bash
   python3 pi_giga_tcp_bridge.py --device /dev/ttyACM0 --port 7000
   ```

4. On the **laptop** `.env`, point pyserial at the Pi:

   ```env
   GIGA_SERIAL_PORT=socket://192.168.x.x:7000
   GIGA_BOOT_DELAY_SEC=0
   ```

Use the Pi’s real IP or hostname. **Wi‑Fi latency** is usually fine for `HOLD` / `RUN`; keep one client only (`main.py`).

### Pi USB webcam with **built-in microphone** (video + STT mic)

Goal: **same UVC device** on the Pi sends **MJPEG** to the laptop for OpenCV, and the **mic** appears as a normal input on the **laptop** for `speech_input`—without sending Piper TTS to the Pi (TTS stays on the laptop speakers).

#### 1) On the Pi: check video and ALSA

```bash
v4l2-ctl --list-devices          # note /dev/video0 (or video1)
arecord -l                       # note card,device for the webcam mic
arecord -d 3 -f cd test.wav && aplay test.wav   # quick mic test
```

If the mic does not show up as a separate capture device, use **PulseAudio / PipeWire** on the Pi and pick the webcam’s **input** in `pavucontrol` once.

#### 2) On the Pi: MJPEG HTTP stream (for `CAMERA_URL`)

Install and run something that exposes **Motion JPEG over HTTP** from `/dev/videoX` (choose one):

- **mjpg-streamer** ([jacksonliam/mjpg-streamer](https://github.com/jacksonliam/mjpg-streamer)) — typical URL: `http://<pi-ip>:8080/?action=stream`  
- Or **`ffmpeg`** publishing MJPEG over HTTP (many tutorials online).

Keep resolution modest (e.g. **640×480**, **10–15 fps**) so the Pi 3B+ keeps up.

**Firewall** on the Pi: allow the HTTP port (e.g. **8080**) from the laptop only.

On the **laptop** `.env`:

```env
CAMERA_URL=http://192.168.x.x:8080/?action=stream
```

(`CAMERA_URL` is tried first; local `CAMERA_INDEX` is only a fallback.)

#### 3) On the Pi: PulseAudio network (so the **mic** is visible on the laptop)

`main.py` uses **PyAudio on the laptop**, so the Pi mic must show up as a **local** capture device on the laptop. The usual way is a **PulseAudio tunnel** (do **not** set global `PULSE_SERVER` for the whole app—that can route **TTS** to the Pi).

**On the Pi** (PulseAudio; Raspberry Pi OS often has `pulseaudio` per user):

1. Allow TCP from your LAN (edit `~/.config/pulse/default.pa` or `/etc/pulse/default.pa`, then restart Pulse):

   ```text
   load-module module-native-protocol-tcp auth-ip-acl=127.0.0.1;192.168.0.0/24 auth-anonymous=1
   ```

   Tighten `auth-ip-acl` to the laptop’s IP for security.

2. Reload Pulse (example): `pulseaudio -k; pulseaudio --start` (or reboot).

3. List the **exact source name** for the webcam mic:

   ```bash
   pactl list sources short
   ```

   Example name: `alsa_input.usb-046d_Logitech_Webcam_...-00.analog-stereo`

**Firewall** on the Pi: allow TCP **4713** from the laptop.

**On the laptop** (same LAN):

1. Create a **tunnel source** that mirrors the Pi’s mic:

   ```bash
   pactl load-module module-tunnel-source \
     server=tcp:192.168.x.x:4713 \
     source=alsa_input.usb-.....-00.analog-stereo
   ```

   Use the **exact** `source=` string from the Pi’s `pactl list sources short`.

2. List inputs on the laptop and pick the new device:

   ```bash
   python3 -c "import pyaudio; pa=pyaudio.PyAudio(); [print(i, pa.get_device_info_by_index(i)['name']) for i in range(pa.get_device_count()) if pa.get_device_info_by_index(i)['maxInputChannels']>=1]"
   ```

3. In `.env`, point the app at that device:

   ```env
   MIC_NAME_HINT=Tunnel        # substring of the tunnel source name on the laptop
   ```

   or set **`MIC_INDEX`** to the number printed for the tunnel input.

**PipeWire on the Pi:** If the Pi uses PipeWire instead of Pulse, enable **PulseAudio compatibility** (socket) or use `pw-cli` / documentation for “network source”; the idea is the same: **a laptop-local virtual mic** fed from the Pi.

#### 4) Checklist

| Piece | Where | Check |
|--------|--------|--------|
| Video | Laptop `CAMERA_URL` | Browser or `ffplay` opens MJPEG URL |
| Mic | Laptop PyAudio | `MIC_NAME_HINT` / `MIC_INDEX` selects tunnel |
| Motors | Laptop `.env` | `GIGA_SERIAL_PORT=socket://pi-ip:7000` + bridge on Pi |
| DB / Piper / STT | Laptop | Unchanged (`DB_HOST=localhost`, etc.) |

### Database

MySQL can stay on the **laptop** (`DB_HOST=localhost`). For a shared DB on the Pi, set `DB_HOST` to the Pi’s IP and grant MySQL user access from the laptop’s address.
