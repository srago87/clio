import os
import time
import wave
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"

# Config from environment (sourced from config.sh by start.sh)
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro").lower()
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_bella")

# Piper fallback config
PIPER_MODEL_NAME = "en_US-lessac-medium"

# Lazy-loaded engine instances
_kokoro = None
_piper_voice = None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        model_path = MODELS_DIR / "kokoro-v1.0.onnx"
        voices_path = MODELS_DIR / "voices-v1.0.bin"
        if not model_path.exists() or not voices_path.exists():
            raise FileNotFoundError(
                f"Kokoro model files not found in {MODELS_DIR}. "
                "Run ./install.sh to download them."
            )
        _kokoro = Kokoro(str(model_path), str(voices_path))
    return _kokoro


def _get_piper():
    global _piper_voice
    if _piper_voice is None:
        from piper import PiperVoice
        model_path = MODELS_DIR / f"{PIPER_MODEL_NAME}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper model not found at {model_path}. "
                "Download it from HuggingFace rhasspy/piper-voices."
            )
        _piper_voice = PiperVoice.load(str(model_path))
    return _piper_voice


def _split_for_kokoro(text: str, max_chars: int = 200) -> list[str]:
    """Split text at sentence/clause boundaries to stay under Kokoro's phoneme limit."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        candidate = text[:max_chars]
        split_at = -1
        for sep in [". ", "! ", "? ", "; ", ", ", " "]:
            idx = candidate.rfind(sep)
            if idx > max_chars // 2:
                split_at = idx + len(sep)
                break
        if split_at == -1:
            split_at = max_chars
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return [c for c in chunks if c]


def _synthesize_kokoro(text: str, output_path: str) -> None:
    import numpy as np
    import soundfile as sf
    kokoro = _get_kokoro()
    lang = "en-gb" if KOKORO_VOICE.startswith("b") else "en-us"
    chunks = _split_for_kokoro(text)
    if len(chunks) == 1:
        samples, sample_rate = kokoro.create(text, voice=KOKORO_VOICE, speed=1.0, lang=lang)
    else:
        parts, sample_rate = [], None
        for chunk in chunks:
            s, sr = kokoro.create(chunk, voice=KOKORO_VOICE, speed=1.0, lang=lang)
            parts.append(s)
            sample_rate = sr
        samples = np.concatenate(parts)
    sf.write(output_path, samples, sample_rate)


def _synthesize_piper(text: str, output_path: str) -> None:
    voice = _get_piper()
    with wave.open(output_path, "w") as wav_file:
        voice.synthesize_wav(text, wav_file)


def synthesize(text: str, output_path: str) -> None:
    t0 = time.time()
    engine_used = TTS_ENGINE

    if TTS_ENGINE == "kokoro":
        try:
            _synthesize_kokoro(text, output_path)
        except Exception as e:
            print(f"[tts] Kokoro failed ({e}), falling back to Piper")
            engine_used = "piper"
            _synthesize_piper(text, output_path)
    else:
        _synthesize_piper(text, output_path)

    print(f"[tts] {engine_used} synthesized {len(text)} chars in {(time.time()-t0)*1000:.0f}ms")
