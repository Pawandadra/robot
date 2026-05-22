import subprocess
import threading
import time

import json
import os

import speech_recognition as sr

# Shared with FAQ thread: only one listener at a time (enrollment vs local Q&A).
MIC_LOCK = threading.Lock()

from face_robot import config
from face_robot.audio_utils import audio_probe_context
from face_robot.names import normalize_name
from face_robot.voice import speak, speak_async

ACTIVE_MIC_INDEX = None
_VOSK_MODEL = None

# speech_recognition VAD defaults for the Google STT path (not exposed in .env).
_SR_DYNAMIC_ENERGY_RATIO = 1.25
_SR_PAUSE_THRESHOLD = 1.2
_SR_NON_SPEAKING_DURATION = 0.6


def _input_capable_indices():
    """Device indices that actually accept audio input (excludes many HDMI ports)."""
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        return {
            i
            for i in range(pa.get_device_count())
            if int(pa.get_device_info_by_index(i).get("maxInputChannels", 0)) >= 1
        }
    finally:
        pa.terminate()


def _looks_like_bad_capture(name: str | None) -> bool:
    """Heuristic: HDMI outputs are rarely valid microphones."""
    if not name:
        return False
    n = name.lower()
    return "hdmi" in n and "mic" not in n


def _pactl_default_source_name() -> str | None:
    """Pulse/PipeWire default recording source (e.g. robot_pi_webcam_mic after tunnel script)."""
    try:
        r = subprocess.run(
            ["pactl", "get-default-source"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if r.returncode != 0:
            return None
        name = (r.stdout or "").strip()
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None


def _pulse_default_looks_like_pi_tunnel(pulse_source: str) -> bool:
    pl = pulse_source.lower()
    return any(
        x in pl
        for x in (
            "robot_pi",
            "tunnel",
            "tunnel-source",
            "module-tunnel",
        )
    )


def resolve_microphone_index():
    try:
        with audio_probe_context():
            mic_names = sr.Microphone.list_microphone_names()
    except Exception as e:
        print(f"⚠️ Could not list microphones: {e}")
        return config.MIC_INDEX

    try:
        capable = _input_capable_indices()
    except Exception as e:
        print(f"⚠️ Could not query PyAudio input devices: {e}")
        capable = None

    def can_capture(idx: int) -> bool:
        if capable is None:
            return True
        return idx in capable

    if config.MIC_NAME_HINT:
        hint = config.MIC_NAME_HINT.lower()
        for index, mic_name in enumerate(mic_names):
            if not mic_name or hint not in mic_name.lower():
                continue
            if not can_capture(index):
                continue
            if _looks_like_bad_capture(mic_name):
                continue
            print(f"✅ Using microphone {index}: {mic_name}")
            return index

    # Pi + laptop: prefer Pulse tunnel mic before MIC_INDEX (often 0/1 = built-in laptop).
    if config.CAMERA_URL.strip() and config.MIC_TUNNEL_HINTS:
        for hint in (
            h.strip().lower()
            for h in config.MIC_TUNNEL_HINTS.split(",")
            if h.strip()
        ):
            for index, mic_name in enumerate(mic_names):
                if not mic_name or hint not in mic_name.lower():
                    continue
                if not can_capture(index):
                    continue
                if _looks_like_bad_capture(mic_name):
                    continue
                print(f"✅ Using Pi tunnel microphone {index}: {mic_name}")
                return index

    # PyAudio often names the device "default" even when Pulse default source is robot_pi_* .
    pulse_def = _pactl_default_source_name()
    if (
        config.CAMERA_URL.strip()
        and pulse_def
        and _pulse_default_looks_like_pi_tunnel(pulse_def)
    ):
        pdl = pulse_def.lower()
        for index, mic_name in enumerate(mic_names):
            if not mic_name or not can_capture(index) or _looks_like_bad_capture(mic_name):
                continue
            mn = mic_name.lower()
            if pdl in mn or mn in pdl or pulse_def.split(".")[-1].lower() in mn:
                print(f"✅ Using microphone {index}: {mic_name} (Pulse default source)")
                return index
        for index, mic_name in enumerate(mic_names):
            if not mic_name or not can_capture(index) or _looks_like_bad_capture(mic_name):
                continue
            if mic_name.strip().lower() == "default":
                print(
                    f"✅ Using microphone {index}: {mic_name} "
                    "(Pulse default capture → Pi tunnel; pactl: "
                    f"{pulse_def})"
                )
                return index

    if 0 <= config.MIC_INDEX < len(mic_names):
        name = mic_names[config.MIC_INDEX]
        if can_capture(config.MIC_INDEX) and not _looks_like_bad_capture(name):
            print(f"✅ Using configured microphone {config.MIC_INDEX}: {name}")
            return config.MIC_INDEX
        print(
            f"⚠️ MIC_INDEX={config.MIC_INDEX} ({name}) is not a usable capture device "
            "(e.g. HDMI output). Set MIC_INDEX or MIC_NAME_HINT to your analog/USB mic."
        )

    # Prefer any input-capable device that is not HDMI-only.
    if capable:
        if config.CAMERA_URL.strip():
            pdn = _pactl_default_source_name() or ""
            if not _pulse_default_looks_like_pi_tunnel(pdn):
                print(
                    "⚠️ CAMERA_URL is set but Pulse default source does not look like a Pi tunnel "
                    f"({pdn!r}). Run laptop_pi_mic_tunnel.sh, then check "
                    "`pactl get-default-source` and `pactl list sources short`. "
                    f"You can set MIC_NAME_HINT to a substring of your tunnel source or MIC_INDEX "
                    f"for PyAudio 'default' ({config.MIC_TUNNEL_HINTS!r})."
                )
        for index in sorted(capable):
            name = mic_names[index] if index < len(mic_names) else None
            if _looks_like_bad_capture(name):
                continue
            print(f"✅ Using input device {index}: {name or 'unknown'}")
            return index
        # Last resort: any device that reports input channels (even HDMI) to avoid None.
        index = min(capable)
        name = mic_names[index] if index < len(mic_names) else None
        print(f"✅ Using input device {index}: {name or 'unknown'}")

        return index

    print("⚠️ No PyAudio input device found. Using SpeechRecognition default microphone.")
    return None


def init_microphone():
    global ACTIVE_MIC_INDEX
    ACTIVE_MIC_INDEX = resolve_microphone_index()


def _ensure_vosk_model():
    global _VOSK_MODEL
    if _VOSK_MODEL is not None:
        return _VOSK_MODEL

    try:
        from vosk import Model
    except Exception as exc:
        raise RuntimeError(f"Vosk not available: {exc}") from exc

    model_path = config.VOSK_MODEL_PATH
    if not model_path or not os.path.isdir(model_path):
        raise RuntimeError(
            f"Vosk model path is not a directory: VOSK_MODEL_PATH={model_path!r}. "
            "It must point to the extracted model folder (the one containing 'conf' and 'am')."
        )

    def looks_like_model_dir(path: str) -> bool:
        return os.path.isdir(os.path.join(path, "conf")) and os.path.isdir(os.path.join(path, "am"))

    # If the user points to the parent directory (e.g. /opt/vosk/models),
    # auto-select a single extracted model within it.
    selected_path = model_path
    if not looks_like_model_dir(selected_path):
        try:
            children = [
                os.path.join(model_path, name)
                for name in os.listdir(model_path)
                if os.path.isdir(os.path.join(model_path, name))
            ]
        except Exception:
            children = []

        model_children = [p for p in children if looks_like_model_dir(p)]
        if len(model_children) == 1:
            selected_path = model_children[0]

    try:
        _VOSK_MODEL = Model(selected_path)
    except Exception as exc:
        # Vosk throws a generic Exception('Failed to create a model') for many path issues.
        try:
            sample = sorted(os.listdir(model_path))[:20]
        except Exception:
            sample = []
        raise RuntimeError(
            "Failed to create a Vosk model. "
            f"Check VOSK_MODEL_PATH={model_path!r}. "
            "It must point to the extracted model folder (with conf/ and am/), "
            "not the parent directory. "
            f"Directory listing (first entries): {sample}"
        ) from exc

    return _VOSK_MODEL


def _vosk_listen_and_transcribe(
    *,
    max_seconds: float | None = None,
    silence_timeout: float | None = None,
) -> str | None:
    """
    Offline STT using Vosk. Returns raw text or None.
    Requires: `vosk` package and a model folder at VOSK_MODEL_PATH.
    """
    max_sec = float(max_seconds if max_seconds is not None else config.VOSK_MAX_SECONDS)
    silence_sec = float(
        silence_timeout if silence_timeout is not None else config.VOSK_SILENCE_TIMEOUT
    )
    try:
        from vosk import KaldiRecognizer
    except Exception as exc:  # ImportError or internal errors
        raise RuntimeError(f"Vosk not available: {exc}") from exc

    model = _ensure_vosk_model()
    grammar = None
    if config.VOSK_GRAMMAR:
        try:
            grammar = json.loads(config.VOSK_GRAMMAR)
        except json.JSONDecodeError as exc:
            raise RuntimeError("VOSK_GRAMMAR must be valid JSON (e.g. [\"pawan\",\"navjot\"]).") from exc

    # Use PyAudio directly for speed and control.
    import pyaudio

    pa = pyaudio.PyAudio()
    open_kw = dict(
        format=pyaudio.paInt16,
        channels=1,
        rate=config.MIC_SAMPLE_RATE,
        input=True,
        frames_per_buffer=config.MIC_CHUNK_SIZE,
    )
    if ACTIVE_MIC_INDEX is not None:
        open_kw["input_device_index"] = ACTIVE_MIC_INDEX
    stream = pa.open(**open_kw)

    rec = (
        KaldiRecognizer(model, config.MIC_SAMPLE_RATE, grammar)
        if grammar
        else KaldiRecognizer(model, config.MIC_SAMPLE_RATE)
    )
    rec.SetWords(False)
    rec.SetMaxAlternatives(5)

    start = time.time()
    last_nonempty = start
    text_out = None

    try:
        while True:
            if time.time() - start > max_sec:
                break

            data = stream.read(config.MIC_CHUNK_SIZE, exception_on_overflow=False)
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result() or "{}")
                # Prefer best alternative if provided.
                alternatives = result.get("alternatives") or []
                if alternatives:
                    for alt in alternatives:
                        txt = (alt.get("text") or "").strip()
                        if txt:
                            text_out = txt
                            break
                else:
                    txt = (result.get("text") or "").strip()
                    if txt:
                        text_out = txt
                if text_out:
                    break
            else:
                partial = json.loads(rec.PartialResult() or "{}").get("partial", "").strip()
                if partial:
                    last_nonempty = time.time()
                elif time.time() - last_nonempty > silence_sec:
                    break

        if text_out is None:
            final = json.loads(rec.FinalResult() or "{}")
            alternatives = final.get("alternatives") or []
            if alternatives:
                for alt in alternatives:
                    txt = (alt.get("text") or "").strip()
                    if txt:
                        text_out = txt
                        break
            if text_out is None:
                text_out = (final.get("text") or "").strip() or None
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    return text_out


def listen_faq_google() -> str | None:
    """Short Google STT pass for FAQ phrase matching (answers still come from nora_faq.json)."""
    import speech_recognition as sr

    r = sr.Recognizer()
    r.energy_threshold = config.MIC_MIN_ENERGY
    r.dynamic_energy_threshold = True
    r.dynamic_energy_adjustment_damping = 0.12
    r.dynamic_energy_ratio = _SR_DYNAMIC_ENERGY_RATIO
    r.pause_threshold = 0.75
    r.phrase_threshold = 0.2
    r.non_speaking_duration = 0.45
    r.operation_timeout = config.FAQ_GOOGLE_TIMEOUT + config.FAQ_GOOGLE_PHRASE_LIMIT

    mic_kw = {"chunk_size": config.MIC_CHUNK_SIZE}
    if ACTIVE_MIC_INDEX is not None:
        mic_kw["device_index"] = ACTIVE_MIC_INDEX

    try:
        with MIC_LOCK:
            with audio_probe_context():
                with sr.Microphone(**mic_kw) as source:
                    amb = min(config.MIC_AMBIENT_DURATION, config.FAQ_GOOGLE_AMBIENT_SEC)
                    if amb > 0:
                        r.adjust_for_ambient_noise(source, duration=amb)
                    r.energy_threshold = max(
                        r.energy_threshold * config.MIC_AFTER_CALIB_MULT,
                        config.MIC_MIN_ENERGY,
                    )
                    audio = r.listen(
                        source,
                        timeout=config.FAQ_GOOGLE_TIMEOUT,
                        phrase_time_limit=config.FAQ_GOOGLE_PHRASE_LIMIT,
                    )
            try:
                heard_text = r.recognize_google(
                    audio, language=config.SPEECH_LANGUAGE
                )
            except sr.UnknownValueError:
                return None
            except sr.RequestError:
                if (
                    config.SPEECH_ALT_LANGUAGE
                    and config.SPEECH_ALT_LANGUAGE != config.SPEECH_LANGUAGE
                ):
                    heard_text = r.recognize_google(
                        audio, language=config.SPEECH_ALT_LANGUAGE
                    )
                else:
                    raise
        if not heard_text or not str(heard_text).strip():
            return None
        print(f"FAQ speech: {heard_text!r}")
        return str(heard_text).strip()
    except sr.WaitTimeoutError:
        return None


def get_name():
    r = sr.Recognizer()
    r.energy_threshold = config.MIC_MIN_ENERGY
    r.dynamic_energy_threshold = True
    r.dynamic_energy_adjustment_damping = 0.12
    r.dynamic_energy_ratio = _SR_DYNAMIC_ENERGY_RATIO
    r.pause_threshold = _SR_PAUSE_THRESHOLD
    r.phrase_threshold = 0.2
    r.non_speaking_duration = _SR_NON_SPEAKING_DURATION
    r.operation_timeout = config.NAME_TIMEOUT + config.NAME_PHRASE_TIME_LIMIT

    for attempt in range(3):
        try:
            speak("I am the Nora. what is your name?")
            if config.NAME_LISTEN_AFTER_TTS_SEC > 0:
                time.sleep(config.NAME_LISTEN_AFTER_TTS_SEC)

            heard_text = None
            if config.STT_ENGINE == "vosk":
                with MIC_LOCK:
                    heard_text = _vosk_listen_and_transcribe()
                if heard_text:
                    print(f"Speech heard (vosk): {heard_text}")
                else:
                    raise sr.UnknownValueError()
            elif config.STT_ENGINE == "google":
                mic_kw = {"chunk_size": config.MIC_CHUNK_SIZE}
                if ACTIVE_MIC_INDEX is not None:
                    mic_kw["device_index"] = ACTIVE_MIC_INDEX
                with MIC_LOCK:
                    with audio_probe_context():
                        with sr.Microphone(**mic_kw) as source:
                            # Keep this small to reduce start-up latency.
                            if config.MIC_AMBIENT_DURATION > 0:
                                r.adjust_for_ambient_noise(
                                    source, duration=config.MIC_AMBIENT_DURATION
                                )
                            r.energy_threshold = max(
                                r.energy_threshold * config.MIC_AFTER_CALIB_MULT,
                                config.MIC_MIN_ENERGY,
                            )
                            print(f"Listening with energy threshold: {r.energy_threshold:.1f}")

                            audio = r.listen(
                                source,
                                timeout=config.NAME_TIMEOUT,
                                phrase_time_limit=config.NAME_PHRASE_TIME_LIMIT,
                            )

                    try:
                        heard_text = r.recognize_google(
                            audio, language=config.SPEECH_LANGUAGE
                        )
                        print(f"Speech heard ({config.SPEECH_LANGUAGE}): {heard_text}")
                    except sr.RequestError:
                        # Optional one-shot fallback (keeps latency low in normal cases).
                        if (
                            config.SPEECH_ALT_LANGUAGE
                            and config.SPEECH_ALT_LANGUAGE != config.SPEECH_LANGUAGE
                        ):
                            heard_text = r.recognize_google(
                                audio, language=config.SPEECH_ALT_LANGUAGE
                            )
                            print(
                                f"Speech heard ({config.SPEECH_ALT_LANGUAGE}): {heard_text}"
                            )
                        else:
                            raise
            else:
                raise RuntimeError("Invalid STT_ENGINE. Use 'google' or 'vosk'.")

            name = normalize_name(heard_text)
            if name is None:
                raise sr.UnknownValueError()

            return name

        except sr.UnknownValueError:
            print(f"Speech error: could not understand name on attempt {attempt + 1}")
            if attempt < 2:
                speak("Please say only your first name clearly")
        except sr.WaitTimeoutError:
            print(
                f"Speech error: listening timed out (no speech detected) on attempt {attempt + 1}. "
                "Try MIC_AFTER_CALIB_MULT=0.35, lower MIC_MIN_ENERGY, longer NAME_TIMEOUT, "
                "or verify Pi mic tunnel / levels."
            )
            if attempt < 2:
                speak("I did not hear anything")
        except Exception as e:
            print(f"Speech error: {e!r}")
            if attempt < 2:
                speak("Let's try again")

    speak_async("Could not hear")
    return None
