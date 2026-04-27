import subprocess
import uuid
from pathlib import Path

TMP_DIR = Path(__file__).parent / "tmp"


class BackgroundJobManager:
    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def start(self, command: str, cwd: str | None = None) -> str:
        work_dir = Path(cwd).expanduser() if cwd else Path.home() / "claude"
        TMP_DIR.mkdir(exist_ok=True)

        job_id = uuid.uuid4().hex[:8]
        log_path = TMP_DIR / f"job_{job_id}.log"

        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(work_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )

        self._jobs[job_id] = {
            "process": proc,
            "log_path": log_path,
            "command": command,
            "cwd": str(work_dir),
        }
        return job_id

    def check(self, job_id: str, lines: int = 30) -> str:
        job = self._jobs.get(job_id)
        if not job:
            return f"No job with ID {job_id}."

        proc = job["process"]
        poll = proc.poll()
        status = "running" if poll is None else f"exited (code {poll})"

        try:
            content = job["log_path"].read_text()
            all_lines = content.splitlines()
            tail = "\n".join(all_lines[-lines:]) if all_lines else "(no output yet)"
        except OSError:
            tail = "(log not available)"

        return f"Job {job_id} [{status}] — `{job['command']}`:\n{tail}"

    def stop(self, job_id: str) -> str:
        job = self._jobs.get(job_id)
        if not job:
            return f"No job with ID {job_id}."

        proc = job["process"]
        if proc.poll() is not None:
            self._cleanup_job(job_id)
            return f"Job {job_id} had already exited (code {proc.poll()})."

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self._cleanup_job(job_id)
        return f"Job {job_id} stopped."

    def list_jobs(self) -> str:
        if not self._jobs:
            return "No background jobs."
        lines = []
        for job_id, job in self._jobs.items():
            poll = job["process"].poll()
            status = "running" if poll is None else f"exited ({poll})"
            cmd = job["command"][:60]
            lines.append(f"{job_id}: `{cmd}` [{status}]")
        return "\n".join(lines)

    def cleanup_exited(self):
        """Remove exited jobs and delete their log files."""
        done = [jid for jid, job in self._jobs.items() if job["process"].poll() is not None]
        for jid in done:
            self._cleanup_job(jid)

    def _cleanup_job(self, job_id: str):
        job = self._jobs.pop(job_id, None)
        if job:
            try:
                job["log_path"].unlink(missing_ok=True)
            except OSError:
                pass


job_manager = BackgroundJobManager()
