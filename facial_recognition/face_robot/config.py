"""Settings from `.env` (via python-dotenv) or the process environment."""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

SUPPRESS_AUDIO_BACKEND_NOISE = os.getenv("SUPPRESS_AUDIO_BACKEND_NOISE", "1") == "1"
ENABLE_VOICE = os.getenv("ENABLE_VOICE", "1") == "1"
# USB serial to Arduino Giga: send HOLD/RUN from the vision loop (same process).
ENABLE_GIGA = os.getenv("ENABLE_GIGA", "1").strip().lower() in ("1", "true", "yes")
GIGA_SERIAL_PORT = os.getenv("GIGA_SERIAL_PORT", "auto").strip()
GIGA_BAUD = int(os.getenv("GIGA_BAUD", "115200"))
GIGA_BOOT_DELAY_SEC = float(os.getenv("GIGA_BOOT_DELAY_SEC", "2.0"))
# While a face is present, re-send HOLD every N seconds (0 = off). Helps if USB CDC drops a line.
GIGA_HOLD_PULSE_SEC = float(os.getenv("GIGA_HOLD_PULSE_SEC", "0.25"))
# Print [Giga] HOLD/RUN when the command changes (troubleshooting).
GIGA_DEBUG = os.getenv("GIGA_DEBUG", "0").strip().lower() in ("1", "true", "yes")
# Consecutive processed frames with ≥1 face before HOLD (1 = stop as soon as a face is seen).
HOLD_PRESENT_STREAK = int(os.getenv("HOLD_PRESENT_STREAK", "1"))
HOLD_ABSENT_STREAK = int(os.getenv("HOLD_ABSENT_STREAK", "4"))
HOLD_POST_INTERACTION_GRACE_SEC = float(os.getenv("HOLD_POST_INTERACTION_GRACE_SEC", "4.0"))
# Extra stillness after spoken greetings (seconds); vision loop keeps ticking hold during wait.
GREET_PAUSE_AFTER_KNOWN_SEC = float(os.getenv("GREET_PAUSE_AFTER_KNOWN_SEC", "1.5"))
GREET_PAUSE_AFTER_ENROLL_SEC = float(
    os.getenv("GREET_PAUSE_AFTER_ENROLL_SEC", "1.0")
)
GREET_PAUSE_AFTER_GROUP_SEC = float(os.getenv("GREET_PAUSE_AFTER_GROUP_SEC", "1.5"))

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "2"))
# If set (e.g. http://pi:8080/stream.mjpg), OpenCV reads the robot camera over the network (Pi + mjpg-streamer).
CAMERA_URL = os.getenv("CAMERA_URL", "").strip()
ALLOW_CAMERA_FALLBACK = os.getenv("ALLOW_CAMERA_FALLBACK", "1") == "1"
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))

MIC_INDEX = int(os.getenv("MIC_INDEX", "1"))
MIC_NAME_HINT = os.getenv("MIC_NAME_HINT", "fingers")
MIC_SAMPLE_RATE = int(os.getenv("MIC_SAMPLE_RATE", "16000"))
MIC_CHUNK_SIZE = int(os.getenv("MIC_CHUNK_SIZE", "1024"))
MIC_AMBIENT_DURATION = float(os.getenv("MIC_AMBIENT_DURATION", "0.1"))
MIC_MIN_ENERGY = int(os.getenv("MIC_MIN_ENERGY", "120"))

TOLERANCE = float(os.getenv("FACE_TOLERANCE", "0.42"))
FRAME_SCALE = float(os.getenv("FRAME_SCALE", "0.5"))
FAR_FRAME_SCALE = float(os.getenv("FAR_FRAME_SCALE", "0.75"))
PROCESS_EVERY_N_FRAMES = int(os.getenv("PROCESS_EVERY_N_FRAMES", "3"))
FACE_ENCODING_JITTERS = int(os.getenv("FACE_ENCODING_JITTERS", "2"))
MIN_FACE_SIZE = int(os.getenv("MIN_FACE_SIZE", "35"))
MIN_FAR_FACE_SIZE = int(os.getenv("MIN_FAR_FACE_SIZE", "24"))
FACE_DETECTION_MODEL = os.getenv("FACE_DETECTION_MODEL", "hog")
DISTANT_FACE_RETRY = os.getenv("DISTANT_FACE_RETRY", "0") == "1"
MIN_BLUR_SCORE = float(os.getenv("MIN_BLUR_SCORE", "70"))

TRACK_CACHE_SECONDS = float(os.getenv("TRACK_CACHE_SECONDS", "1.0"))
TRACK_IOU_THRESHOLD = float(os.getenv("TRACK_IOU_THRESHOLD", "0.45"))
EXIT_RESET_SECONDS = float(os.getenv("EXIT_RESET_SECONDS", "5.0"))

NAME_TIMEOUT = float(os.getenv("NAME_TIMEOUT", "10"))
NAME_PHRASE_TIME_LIMIT = float(os.getenv("NAME_PHRASE_TIME_LIMIT", "8"))

STT_ENGINE = os.getenv("STT_ENGINE", "google").strip().lower()
SPEECH_LANGUAGE = os.getenv("SPEECH_LANGUAGE", "en-IN").strip()
SPEECH_ALT_LANGUAGE = os.getenv("SPEECH_ALT_LANGUAGE", "").strip()
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "/opt/vosk/models").strip()
VOSK_MAX_SECONDS = float(os.getenv("VOSK_MAX_SECONDS", "5.0"))
VOSK_SILENCE_TIMEOUT = float(os.getenv("VOSK_SILENCE_TIMEOUT", "1.5"))
VOSK_GRAMMAR = os.getenv("VOSK_GRAMMAR", "").strip()

RECOGNITION_STREAK = int(os.getenv("RECOGNITION_STREAK", "2"))
UNKNOWN_STREAK = int(os.getenv("UNKNOWN_STREAK", "2"))
UNKNOWN_COOLDOWN = float(os.getenv("UNKNOWN_COOLDOWN", "15"))
ENROLLMENT_GRACE_PERIOD = float(os.getenv("ENROLLMENT_GRACE_PERIOD", "30"))
ENROLLMENT_SAMPLES = int(os.getenv("ENROLLMENT_SAMPLES", "3"))
# Enrollment-only: fewer jitters = faster encode (slightly noisier; OK for extra samples).
ENROLLMENT_JITTERS = int(os.getenv("ENROLLMENT_JITTERS", "1"))
# Pause between enrollment camera grabs (was 0.3s hardcoded).
ENROLLMENT_FRAME_SLEEP_SEC = float(os.getenv("ENROLLMENT_FRAME_SLEEP_SEC", "0.08"))
# Max camera attempts before giving up (avoids infinite loop if blur/detection fails).
ENROLLMENT_MAX_TRIES = int(os.getenv("ENROLLMENT_MAX_TRIES", "18"))

TTS_ENGINE = os.getenv("TTS_ENGINE", "piper").strip().lower()
ESPEAK_VOICE = os.getenv("ESPEAK_VOICE", "en").strip()
ESPEAK_WPM = int(os.getenv("ESPEAK_WPM", "150"))
PIPER_BINARY = os.getenv("PIPER_BINARY", "").strip()
PIPER_MODEL = os.getenv(
    "PIPER_MODEL",
    "/opt/piper/models/en_US-lessac-medium.onnx",
).strip()
PIPER_LENGTH_SCALE = float(os.getenv("PIPER_LENGTH_SCALE", "0.9"))

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "robot")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "robot")

FACE_FILES_DIR = os.getenv("FACE_FILES_DIR", "data/faces")
