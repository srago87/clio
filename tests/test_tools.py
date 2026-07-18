import pytest
import re
from pathlib import Path
from server.tools import (
    _read_file, _write_file, _edit_file, _list_directory, _delete_file,
    _is_private_url, _is_safe_path, _get_current_time,
    describe_tool_call, summarize_tool_result,
)


# ── _read_file ────────────────────────────────────────────────────────────────

def test_read_file_full(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    assert _read_file(str(f)) == "hello world"


def test_read_file_line_range(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n")
    result = _read_file(str(f), start_line=2, end_line=2)
    assert "line2" in result
    assert "line1" not in result
    assert "line3" not in result


def test_read_file_start_only(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n")
    result = _read_file(str(f), start_line=2)
    assert "line2" in result
    assert "line3" in result
    assert "line1" not in result


def test_read_file_includes_line_numbers_in_range(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    result = _read_file(str(f), start_line=1, end_line=2)
    assert "1:" in result
    assert "2:" in result


def test_read_file_truncates_large_content(tmp_path, monkeypatch):
    import server.tools as tools_module
    monkeypatch.setattr(tools_module, "READ_LIMIT", 10)
    f = tmp_path / "big.txt"
    f.write_text("a" * 100)
    result = _read_file(str(f))
    assert "[truncated" in result


# ── _write_file ───────────────────────────────────────────────────────────────

def test_write_file_creates_file(tmp_path):
    path = str(tmp_path / "out.txt")
    result = _write_file(path, "hello")
    assert "Wrote" in result["text"]
    assert Path(path).read_text() == "hello"


def test_write_file_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "a" / "b" / "out.txt")
    _write_file(path, "deep")
    assert Path(path).read_text() == "deep"


def test_write_file_overwrites_existing(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("old")
    _write_file(str(f), "new")
    assert f.read_text() == "new"


# ── _edit_file ────────────────────────────────────────────────────────────────

def test_edit_file_exact_match(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    pass\n")
    result = _edit_file(str(f), "pass", "return 42")
    assert result["text"].startswith("Replaced")
    assert f.read_text() == "def foo():\n    return 42\n"


def test_edit_file_fuzzy_whitespace_match(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():   \n    pass\n")
    result = _edit_file(str(f), "def foo():", "def bar():")
    assert result["text"].startswith("Replaced")
    assert "bar" in f.read_text()


def test_edit_file_not_found_returns_error(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("hello world")
    result = _edit_file(str(f), "nonexistent string xyz", "replacement")
    assert result["text"].startswith("Error")


def test_edit_file_replaces_only_first_occurrence(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("x = 1\nx = 1\n")
    _edit_file(str(f), "x = 1", "x = 2")
    content = f.read_text()
    assert content.count("x = 2") == 1
    assert content.count("x = 1") == 1


def test_edit_file_partial_context_mismatch_error(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    return 1\n")
    # First line exists but surrounding context doesn't match
    result = _edit_file(str(f), "def foo():\n    return 99\n", "def bar():\n    return 99\n")
    assert result["text"].startswith("Error")


# ── _list_directory ───────────────────────────────────────────────────────────

def test_list_directory_shows_files_and_dirs(tmp_path):
    (tmp_path / "file.txt").write_text("")
    (tmp_path / "subdir").mkdir()
    result = _list_directory(str(tmp_path))
    assert "file.txt" in result
    assert "subdir" in result


def test_list_directory_dirs_listed_before_files(tmp_path):
    (tmp_path / "aaa.txt").write_text("")
    (tmp_path / "zzz").mkdir()
    result = _list_directory(str(tmp_path))
    lines = result.strip().split("\n")
    dir_idx = next(i for i, l in enumerate(lines) if "zzz" in l)
    file_idx = next(i for i, l in enumerate(lines) if "aaa.txt" in l)
    assert dir_idx < file_idx


# ── _delete_file ──────────────────────────────────────────────────────────────

def test_delete_file(tmp_path):
    f = tmp_path / "gone.txt"
    f.write_text("bye")
    result = _delete_file(str(f))
    assert "Deleted" in result
    assert not f.exists()


def test_delete_file_nonexistent_raises(tmp_path):
    with pytest.raises(Exception):
        _delete_file(str(tmp_path / "nope.txt"))


# ── path sandboxing ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_sensitive(tmp_path, monkeypatch):
    """Patch _SENSITIVE_PREFIXES to use a controlled tmp dir."""
    sensitive = tmp_path / ".ssh"
    sensitive.mkdir()
    import server.tools as tools_module
    monkeypatch.setattr(tools_module, "_SENSITIVE_PREFIXES", [sensitive])
    return sensitive


def test_is_safe_path_blocks_sensitive(fake_sensitive):
    assert not _is_safe_path(fake_sensitive / "id_rsa")


def test_is_safe_path_allows_outside(fake_sensitive, tmp_path):
    safe = tmp_path / "project" / "main.py"
    safe.parent.mkdir()
    assert _is_safe_path(safe)


def test_read_file_blocks_sensitive_path(fake_sensitive):
    target = fake_sensitive / "id_rsa"
    target.write_text("PRIVATE KEY")
    result = _read_file(str(target))
    assert "Error" in result
    assert "not allowed" in result


def test_write_file_blocks_sensitive_path(fake_sensitive):
    result = _write_file(str(fake_sensitive / "injected"), "evil")
    assert "Error" in result["text"]
    assert "not allowed" in result["text"]


def test_edit_file_blocks_sensitive_path(fake_sensitive):
    target = fake_sensitive / "config"
    target.write_text("old content")
    result = _edit_file(str(target), "old", "new")
    assert "Error" in result["text"]
    assert "not allowed" in result["text"]


def test_delete_file_blocks_sensitive_path(fake_sensitive):
    target = fake_sensitive / "known_hosts"
    target.write_text("entries")
    result = _delete_file(str(target))
    assert "Error" in result
    assert "not allowed" in result


def test_list_directory_blocks_sensitive_path(fake_sensitive):
    result = _list_directory(str(fake_sensitive))
    assert "Error" in result
    assert "not allowed" in result


def test_safe_path_allows_normal_tmp_file(tmp_path, monkeypatch):
    import server.tools as tools_module
    sensitive = tmp_path / ".ssh"
    monkeypatch.setattr(tools_module, "_SENSITIVE_PREFIXES", [sensitive])
    f = tmp_path / "project" / "safe.txt"
    f.parent.mkdir()
    f.write_text("ok")
    result = _read_file(str(f))
    assert result == "ok"


# ── _is_private_url ───────────────────────────────────────────────────────────

def test_is_private_url_localhost():
    assert _is_private_url("http://localhost:8080/path") is True


def test_is_private_url_private_class_a():
    assert _is_private_url("http://10.0.0.1/") is True


def test_is_private_url_private_class_b():
    assert _is_private_url("http://172.16.0.1/") is True


def test_is_private_url_private_class_c():
    assert _is_private_url("http://192.168.1.1/") is True


def test_is_private_url_public_domain():
    assert _is_private_url("https://example.com") is False


def test_is_private_url_public_anthropic():
    assert _is_private_url("https://anthropic.com") is False


# ── _get_current_time ─────────────────────────────────────────────────────────

def test_get_current_time_returns_nonempty():
    result = _get_current_time()
    assert isinstance(result, str)
    assert len(result) > 10


def test_get_current_time_contains_year():
    result = _get_current_time()
    assert re.search(r"20\d\d", result)


# ── describe_tool_call ────────────────────────────────────────────────────────

def test_describe_bash_command():
    result = describe_tool_call("bash_command", {"command": "ls -la"})
    assert "ls -la" in result


def test_describe_bash_command_with_cwd():
    result = describe_tool_call("bash_command", {"command": "npm test", "cwd": "/tmp/proj"})
    assert "npm test" in result
    assert "/tmp/proj" in result


def test_describe_write_file():
    result = describe_tool_call("write_file", {"path": "/tmp/x.py", "content": ""})
    assert "/tmp/x.py" in result


def test_describe_edit_file():
    result = describe_tool_call("edit_file", {"path": "/tmp/x.py", "old_string": "a", "new_string": "b"})
    assert "/tmp/x.py" in result


def test_describe_read_file_with_range():
    result = describe_tool_call("read_file", {"path": "/tmp/x.py", "start_line": 5, "end_line": 10})
    assert "5" in result
    assert "10" in result


def test_describe_web_search():
    result = describe_tool_call("web_search", {"query": "pytest fixtures"})
    assert "pytest fixtures" in result


def test_describe_unknown_tool_returns_name():
    result = describe_tool_call("nonexistent_tool", {})
    assert result == "nonexistent_tool"


# ── summarize_tool_result ─────────────────────────────────────────────────────

def test_summarize_read_file_counts_lines():
    result = summarize_tool_result("read_file", "line1\nline2\nline3")
    assert "3" in result


def test_summarize_write_file():
    assert summarize_tool_result("write_file", "Wrote 42 bytes") == "File written"


def test_summarize_edit_file():
    assert summarize_tool_result("edit_file", "Replaced in /tmp/x.py") == "Edit applied"


def test_summarize_delete_file():
    assert summarize_tool_result("delete_file", "Deleted /tmp/x.py") == "File deleted"


def test_summarize_error_propagates():
    result = summarize_tool_result("bash_command", "Error: command not found")
    assert "Error" in result


def test_summarize_update_memory():
    assert summarize_tool_result("update_memory", "Memory updated (512 bytes)") == "Memory updated"


def test_summarize_list_directory():
    result = summarize_tool_result("list_directory", "  file1.txt\n  file2.txt\n/subdir")
    assert "3" in result
