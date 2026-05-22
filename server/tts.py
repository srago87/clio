from pathlib import Path
from piper import PiperVoice

MODEL_NAME = "en_US-lessac-medium"
MODELS_DIR = Path(__file__).parent / "models"

_voice = None

def get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        model_path = MODELS_DIR / f"{MODEL_NAME}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper model not found at {model_path}. "
                f"Download it from HuggingFace rhasspy/piper-voices."
            )
        _voice = PiperVoice.load(str(model_path))
    return _voice

def synthesize(text: str, output_path: str) -> None:
    import wave
    import time
    voice = get_voice()
    t0 = time.time()
    with wave.open(output_path, "w") as wav_file:
        voice.synthesize_wav(text, wav_file)
    print(f"[tts] synthesized {len(text)} chars in {(time.time()-t0)*1000:.0f}ms")
