# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import glob as glob_module
import ipaddress
import os
import subprocess
import time
import urllib.parse
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIMEZONE = os.environ.get("TIMEZONE", "").strip()

_CLIO_DIR = Path(__file__).parent.parent
_WORK_DIR = _CLIO_DIR.parent

from .jobs import job_manager
from .browser import (
    browser_open as _browser_open,
    browser_navigate as _browser_navigate,
    browser_click as _browser_click,
    browser_type as _browser_type,
    browser_screenshot as _browser_screenshot,
    browser_get_content as _browser_get_content,
    browser_get_elements as _browser_get_elements,
    browser_close as _browser_close,
)

MEMORY_PATH = Path(__file__).parent.parent / "memory.md"
RESTART_CONTEXT_PATH = Path(__file__).parent / "restart_context.txt"

# Tools that execute without asking the user
AUTO_APPROVE = {
    "read_file", "list_directory", "search_code", "find_files",
    "update_memory", "update_scratchpad",
    "web_search", "get_current_time", "read_url", "close_connection",
    "check_job", "stop_job", "list_jobs",
    "browser_get_content", "browser_get_elements", "browser_screenshot",
    "browser_navigate", "browser_open",
    "display_get_state", "display_list_actions", "display_invoke_action",
}

# Tools withheld from Clio entirely (filtered out of TOOL_DEFINITIONS below).
# read_file/list_directory/search_code/find_files/write_file/edit_file/
# delete_file were briefly retired in favor of routing everything through
# bash_command and the terminal pane (2026-09-08) — reverted 2026-09-22 after
# testing showed terminal-only file navigation was noticeably less smooth
# than the dedicated tools. The terminal pane stays as one tool among many,
# not the sole file-access path; see the project-clio memory. Nothing is
# currently disabled, but the mechanism is kept for whatever's next.
DISABLED_TOOLS = set()

# The three tools that reach Clio's display action surface (getState /
# listActions / invokeAction over the display WebSocket link). Handled
# directly in agent.py — real async I/O over a socket, not a plain sync
# call — so they're absent from execute_tool's dispatch below.
DISPLAY_TOOLS = {"display_get_state", "display_list_actions", "display_invoke_action"}

# bash_command reaches the shared interactive terminal session (server/
# terminal_session.py) instead of a one-shot subprocess.run — same reasoning
# as DISPLAY_TOOLS: real async I/O against a live PTY, not a plain sync call,
# so it's handled directly in agent.py and absent from execute_tool below.
TERMINAL_SESSION_TOOLS = {"bash_command"}

# Which pane (existing or planned) represents each tool visually, or None if a
# pane will never make sense for it — nothing to render, no persistent object,
# no benefit from a visual surface. Kept separate from ALL_TOOL_DEFINITIONS
# because that list is sent to the Anthropic API verbatim as the tool schema —
# it rejects any field it doesn't recognize, which is exactly how the "pane"
# key ended up in here in the first place instead of inline on each tool.
TOOL_PANES = {
    "read_file": None,
    "list_directory": None,
    "search_code": None,
    "find_files": None,
    "write_file": "file-manager",
    "edit_file": "file-manager",
    "bash_command": "terminal",
    "run_background": "terminal",
    "check_job": "terminal",
    "stop_job": "terminal",
    "list_jobs": "terminal",
    "delete_file": "file-manager",
    "restart_server": None,
    "update_memory": "document",
    "get_current_time": None,
    "web_search": "browser",
    "read_url": "browser",
    "update_scratchpad": "ambient",
    "browser_open": "browser",
    "browser_navigate": "browser",
    "browser_click": "browser",
    "browser_type": "browser",
    "browser_screenshot": "browser",
    "browser_get_content": "browser",
    "browser_get_elements": "browser",
    "browser_close": "browser",
    "close_connection": None,
    "display_get_state": None,
    "display_list_actions": None,
    "display_invoke_action": None,
}

ALL_TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Optionally specify start_line and end_line "
            "to read a specific range (1-indexed, inclusive). Line numbers are included "
            "in output when reading a range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to the file."},
                "start_line": {"type": "integer", "description": "First line to read (1-indexed). Omit to read from the beginning."},
                "end_line": {"type": "integer", "description": "Last line to read (1-indexed, inclusive). Omit to read to the end."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search for a pattern across files using grep. Returns matching lines with "
            "file paths and line numbers. Use file_pattern to restrict to specific file "
            "types (e.g. '*.py', '*.ts'). Excludes .venv, __pycache__, node_modules, "
            ".git, dist, and build by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path": {"type": "string", "description": "Directory to search in. Defaults to ~/claude."},
                "file_pattern": {"type": "string", "description": "Glob pattern to filter files, e.g. '*.py'. Optional."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "find_files",
        "description": (
            "Find files matching a glob pattern, searched recursively. "
            "Excludes .venv, __pycache__, node_modules, .git, dist, and build. "
            "Examples: '*.py', 'agent*.py', '*.ts'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob filename pattern, e.g. '*.py' or 'main.*'."},
                "path": {"type": "string", "description": "Directory to search in. Defaults to ~/claude."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write the file to."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace a specific string in a file with a new string. Tries an exact match "
            "first; if not found, retries with trailing whitespace normalized per line. "
            "Use write_file for changes touching more than ~20 lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {"type": "string", "description": "The string to find and replace."},
                "new_string": {"type": "string", "description": "The replacement string."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "bash_command",
        "description": (
            "Run a shell command in the shared interactive terminal, visible live on the "
            "display's terminal pane. Use for building, testing, installing packages, "
            "git operations, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
                "cwd": {"type": "string", "description": "Working directory. Defaults to ~/claude."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_background",
        "description": (
            "Run a long-running shell command in the background — dev servers, watchers, "
            "build processes. Returns a job ID. Use check_job to read its output, "
            "stop_job to kill it. Use this instead of bash_command for anything that "
            "runs indefinitely."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
                "cwd": {"type": "string", "description": "Working directory. Defaults to ~/claude."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "check_job",
        "description": "Check the status and recent output of a background job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID returned by run_background."},
                "lines": {"type": "integer", "description": "Number of recent output lines to return. Defaults to 30."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "stop_job",
        "description": "Stop a running background job by its job ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID to stop."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_jobs",
        "description": "List all background jobs and their current status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to delete."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "restart_server",
        "description": (
            "Restart the Clio server process. Use this after modifying your own source code "
            "so the changes take effect. The phone will briefly disconnect and auto-reconnect."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": (
                        "What you were just working on and what comes next — shown back to "
                        "you automatically right after the restart so you can resume without "
                        "asking the user to repeat themselves. This is separate from and in "
                        "addition to update_memory, which is for facts worth keeping across "
                        "many future sessions, not just this restart."
                    ),
                },
            },
            "required": ["context"],
        },
    },
    {
        "name": "update_memory",
        "description": (
            "Update your persistent memory file. Use this to remember important facts about "
            "the user, their projects, preferences, or anything worth recalling in future "
            "sessions. The entire file is replaced, so always include everything you want to keep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The full new content of the memory file."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "get_current_time",
        "description": (
            "Get the current local date and time. Use when the user asks what time it is, "
            "asks about timing or schedules, or when knowing the current time matters."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo and return titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_url",
        "description": (
            "Fetch a webpage and return its readable text content. Good for documentation, "
            "Stack Overflow, GitHub, and most dev resources. May not work on heavy "
            "JavaScript-rendered pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "update_scratchpad",
        "description": (
            "Update your working memory for this session. Track your current task, files "
            "modified, decisions made, and next steps. Rewrite the full scratchpad each time. "
            "Not persisted — resets when the session ends."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The full new content of the scratchpad."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "browser_open",
        "description": (
            "Open a new browser session. Optionally navigate to a URL on open. "
            "Use headless=true for background tasks, headless=false to show the browser visually on the server display."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to navigate to after opening."},
                "headless": {"type": "boolean", "description": "Run headless (no visible window). Defaults to true."},
            },
            "required": [],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Navigate the current browser page to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": (
            "Click an element on the current page. "
            "Selector can be a CSS selector, XPath, or visible text/label of the element."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector, XPath, or visible text of the element to click."},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_type",
        "description": (
            "Type text into an input field on the current page. "
            "Selector can be a CSS selector, placeholder text, or field label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector, placeholder, or label of the input field."},
                "text": {"type": "string", "description": "Text to type into the field."},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": (
            "Take a screenshot of the current browser page and return it as an image "
            "so you can visually analyze what is on the screen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "browser_get_content",
        "description": (
            "Get the readable text content of the current browser page — stripped of HTML noise. "
            "Use this to read articles, search results, documentation, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "browser_get_elements",
        "description": (
            "List the interactive elements on the current page: buttons, links, and input fields. "
            "Use this to understand what actions are available before clicking or typing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "browser_close",
        "description": "Close the current browser session.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "close_connection",
        "description": (
            "Close the WebSocket connection to the phone client. Use this to test that "
            "the client reconnects cleanly. The client should automatically reconnect within "
            "a few seconds."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "display_get_state",
        "description": (
            "Get a snapshot of Clio's display. The \"visible\" field is the one fact "
            "that answers \"what is the user looking at right now\" — it's either "
            "\"overview\" (the grid of all panes, shown by default) or the id of "
            "one pane. A pane existing, or having been focused before, does NOT mean "
            "it's open — only visible == that pane's id means the user is actually "
            "looking at it. If visible is \"overview\" and the user asks about a "
            "specific pane, it is NOT open; use display_invoke_action with "
            "\"shell.focusPane\" to bring it into view before saying otherwise. Also "
            "returns a one-line summary of each pane. Use this before acting on the "
            "display, or when the user refers to something on their screen. Errors if "
            "the display isn't currently open."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "display_list_actions",
        "description": (
            "List every action currently invocable on the display: two shell-level "
            "actions (\"shell.openOverview\" to show the overview, \"shell.focusPane\" to "
            "bring one pane into view — takes a paneId argument, from display_get_state) "
            "plus every pane's own actions, namespaced as \"<pane>.<action>\" (e.g. "
            "\"file-manager.open\"). Call this to see exactly what's available before "
            "calling display_invoke_action — don't guess an action name."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "display_invoke_action",
        "description": (
            "Invoke one action on the display by its exact name, from "
            "display_list_actions (e.g. \"file-manager.open\", or \"shell.focusPane\" with "
            "args {\"paneId\": \"file-manager\"}). Use this to act on whatever the display "
            "is currently showing, or to bring a pane into view — invoking a pane's own "
            "action automatically brings that pane into view first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Namespaced action name, e.g. \"file-manager.open\"."},
                "args": {"type": "object", "description": "Arguments for the action, if it takes any."},
            },
            "required": ["name"],
        },
    },
]

# What Clio is actually offered — ALL_TOOL_DEFINITIONS with DISABLED_TOOLS filtered out.
TOOL_DEFINITIONS = [t for t in ALL_TOOL_DEFINITIONS if t["name"] not in DISABLED_TOOLS]


def execute_tool(name: str, inputs: dict):
    try:
        if name == "read_file":
            return _read_file(inputs["path"], inputs.get("start_line"), inputs.get("end_line"))
        elif name == "list_directory":
            return _list_directory(inputs["path"])
        elif name == "search_code":
            return _search_code(inputs["pattern"], inputs.get("path", ""), inputs.get("file_pattern", ""))
        elif name == "find_files":
            return _find_files(inputs["pattern"], inputs.get("path", ""))
        elif name == "write_file":
            return _write_file(inputs["path"], inputs["content"])
        elif name == "edit_file":
            return _edit_file(inputs["path"], inputs["old_string"], inputs["new_string"])
        elif name == "run_background":
            return _run_background(inputs["command"], inputs.get("cwd"))
        elif name == "check_job":
            return job_manager.check(inputs["job_id"], inputs.get("lines", 30))
        elif name == "stop_job":
            return job_manager.stop(inputs["job_id"])
        elif name == "list_jobs":
            return job_manager.list_jobs()
        elif name == "delete_file":
            return _delete_file(inputs["path"])
        elif name == "browser_open":
            return _browser_open(inputs.get("url", ""), inputs.get("headless", True))
        elif name == "browser_navigate":
            return _browser_navigate(inputs["url"])
        elif name == "browser_click":
            return _browser_click(inputs["selector"])
        elif name == "browser_type":
            return _browser_type(inputs["selector"], inputs["text"])
        elif name == "browser_screenshot":
            return _browser_screenshot()
        elif name == "browser_get_content":
            return _browser_get_content()
        elif name == "browser_get_elements":
            return _browser_get_elements()
        elif name == "browser_close":
            return _browser_close()
        elif name == "get_current_time":
            return _get_current_time()
        elif name == "web_search":
            return _web_search(inputs["query"])
        elif name == "read_url":
            return _read_url(inputs["url"])
        elif name == "update_memory":
            return _update_memory(inputs["content"])
        elif name == "restart_server":
            return _restart_server(inputs["context"])
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


def describe_tool_call(name: str, inputs: dict) -> str:
    if name == "bash_command":
        cwd = inputs.get("cwd", "")
        return f"run `{inputs['command']}`" + (f" in {cwd}" if cwd else "")
    elif name == "write_file":
        return f"write {inputs['path']}"
    elif name == "edit_file":
        return f"edit {inputs['path']}"
    elif name == "read_file":
        start = inputs.get("start_line")
        end = inputs.get("end_line")
        if start or end:
            return f"read {inputs['path']} lines {start or 1}–{end or 'end'}"
        return f"read {inputs['path']}"
    elif name == "list_directory":
        return f"list {inputs['path']}"
    elif name == "search_code":
        path = inputs.get("path", "")
        return f"search `{inputs['pattern']}`" + (f" in {path}" if path else "")
    elif name == "find_files":
        path = inputs.get("path", "")
        return f"find {inputs['pattern']}" + (f" in {path}" if path else "")
    elif name == "run_background":
        cwd = inputs.get("cwd", "")
        return f"run in background: `{inputs['command']}`" + (f" in {cwd}" if cwd else "")
    elif name == "check_job":
        return f"check job {inputs['job_id']}"
    elif name == "stop_job":
        return f"stop job {inputs['job_id']}"
    elif name == "list_jobs":
        return "list background jobs"
    elif name == "delete_file":
        return f"delete {inputs['path']}"
    elif name == "get_current_time":
        return "get current time"
    elif name == "web_search":
        return f"search \"{inputs['query']}\""
    elif name == "read_url":
        return f"read {inputs['url']}"
    elif name == "update_memory":
        return "update memory"
    elif name == "restart_server":
        return "restart server"
    elif name == "update_scratchpad":
        return "update scratchpad"
    elif name == "browser_open":
        url = inputs.get("url", "")
        mode = "headless" if inputs.get("headless", True) else "visible"
        return f"open browser ({mode})" + (f" → {url}" if url else "")
    elif name == "browser_navigate":
        return f"navigate to {inputs['url']}"
    elif name == "browser_click":
        return f"click \"{inputs['selector']}\""
    elif name == "browser_type":
        return f"type into \"{inputs['selector']}\""
    elif name == "browser_screenshot":
        return "take screenshot"
    elif name == "browser_get_content":
        return "get page content"
    elif name == "browser_get_elements":
        return "get page elements"
    elif name == "browser_close":
        return "close browser"
    elif name == "close_connection":
        return "close connection"
    elif name == "display_get_state":
        return "check the display"
    elif name == "display_list_actions":
        return "list display actions"
    elif name == "display_invoke_action":
        return f"invoke \"{inputs.get('name', '?')}\" on the display"
    return name


def summarize_tool_result(name: str, result) -> str:
    # File diff results are dicts with a "_diff" key
    if isinstance(result, dict) and "_diff" in result:
        text = result["text"]
        if text.startswith("Error:"):
            return text[:80]
        return "File written" if name == "write_file" else "Edit applied"
    # browser_screenshot returns a dict (image content block)
    if isinstance(result, dict):
        return "Screenshot taken"
    if result.startswith("Error:"):
        return result[:80]
    if name == "read_file":
        lines = result.count("\n") + 1
        return f"Read {lines} lines"
    elif name == "list_directory":
        items = len(result.strip().split("\n")) if result.strip() else 0
        return f"Found {items} items"
    elif name == "search_code":
        lines = result.strip().split("\n") if result.strip() else []
        if not lines or result == "No matches found.":
            return "No matches"
        return f"{len(lines)} match{'es' if len(lines) != 1 else ''}"
    elif name == "find_files":
        lines = result.strip().split("\n") if result.strip() else []
        if not lines or result == "No files found.":
            return "No files found"
        return f"{len(lines)} file{'s' if len(lines) != 1 else ''}"
    elif name == "write_file":
        return "File written"
    elif name == "edit_file":
        return "Edit applied"
    elif name == "run_background":
        return result
    elif name == "check_job":
        first_line = result.splitlines()[0] if result else "No output"
        return first_line[:80]
    elif name == "stop_job":
        return result
    elif name == "list_jobs":
        return result[:80]
    elif name == "delete_file":
        return "File deleted"
    elif name == "get_current_time":
        return result
    elif name == "web_search":
        lines = result.strip().split("\n")
        return f"{len(lines)} result lines"
    elif name == "read_url":
        return f"Read {len(result)} chars"
    elif name == "update_memory":
        return "Memory updated"
    elif name == "restart_server":
        return "Restarting..."
    elif name == "update_scratchpad":
        return "Scratchpad updated"
    elif name == "browser_open":
        return result
    elif name == "browser_navigate":
        return result[:80]
    elif name == "browser_click":
        return result[:80]
    elif name == "browser_type":
        return result[:80]
    elif name == "browser_screenshot":
        return "Screenshot taken"
    elif name == "browser_get_content":
        lines = result.strip().split("\n")
        return f"Got {len(lines)} lines"
    elif name == "browser_get_elements":
        lines = result.strip().split("\n") if result.strip() else []
        return f"Found {len(lines)} elements"
    elif name == "browser_close":
        return result
    elif name == "close_connection":
        return "Connection closed"
    return "Done"


READ_LIMIT = 50_000
_SEARCH_IGNORE_DIRS = {".venv", "__pycache__", "node_modules", ".git", "dist", "build"}

_SENSITIVE_PREFIXES = [
    Path.home() / ".ssh",
    Path.home() / ".aws",
    Path.home() / ".gnupg",
    Path.home() / ".config" / "gcloud",
]


def _is_safe_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    for sensitive in _SENSITIVE_PREFIXES:
        try:
            resolved.relative_to(sensitive.resolve())
            return False
        except ValueError:
            pass
    return True


def _read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return f"Error: access to {path} is not allowed"
    content = p.read_text()

    if start_line is not None or end_line is not None:
        lines = content.splitlines(keepends=True)
        start = max(0, (start_line or 1) - 1)
        end = min(len(lines), end_line or len(lines))
        selected = lines[start:end]
        numbered = [f"{start + i + 1}: {line}" for i, line in enumerate(selected)]
        result = "".join(numbered)
        if len(result) > READ_LIMIT:
            return result[:READ_LIMIT] + "\n[truncated]"
        return result

    if len(content) > READ_LIMIT:
        return content[:READ_LIMIT] + f"\n\n[truncated — file is {len(content)} chars, showing first {READ_LIMIT}]"
    return content


def _list_directory(path: str) -> str:
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return f"Error: access to {path} is not allowed"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
    lines = []
    for entry in entries:
        try:
            entry.name.encode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        prefix = "  " if entry.is_file() else "/"
        lines.append(f"{prefix}{entry.name}")
    return "\n".join(lines)


def _search_code(pattern: str, path: str = "", file_pattern: str = "") -> str:
    search_path = str(Path(path).expanduser() if path else _WORK_DIR)
    cmd = ["grep", "-rn", "--color=never", "-E"]
    if file_pattern:
        cmd.extend(["--include", file_pattern])
    for d in _SEARCH_IGNORE_DIRS:
        cmd.extend(["--exclude-dir", d])
    cmd.extend([pattern, search_path])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = result.stdout
    if not output:
        if result.stderr:
            return f"Error: {result.stderr.strip()}"
        return "No matches found."

    lines = output.splitlines()
    if len(lines) > 100:
        output = "\n".join(lines[:100]) + f"\n\n[truncated — showing 100 of {len(lines)} matches]"
    return output.strip()


def _find_files(pattern: str, path: str = "") -> str:
    search_path = Path(path).expanduser() if path else _WORK_DIR
    matches = glob_module.glob(str(search_path / "**" / pattern), recursive=True)
    filtered = [
        m for m in matches
        if not _SEARCH_IGNORE_DIRS.intersection(set(Path(m).parts))
    ]
    if not filtered:
        return "No files found."
    return "\n".join(sorted(filtered)[:500])


def _write_file(path: str, content: str) -> dict:
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return {"_diff": False, "text": f"Error: access to {path} is not allowed"}
    old_content = p.read_text() if p.exists() else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {
        "_diff": True,
        "path": str(p),
        "old": old_content,
        "new": content,
        "text": f"Wrote {len(content)} bytes to {path}",
    }


def _edit_file(path: str, old_string: str, new_string: str) -> dict:
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return {"_diff": False, "text": f"Error: access to {path} is not allowed"}
    content = p.read_text()

    # Exact match
    if old_string in content:
        new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content)
        return {"_diff": True, "path": str(p), "old": content, "new": new_content, "text": f"Replaced in {path}"}

    # Fuzzy: normalize trailing whitespace per line and retry
    content_lines = content.splitlines(keepends=True)
    old_lines = old_string.splitlines()
    if not old_lines:
        return {"_diff": False, "text": "Error: old_string is empty"}

    n = len(old_lines)
    norm_old = [line.rstrip() for line in old_lines]

    for i in range(len(content_lines) - n + 1):
        block = content_lines[i:i + n]
        norm_block = [line.rstrip("\n").rstrip("\r").rstrip() for line in block]
        if norm_block == norm_old:
            before = "".join(content_lines[:i])
            after = "".join(content_lines[i + n:])
            new_content = before + new_string + after
            p.write_text(new_content)
            return {"_diff": True, "path": str(p), "old": content, "new": new_content, "text": f"Replaced in {path}"}

    # Helpful error indicating whether the first line exists at all
    first_line = old_lines[0].strip()
    for line in content.splitlines():
        if first_line and first_line in line:
            return {
                "_diff": False,
                "text": (
                    f"Error: string not found in {path} — the first line was found but "
                    f"surrounding context didn't match. Read the file and use the exact text."
                ),
            }
    return {"_diff": False, "text": f"Error: string not found in {path} — read the file first to get the exact text"}


def _run_background(command: str, cwd: str | None = None) -> str:
    job_id = job_manager.start(command, cwd)
    return f"Started job {job_id}. Use check_job to see output."


def _delete_file(path: str) -> str:
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return f"Error: access to {path} is not allowed"
    p.unlink()
    return f"Deleted {path}"


def _update_memory(content: str) -> str:
    MEMORY_PATH.write_text(content)
    return f"Memory updated ({len(content)} bytes)"


def _web_search(query: str) -> str:
    from bs4 import BeautifulSoup
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Clio/1.0)"}
    resp = requests.post(url, data=params, timeout=10, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for result in soup.select(".result__body")[:6]:
        title_el = result.select_one(".result__title")
        snippet_el = result.select_one(".result__snippet")
        url_el = result.select_one(".result__url")
        title = title_el.get_text(strip=True) if title_el else ""
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        link = url_el.get_text(strip=True) if url_el else ""
        if title or snippet:
            results.append(f"{title}\n{snippet}\n{link}".strip())

    if not results:
        return "No results found."
    return "\n\n".join(results)


def _is_private_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        if host.lower() in ("localhost",):
            return True
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _read_url(url: str) -> str:
    if _is_private_url(url):
        return "Error: fetching private or local addresses is not allowed."
    from bs4 import BeautifulSoup
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Clio/1.0)"}
    resp = requests.get(url, timeout=15, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    if len(cleaned) > READ_LIMIT:
        return cleaned[:READ_LIMIT] + f"\n\n[truncated — showing first {READ_LIMIT} chars]"
    return cleaned


def _get_current_time() -> str:
    try:
        tz = ZoneInfo(_TIMEZONE) if _TIMEZONE else None
    except ZoneInfoNotFoundError:
        tz = None
    now = datetime.now(tz)
    return now.strftime("%A, %B %d, %Y at %I:%M %p")


def _restart_server(context: str) -> str:
    import threading
    RESTART_CONTEXT_PATH.write_text(context)
    threading.Timer(1.0, lambda: os._exit(1)).start()
    return "Restarting server. Reconnect in a few seconds."
