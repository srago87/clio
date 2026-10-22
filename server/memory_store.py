# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""
memory_store.py — Clio's persistent memory: a directory of small typed
markdown notes plus an index, structured to double as an Obsidian vault.

Why this shape instead of one growing file: the old design (a single
memory.md, rewritten wholesale by an LLM on every update) meant every save
was a full-file regeneration — a lossy compression step that compounds over
many rewrites, and forced a jarring "compress everything" event whenever the
file crossed its size cap. Splitting memory into small, typed, per-topic
notes means an update touches exactly the one note it's about; every other
note is untouched, byte-for-byte, across sessions.

Each note is a real Obsidian note: YAML frontmatter (name/type/description/
updated) plus a body that can use [[wikilink]] syntax to reference other
notes by name. MEMORY_DIR can be opened directly as an Obsidian vault with no
conversion step — Sean can browse, search, and hand-edit Clio's memory the
same way Clio does.

MEMORY.md is the one file always loaded first, in full: a one-line-per-note
table of contents (see _index_line). Note bodies are also all loaded into
the stable system prompt today (see agent.py's build_stable_prompt) — at
Clio's current memory scale that's still cheap and simple. Selective
retrieval (loading only notes relevant to what's currently being discussed)
is a reasonable future step if the vault grows large enough for that to
matter; not needed yet, so not built yet.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "memory"
INDEX_PATH = MEMORY_DIR / "MEMORY.md"

VALID_TYPES = {"user", "feedback", "project", "reference"}

# Per-note, not per-vault — see module docstring. A note this size is already
# a lot for one topic; hitting it is a signal to compress, not a crisis.
NOTE_SIZE_LIMIT = 4 * 1024

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n\n?(.*)$", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+):\s*(.*)$")


def is_valid_name(name: str) -> bool:
    return bool(_SLUG_RE.match(name))


def _note_path(name: str) -> Path:
    return MEMORY_DIR / f"{name}.md"


@dataclass
class Note:
    name: str
    type: str
    description: str
    body: str
    updated: str = ""

    def render(self) -> str:
        return (
            "---\n"
            f"name: {self.name}\n"
            f"type: {self.type}\n"
            f"description: {self.description}\n"
            f"updated: {self.updated}\n"
            "---\n\n"
            f"{self.body.strip()}\n"
        )


def parse_note(text: str, name: str) -> Note:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # Tolerate a note with no frontmatter (e.g. a fresh file hand-created
        # in Obsidian) — treat the whole thing as body rather than erroring.
        return Note(name=name, type="", description="", body=text)
    fm_text, body = match.groups()
    fields = {}
    for line in fm_text.splitlines():
        field_match = _FIELD_RE.match(line)
        if field_match:
            fields[field_match.group(1)] = field_match.group(2)
    return Note(
        name=name,
        type=fields.get("type", ""),
        description=fields.get("description", ""),
        body=body,
        updated=fields.get("updated", ""),
    )


def read_note(name: str) -> "Note | None":
    path = _note_path(name)
    if not path.exists():
        return None
    return parse_note(path.read_text(), name)


def write_note(name: str, type: str, description: str, body: str) -> Note:
    if not is_valid_name(name):
        raise ValueError(f"'{name}' isn't a valid note name — use lowercase-kebab-case, e.g. 'about-the-user'.")
    if type not in VALID_TYPES:
        raise ValueError(f"'{type}' isn't a valid memory type — use one of: {', '.join(sorted(VALID_TYPES))}.")
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    note = Note(
        name=name,
        type=type,
        description=description,
        body=body,
        updated=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    _note_path(name).write_text(note.render())
    _update_index(note)
    return note


def delete_note(name: str) -> bool:
    path = _note_path(name)
    if not path.exists():
        return False
    path.unlink()
    _remove_from_index(name)
    return True


def note_size(name: str) -> int:
    path = _note_path(name)
    return path.stat().st_size if path.exists() else 0


def list_notes() -> list[Note]:
    if not MEMORY_DIR.exists():
        return []
    notes = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        notes.append(parse_note(path.read_text(), path.stem))
    return notes


def _index_line(note: Note) -> str:
    return f"- [[{note.name}]] ({note.type}) — {note.description}"


def _read_index_lines() -> list[str]:
    if not INDEX_PATH.exists():
        return []
    lines = INDEX_PATH.read_text().splitlines()
    return [l for l in lines if l.startswith("- [[")]


def _update_index(note: Note) -> None:
    lines = _read_index_lines()
    new_line = _index_line(note)
    pattern = re.compile(rf"^- \[\[{re.escape(note.name)}\]\]")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    _write_index(lines)


def _remove_from_index(name: str) -> None:
    pattern = re.compile(rf"^- \[\[{re.escape(name)}\]\]")
    lines = [l for l in _read_index_lines() if not pattern.match(l)]
    _write_index(lines)


_INDEX_HEADER = (
    "# Clio's Memory\n\n"
    "This is an Obsidian vault — open this folder directly in Obsidian to browse, search, "
    "and hand-edit these notes with backlinks and graph view. [[wikilinks]] below resolve "
    "to the notes in this folder.\n\n"
    "Index of every note:\n\n"
)


def _write_index(lines: list[str]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(_INDEX_HEADER + "\n".join(sorted(lines)) + ("\n" if lines else ""))


def build_memory_prompt() -> str:
    """Everything that goes into the stable system prompt: the index, then
    every note's full body. Loading all notes (not just the index) is
    deliberate for now — see the module docstring."""
    if not INDEX_PATH.exists():
        return ""
    parts = [INDEX_PATH.read_text().strip()]
    for note in list_notes():
        parts.append(f"## {note.name}\n{note.body.strip()}")
    return "\n\n".join(parts)
