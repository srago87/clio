# stt.py — speech-to-text using faster-whisper
from pathlib import Path
from faster_whisper import WhisperModel

WHISPER_MODEL = "base"
MODELS_DIR = Path(__file__).parent / "models"
NO_SPEECH_THRESHOLD = 0.75  # discard segments where Whisper thinks there's no speech

# Whisper was trained on YouTube transcripts and hallucinates these phrases on
# short/ambiguous audio, usually with spuriously high confidence.
_HALLUCINATION_BLOCKLIST = [
    "thanks for watching",
    "thank you for watching",
    "don't forget to subscribe",
    "please subscribe",
    "like and subscribe",
    "hit the like button",
    "hit the bell",
    "see you in the next video",
    "see you next time",
    "in the next video",
    "subscribe to my channel",
    "smash the like button",
    "turn on notifications",
    "comment down below",
    "check out my other videos",
]

_model = None
_last_transcript: str = ""  # tracks previous transcript to detect priming hallucinations

def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, compute_type="int8", download_root=str(MODELS_DIR))
    return _model

def transcribe(audio_path: str) -> str:
    global _last_transcript
    import time
    try:
        model = get_model()
        t0 = time.time()
        segments, _ = model.transcribe(
            audio_path,
            beam_size=1,
            vad_filter=True,               # strip non-speech (breathing, silence) before transcription
            condition_on_previous_text=False,  # reduce hallucinations
        )
        texts = [
            s.text for s in segments
            if s.no_speech_prob < NO_SPEECH_THRESHOLD
            and not any(phrase in s.text.lower() for phrase in _HALLUCINATION_BLOCKLIST)
        ]
        print(f"[stt] transcribed in {(time.time()-t0)*1000:.0f}ms")
        transcript = " ".join(texts).strip()
        transcript = transcript.replace("Cleo", "Clio").replace("CLEO", "CLIO").replace("cleo", "clio")
        import re
        # Fix Whisper mishearing "Hi, Clio" as "High Clio"
        transcript = re.sub(r'\bHigh Clio\b', 'Hi, Clio', transcript, flags=re.IGNORECASE)
        # Strip hallucinated leading "Hello" only when followed by more content
        transcript = re.sub(r'^Hello[,.]?\s+(?=\S)', '', transcript).strip()
        transcript = re.sub(r'^[^\w\s]+\s*', '', transcript).strip()
        if transcript:
            transcript = transcript[0].upper() + transcript[1:]
        return transcript
    except Exception:
        return ""
