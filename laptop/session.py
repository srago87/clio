import uuid
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"

class VoiceSession:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.start_time = datetime.now()
        self.exchange_count = 0

        LOGS_DIR.mkdir(exist_ok=True)
        timestamp = self.start_time.strftime("%Y-%m-%d_%H-%M-%S")
        self.log_path = LOGS_DIR / f"session_{timestamp}.md"
        self._write_header()

    def _write_header(self):
        with open(self.log_path, "w") as f:
            f.write(f"# Claude Voice Session\n")
            f.write(f"**Date**: {self.start_time.strftime('%Y-%m-%d')}\n")
            f.write(f"**Time**: {self.start_time.strftime('%H:%M:%S')}\n\n---\n\n")

    def add_exchange(self, user_text: str, claude_text: str):
        self.exchange_count += 1
        now = datetime.now().strftime("%H:%M:%S")
        with open(self.log_path, "a") as f:
            f.write(f"**[{now}] You**\n{user_text}\n\n")
            f.write(f"**[{now}] Claude**\n{claude_text}\n\n---\n\n")

    def end(self):
        end_time = datetime.now().strftime("%H:%M:%S")
        with open(self.log_path, "a") as f:
            f.write(f"*Session ended: {end_time} | Exchanges: {self.exchange_count}*\n")
