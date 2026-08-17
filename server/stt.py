# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

# stt.py — speech-to-text using faster-whisper
import os
import re
from pathlib import Path
from faster_whisper import WhisperModel

# Whisper model to use for speech recognition.
# Options: tiny, base, small, medium, large
# tiny   — fastest, least accurate
# base   — fast, reasonable accuracy (previous default)
# small  — good balance of speed and accuracy (recommended)
# medium — more accurate, noticeably slower
# large  — most accurate, slowest; not recommended on CPU
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
MODELS_DIR = Path(__file__).parent / "models"
NO_SPEECH_THRESHOLD = 0.75  # discard segments where Whisper thinks there's no speech
AVG_LOGPROB_THRESHOLD = -1.0  # discard segments Whisper isn't confident it decoded correctly —
                               # hallucinated filler often has low no_speech_prob (Whisper is
                               # sure it heard *something*) but poor decode confidence

# Optional language hint — set STT_LANGUAGE in config.sh (e.g. "en") to prevent
# Whisper from misidentifying your speech as another language.
STT_LANGUAGE = os.environ.get("STT_LANGUAGE") or None

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

def reset_last_transcript():
    """Call at the start of a new session to prevent cross-session priming hallucinations."""
    global _last_transcript
    _last_transcript = ""

def _normalize_words(text: str) -> list:
    return [w for w in re.sub(r"[^\w\s']", " ", text.lower()).split() if w]

def _is_echo_of(transcript: str, reference: str, max_words: int = 6) -> bool:
    """True if `transcript` is a short duplicate of the start of `reference`.

    Whisper occasionally hallucinates on ambiguous/near-silent audio right after a
    turn ends, regenerating a short garbled variant of whatever was just said —
    either the user's own last line or Clio's spoken reply — instead of returning
    silence. A short new transcript whose words exactly match the start of the
    previous line is almost certainly one of these, not new speech.
    """
    if not transcript or not reference:
        return False
    t_words = _normalize_words(transcript)
    if not t_words or len(t_words) > max_words:
        return False
    r_words = _normalize_words(reference)
    return t_words == r_words[:len(t_words)]

def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, compute_type="int8", download_root=str(MODELS_DIR))
    return _model

def transcribe(audio_path: str, last_assistant_text: str = "") -> str:
    """Transcribe audio to text.

    `last_assistant_text` is Clio's own most recent spoken reply — passed in so
    a hallucinated echo of it can be told apart from real user speech.
    """
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
            language=STT_LANGUAGE,         # None = auto-detect; set STT_LANGUAGE in config.sh to force a language
        )
        texts = [
            s.text for s in segments
            if s.no_speech_prob < NO_SPEECH_THRESHOLD
            and s.avg_logprob > AVG_LOGPROB_THRESHOLD
            and not any(phrase in s.text.lower() for phrase in _HALLUCINATION_BLOCKLIST)
        ]
        print(f"[stt] transcribed in {(time.time()-t0)*1000:.0f}ms")
        transcript = " ".join(texts).strip()
        transcript = transcript.replace("Cleo", "Clio").replace("CLEO", "CLIO").replace("cleo", "clio")
        # Fix Whisper mishearing "Hi, Clio" as "High Clio"
        transcript = re.sub(r'\bHigh Clio\b', 'Hi, Clio', transcript, flags=re.IGNORECASE)
        # Strip hallucinated leading "Hello" only when followed by more content
        transcript = re.sub(r'^Hello[,.]?\s+(?=\S)', '', transcript).strip()
        transcript = re.sub(r'^[^\w\s]+\s*', '', transcript).strip()
        if transcript:
            transcript = transcript[0].upper() + transcript[1:]

        # Detect priming hallucination: if transcript starts with the previous
        # transcript, Whisper is anchoring on a prior phrase — strip the repeated part.
        if _last_transcript and transcript.lower().startswith(_last_transcript.lower()):
            transcript = transcript[len(_last_transcript):].strip()
            if transcript:
                transcript = transcript[0].upper() + transcript[1:]
        # Reverse case: a short new transcript that's itself just the start of the
        # previous line (ours or Clio's spoken reply) — pure echo, not partial speech.
        elif _is_echo_of(transcript, _last_transcript) or _is_echo_of(transcript, last_assistant_text):
            transcript = ""

        if transcript:
            _last_transcript = transcript
        return transcript
    except Exception:
        return ""
