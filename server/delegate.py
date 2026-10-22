# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""Delegating tasks to headless Claude Code instances.

Each delegation runs `claude -p` with stream-json output. Every event it
emits is mirrored into the shared terminal's history as it happens, so the
delegated work is watchable rather than a black box. When a run ends, a
notice is queued on `completions`; main.py turns that into a proactive Clio
turn so the result gets spoken without the user having to ask."""

import asyncio
import json
import os
import uuid
from pathlib import Path

from .terminal_session import terminal_session

_WORK_DIR = Path(__file__).parent.parent.parent

# Delegated instances can edit files freely but only run this short list of
# shell commands — anything else is denied and the instance reports that it
# was blocked, rather than stalling on a permission prompt nobody can answer.
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(npm test:*)", "Bash(npm run build:*)", "Bash(npm run lint:*)",
    "Bash(pytest:*)", "Bash(python -m pytest:*)",
]

RESULT_LIMIT = 4000


class DelegationManager:
    def __init__(self):
        self._runs: dict[str, dict] = {}
        self.completions: asyncio.Queue[str] = asyncio.Queue()

    def start(self, task: str, cwd: str | None = None) -> str:
        work_dir = Path(cwd).expanduser() if cwd else _WORK_DIR
        if not work_dir.is_dir():
            return f"Error: {work_dir} is not a directory."

        run_id = uuid.uuid4().hex[:6]
        self._runs[run_id] = {
            "task": task,
            "cwd": str(work_dir),
            "session_id": None,
            "status": "running",
            "result": "",
            "cost_usd": 0.0,
            "activity": [],
            "process": None,
        }
        asyncio.create_task(self._run(run_id, task, resume=False))
        return f"Delegated as {run_id}. You'll be told when it finishes."

    def follow_up(self, run_id: str, message: str) -> str:
        run = self._runs.get(run_id)
        if not run:
            return f"No delegation with ID {run_id}."
        if run["status"] == "running":
            return f"Delegation {run_id} is still running — wait for it to finish first."
        if not run["session_id"]:
            return f"Delegation {run_id} has no session to resume."
        run["status"] = "running"
        run["result"] = ""
        asyncio.create_task(self._run(run_id, message, resume=True))
        return f"Sent follow-up to {run_id}. You'll be told when it finishes."

    def check(self, run_id: str, lines: int = 15) -> str:
        run = self._runs.get(run_id)
        if not run:
            return f"No delegation with ID {run_id}."
        recent = "\n".join(run["activity"][-lines:]) or "(no activity yet)"
        text = f"Delegation {run_id} [{run['status']}] in {run['cwd']}\nTask: {run['task']}\nRecent activity:\n{recent}"
        if run["result"]:
            text += f"\nResult:\n{run['result']}"
        return text

    def stop(self, run_id: str) -> str:
        run = self._runs.get(run_id)
        if not run:
            return f"No delegation with ID {run_id}."
        proc = run["process"]
        if run["status"] != "running" or proc is None or proc.returncode is not None:
            return f"Delegation {run_id} isn't running."
        run["status"] = "stopped"
        proc.terminate()
        return f"Delegation {run_id} stopped."

    def list_runs(self) -> str:
        if not self._runs:
            return "No delegations."
        return "\n".join(
            f"{run_id}: {run['task'][:60]} [{run['status']}]"
            for run_id, run in self._runs.items()
        )

    async def _run(self, run_id: str, prompt: str, resume: bool):
        run = self._runs[run_id]
        args = [
            "claude", "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--allowedTools", *ALLOWED_TOOLS,
        ]
        if resume:
            args += ["--resume", run["session_id"]]

        # Without an API key in the environment, the CLI falls back to the
        # user's Claude subscription login instead of billing Clio's key.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        await self._mirror(run_id, f"$ claude {'(follow-up) ' if resume else ''}— {prompt}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=run["cwd"],
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=16 * 1024 * 1024,
            )
        except OSError as e:
            run["status"] = "failed"
            run["result"] = f"Couldn't launch Claude Code: {e}"
            await self._finish(run_id)
            return

        run["process"] = proc
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                await self._mirror(run_id, line)
                continue
            await self._handle_event(run_id, event)

        await proc.wait()
        if run["status"] == "running":
            if proc.returncode != 0 and not run["result"]:
                run["status"] = "failed"
                run["result"] = f"Claude Code exited with code {proc.returncode}."
            else:
                run["status"] = "done"
        await self._finish(run_id)

    async def _handle_event(self, run_id: str, event: dict):
        run = self._runs[run_id]
        etype = event.get("type")

        if etype == "system" and event.get("subtype") == "init":
            run["session_id"] = event.get("session_id")

        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    await self._mirror(run_id, f"→ {block.get('name')} {_brief(block.get('input', {}))}")
                elif block.get("type") == "text" and block.get("text", "").strip():
                    await self._mirror(run_id, block["text"].strip())

        elif etype == "result":
            run["session_id"] = event.get("session_id") or run["session_id"]
            run["cost_usd"] += event.get("total_cost_usd") or 0.0
            run["result"] = (event.get("result") or "")[:RESULT_LIMIT]
            if event.get("is_error") or event.get("subtype") != "success":
                run["status"] = "failed"
                if not run["result"]:
                    run["result"] = f"Claude Code ended with {event.get('subtype')}."

    async def _mirror(self, run_id: str, text: str):
        self._runs[run_id]["activity"].append(text)
        await terminal_session.write_system_line(f"[claude {run_id}] {text}")

    async def _finish(self, run_id: str):
        run = self._runs[run_id]
        await self._mirror(run_id, f"■ {run['status']} (${run['cost_usd']:.2f} total)")
        await self.completions.put(
            f"Delegated task {run_id} finished with status: {run['status']}.\n"
            f"Task: {run['task']}\n"
            f"Claude Code's final report:\n{run['result'] or '(none)'}"
        )


def _brief(inputs: dict) -> str:
    for key in ("command", "file_path", "pattern", "path", "url"):
        if key in inputs:
            return str(inputs[key])[:120]
    return ""


delegation_manager = DelegationManager()


def execute_delegation_tool(name: str, inputs: dict) -> str:
    """Dispatch for DELEGATION_TOOLS. Must be called on the event loop."""
    try:
        if name == "delegate_task":
            return delegation_manager.start(inputs["task"], inputs.get("cwd"))
        elif name == "follow_up_delegation":
            return delegation_manager.follow_up(inputs["delegation_id"], inputs["message"])
        elif name == "check_delegation":
            return delegation_manager.check(inputs["delegation_id"])
        elif name == "stop_delegation":
            return delegation_manager.stop(inputs["delegation_id"])
        elif name == "list_delegations":
            return delegation_manager.list_runs()
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"
