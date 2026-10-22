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
from urllib.parse import urljoin
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
from .computer import (
    computer_screenshot as _computer_screenshot,
    computer_click as _computer_click,
    computer_type as _computer_type,
)
from . import memory_store
from . import gmail

RESTART_CONTEXT_PATH = Path(__file__).parent / "restart_context.txt"

# Tools that execute without asking the user
AUTO_APPROVE = {
    "read_file", "list_directory", "search_code", "find_files",
    "update_memory", "update_scratchpad",
    "web_search", "get_current_time", "read_url", "close_connection",
    "check_job", "stop_job", "list_jobs",
    "check_delegation", "stop_delegation", "list_delegations",
    "browser_get_content", "browser_get_elements", "browser_screenshot",
    "browser_navigate", "browser_open",
    "display_get_state", "display_list_actions", "display_invoke_action",
    "computer_screenshot",
    "delete_memory",
    "email_search", "email_read", "email_list_labels", "email_draft",
}

# Tools withheld from Clio entirely (filtered out of TOOL_DEFINITIONS below).
# read_file/list_directory/search_code/find_files/write_file/edit_file/
# delete_file were briefly retired in favor of routing everything through
# bash_command and the terminal pane (2026-09-08) — reverted 2026-09-22 after
# testing showed terminal-only file navigation was noticeably less smooth
# than the dedicated tools. The shared terminal stays as one tool among many,
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

# Job-management tools don't run in the shared shell, but their results are
# written into its history anyway so they show up in the terminal window
# alongside everything else Clio ran.
TERMINAL_MIRRORED_TOOLS = {"run_background", "check_job", "stop_job", "list_jobs"}

# Delegation tools spawn and track asyncio subprocesses (server/delegate.py),
# so they must run on the event loop — handled directly in agent.py rather
# than in execute_tool's worker thread.
DELEGATION_TOOLS = {
    "delegate_task", "follow_up_delegation", "check_delegation",
    "stop_delegation", "list_delegations",
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
        "description": (
            "Create or overwrite a file with the given content. Broadcasts a before/after "
            "diff to the display for review. Prefer edit_file for a small, targeted change "
            "(a few lines); use write_file when the change touches more than ~20 lines or "
            "the file doesn't exist yet."
        ),
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
            "Broadcasts a before/after diff to the display for review. Use write_file for "
            "changes touching more than ~20 lines."
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
            "Run a shell command in the shared interactive terminal. The user watches it "
            "live in a terminal window that opens on their screen automatically, so pick "
            "flags that show progress (e.g. curl --progress-bar) for anything slow. Use for building, testing, installing packages, "
            "git operations, and other shell-shaped work. For ordinary file reading or "
            "searching, prefer read_file, list_directory, search_code, and find_files "
            "instead — they're smoother and more direct than shelling out for the same thing."
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
        "name": "delegate_task",
        "description": (
            "Hand a self-contained coding task to a separate Claude Code instance that runs "
            "in the background. Use this for substantial multi-file work — implementing a "
            "feature, a refactor, fixing a bug that needs investigation — rather than doing "
            "it yourself step by step. It can read and edit files and run git status/diff/log, "
            "tests and builds, but no other shell commands. Its activity streams into the "
            "terminal window. Returns immediately with an ID; you'll receive a message when "
            "it finishes, so tell the user it's started and don't poll. Write the task as a "
            "complete brief: it can't see this conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Full instructions, including goal, relevant files, and what 'done' means."},
                "cwd": {"type": "string", "description": "Project directory to work in. Defaults to ~/claude."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "follow_up_delegation",
        "description": (
            "Send a follow-up message to a finished delegation, continuing the same Claude "
            "Code session with its full context — e.g. 'also add tests' or answering a "
            "question it asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "delegation_id": {"type": "string", "description": "The ID returned by delegate_task."},
                "message": {"type": "string", "description": "The follow-up instruction."},
            },
            "required": ["delegation_id", "message"],
        },
    },
    {
        "name": "check_delegation",
        "description": "Check a delegation's status, recent activity, and final report if finished.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delegation_id": {"type": "string", "description": "The ID returned by delegate_task."},
            },
            "required": ["delegation_id"],
        },
    },
    {
        "name": "stop_delegation",
        "description": "Stop a running delegation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delegation_id": {"type": "string", "description": "The delegation to stop."},
            },
            "required": ["delegation_id"],
        },
    },
    {
        "name": "list_delegations",
        "description": "List all delegations and their status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
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
            "Save or update one persistent memory note — a fact about the user, a project "
            "decision, feedback on how to work, or a pointer to something external. Memory "
            "is a directory of small typed notes (an Obsidian vault), not one big file: this "
            "replaces only the one note named, leaving every other note untouched. Reuse an "
            "existing name from your memory index to update that note, or invent a new short "
            "lowercase-kebab-case name to create one. Include everything you want that note "
            "to keep — the whole note is replaced, but only that note. Note bodies may use "
            "[[note-name]] to link to other notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lowercase-kebab-case note name, e.g. 'about-the-user' or 'clio-display-status'."},
                "type": {"type": "string", "description": "One of: user, feedback, project, reference."},
                "description": {"type": "string", "description": "One-line summary shown in the memory index."},
                "content": {"type": "string", "description": "The full body of the note, in markdown."},
            },
            "required": ["name", "type", "description", "content"],
        },
    },
    {
        "name": "delete_memory",
        "description": "Delete one memory note by name — use when a note is stale, wrong, or no longer relevant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The note name to delete."},
            },
            "required": ["name"],
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
            "Fetch a webpage and return its readable text content, with each link's URL "
            "shown in parentheses after its text. Good for documentation, download pages, "
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
            "Open a new browser session in a visible window on the user's screen, so they "
            "can watch what you're doing. Optionally navigate to a URL on open. Only pass "
            "headless=true if the user explicitly asks for the browser to stay hidden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to navigate to after opening."},
                "headless": {"type": "boolean", "description": "Run headless (no visible window). Defaults to false."},
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
        "name": "computer_screenshot",
        "description": (
            "Take a screenshot of the real desktop and return it as an image so you can "
            "visually analyze what's on screen. Use only for non-browser desktop apps — "
            "for websites, use browser_screenshot and the other browser_* tools instead, "
            "which give more reliable structured control."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "computer_click",
        "description": (
            "Click at a pixel coordinate on the real desktop — coordinates match a "
            "computer_screenshot image exactly (verified 1:1, no scaling offset). Use only "
            "for non-browser desktop apps; use browser_click for websites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X pixel coordinate, from a computer_screenshot image."},
                "y": {"type": "integer", "description": "Y pixel coordinate, from a computer_screenshot image."},
                "button": {"type": "string", "description": "Which button to click: left, right, or middle. Defaults to left."},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "computer_type",
        "description": (
            "Type text via real keyboard input into whatever's currently focused on the "
            "real desktop. Use only for non-browser desktop apps; use browser_type for "
            "websites. Click the target field first if it isn't already focused."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
            },
            "required": ["text"],
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
            "Get a snapshot of your document viewer: the open document's path and "
            "block count, or that none is open. Use this before acting on the viewer, "
            "or when the user refers to what it's showing. Errors if the viewer isn't "
            "currently open."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "display_list_actions",
        "description": (
            "List every action currently invocable on the document viewer, namespaced as "
            "\"<pane>.<action>\" (e.g. \"document.open\", \"document.select\"). Call this "
            "to see exactly what's available before calling display_invoke_action — "
            "don't guess an action name."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "display_invoke_action",
        "description": (
            "Invoke one action on the document viewer by its exact name, from "
            "display_list_actions (e.g. \"document.open\" with args {\"path\": \"...\"}). "
            "Invoking an action brings what it acts on into view first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Namespaced action name, e.g. \"document.open\"."},
                "args": {"type": "object", "description": "Arguments for the action, if it takes any."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "email_search",
        "description": (
            "Search the user's Gmail using Gmail search syntax (e.g. 'is:unread in:inbox', "
            "'from:landlord newer_than:7d', 'subject:invoice', 'category:promotions older_than:30d'). "
            "Returns message IDs, sender, subject, date and a snippet — use email_read for the full text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query."},
                "max_results": {"type": "integer", "description": "Max messages to return (default 15, max 50)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "email_read",
        "description": (
            "Read one email (message_id) or a whole conversation (thread_id) in full: headers, "
            "labels, attachment names, and plain-text body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID from email_search."},
                "thread_id": {"type": "string", "description": "Thread ID, to read the whole conversation."},
            },
        },
    },
    {
        "name": "email_list_labels",
        "description": "List the user's Gmail labels (system and custom).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "email_modify",
        "description": (
            "Add or remove labels on one or more emails. This is how to triage: archive = remove "
            "'INBOX'; mark read = remove 'UNREAD'; mark unread = add 'UNREAD'; star = add 'STARRED'; "
            "mark important = add 'IMPORTANT'. Custom labels that don't exist yet are created. "
            "Batch related messages into one call — the user approves each call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to add."},
                "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to remove."},
            },
            "required": ["message_ids"],
        },
    },
    {
        "name": "email_trash",
        "description": (
            "Move emails to Trash (recoverable for 30 days; nothing is permanently deleted). "
            "Prefer archiving (email_modify removing INBOX) unless the user says delete or trash."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"message_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["message_ids"],
        },
    },
    {
        "name": "email_unsubscribe",
        "description": (
            "Unsubscribe from a mailing list using the email's List-Unsubscribe header (one-click "
            "or by sending the unsubscribe email). If only a web page is offered, returns its URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string", "description": "Any message from the list."}},
            "required": ["message_id"],
        },
    },
    {
        "name": "email_draft",
        "description": (
            "Save an email as a Gmail draft without sending it. For a reply, pass reply_to_message_id — "
            "recipient, 'Re:' subject and threading are filled in automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient(s), comma-separated. Optional for replies."},
                "cc": {"type": "string"},
                "subject": {"type": "string", "description": "Optional for replies."},
                "body": {"type": "string", "description": "Plain-text body, signed the way the user signs."},
                "reply_to_message_id": {"type": "string"},
            },
            "required": ["body"],
        },
    },
    {
        "name": "email_send",
        "description": (
            "Send an email from the user's Gmail. The user sees the full message on their phone and "
            "must approve it. For a reply, pass reply_to_message_id — recipient, 'Re:' subject and "
            "threading are filled in automatically. Write in the user's voice; never send anything "
            "they haven't asked for."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient(s), comma-separated. Optional for replies."},
                "cc": {"type": "string"},
                "subject": {"type": "string", "description": "Optional for replies."},
                "body": {"type": "string", "description": "Plain-text body."},
                "reply_to_message_id": {"type": "string"},
            },
            "required": ["body"],
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
            return _browser_open(inputs.get("url", ""), inputs.get("headless", False))
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
        elif name == "computer_screenshot":
            return _computer_screenshot()
        elif name == "computer_click":
            return _computer_click(inputs["x"], inputs["y"], inputs.get("button", "left"))
        elif name == "computer_type":
            return _computer_type(inputs["text"])
        elif name == "get_current_time":
            return _get_current_time()
        elif name == "web_search":
            return _web_search(inputs["query"])
        elif name == "read_url":
            return _read_url(inputs["url"])
        elif name == "update_memory":
            return _update_memory(inputs["name"], inputs["type"], inputs["description"], inputs["content"])
        elif name == "delete_memory":
            return _delete_memory(inputs["name"])
        elif name == "restart_server":
            return _restart_server(inputs["context"])
        elif name == "email_search":
            return gmail.search(inputs["query"], inputs.get("max_results", 15))
        elif name == "email_read":
            return gmail.read(inputs.get("message_id", ""), inputs.get("thread_id", ""))
        elif name == "email_list_labels":
            return gmail.list_labels()
        elif name == "email_modify":
            return gmail.modify(inputs["message_ids"], inputs.get("add_labels"), inputs.get("remove_labels"))
        elif name == "email_trash":
            return gmail.trash(inputs["message_ids"])
        elif name == "email_unsubscribe":
            return gmail.unsubscribe(inputs["message_id"])
        elif name == "email_draft":
            return gmail.draft(**_email_fields(inputs))
        elif name == "email_send":
            return gmail.send(**_email_fields(inputs))
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
    elif name == "delegate_task":
        cwd = inputs.get("cwd", "")
        return f"delegate to Claude Code" + (f" in {cwd}" if cwd else "") + f": {inputs['task']}"
    elif name == "follow_up_delegation":
        return f"follow up with delegation {inputs['delegation_id']}: {inputs['message']}"
    elif name == "check_delegation":
        return f"check delegation {inputs['delegation_id']}"
    elif name == "stop_delegation":
        return f"stop delegation {inputs['delegation_id']}"
    elif name == "list_delegations":
        return "list delegations"
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
        return f"update memory note '{inputs.get('name', '?')}'"
    elif name == "delete_memory":
        return f"delete memory note '{inputs.get('name', '?')}'"
    elif name == "restart_server":
        return "restart server"
    elif name == "update_scratchpad":
        return "update scratchpad"
    elif name == "browser_open":
        url = inputs.get("url", "")
        mode = "headless" if inputs.get("headless", False) else "visible"
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
    elif name == "computer_screenshot":
        return "take a screenshot of the desktop"
    elif name == "computer_click":
        button = inputs.get("button", "left")
        return f"{button}-click at ({inputs['x']}, {inputs['y']})"
    elif name == "computer_type":
        return f"type \"{inputs['text']}\" on the desktop"
    elif name == "close_connection":
        return "close connection"
    elif name == "display_get_state":
        return "check the display"
    elif name == "display_list_actions":
        return "list display actions"
    elif name == "display_invoke_action":
        return f"invoke \"{inputs.get('name', '?')}\" on the display"
    elif name == "email_search":
        return f"search email for \"{inputs.get('query', '')}\""
    elif name == "email_read":
        return "read an email thread" if inputs.get("thread_id") else "read an email"
    elif name == "email_list_labels":
        return "list email labels"
    elif name == "email_modify":
        n = len(inputs.get("message_ids", []))
        changes = []
        add = inputs.get("add_labels") or []
        remove = inputs.get("remove_labels") or []
        if "INBOX" in [r.upper() for r in remove]:
            changes.append("archive")
        if "UNREAD" in [r.upper() for r in remove]:
            changes.append("mark read")
        if "UNREAD" in [a.upper() for a in add]:
            changes.append("mark unread")
        other_add = [a for a in add if a.upper() != "UNREAD"]
        other_remove = [r for r in remove if r.upper() not in ("INBOX", "UNREAD")]
        if other_add:
            changes.append("add label " + ", ".join(other_add))
        if other_remove:
            changes.append("remove label " + ", ".join(other_remove))
        return f"{' + '.join(changes) or 'relabel'} {n} email{'s' if n != 1 else ''}"
    elif name == "email_trash":
        n = len(inputs.get("message_ids", []))
        return f"move {n} email{'s' if n != 1 else ''} to Trash"
    elif name == "email_unsubscribe":
        return "unsubscribe from this mailing list"
    elif name in ("email_draft", "email_send"):
        verb = "send" if name == "email_send" else "save a draft"
        to = gmail.recipients_summary(inputs.get("to", ""), inputs.get("cc", ""))
        target = f" to {to}" if to else (" replying to the thread" if inputs.get("reply_to_message_id") else "")
        subject = inputs.get("subject", "")
        head = f"{verb}{target}" + (f" — \"{subject}\"" if subject else "")
        return f"{head}:\n\n{inputs.get('body', '')}"
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
        return result
    elif name == "delete_memory":
        return result
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
    elif name == "computer_click":
        return result[:80]
    elif name == "computer_type":
        return result[:80]
    elif name == "close_connection":
        return "Connection closed"
    elif name == "email_search":
        return result.splitlines()[0][:80]
    elif name == "email_read":
        return "Read email"
    elif name.startswith("email_"):
        return result[:80]
    return "Done"


def _email_fields(inputs: dict) -> dict:
    return {k: inputs.get(k, "") for k in ("to", "cc", "subject", "body", "reply_to_message_id")}


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


def _update_memory(name: str, type: str, description: str, content: str) -> str:
    try:
        memory_store.write_note(name, type, description, content)
    except ValueError as e:
        return f"Error: {e}"
    return f"Memory note '{name}' updated ({len(content)} bytes)."


def _delete_memory(name: str) -> str:
    if memory_store.delete_note(name):
        return f"Memory note '{name}' deleted."
    return f"Error: no memory note named '{name}'."


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
    # Keep each link's destination next to its text — otherwise "Download for
    # ARM64" reaches Claude with no URL, and the only way to find it is to
    # escalate to the browser.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(resp.url, href)
        label = a.get_text(" ", strip=True)
        a.replace_with(f"{label} ({absolute})" if label and label != absolute else absolute)
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
