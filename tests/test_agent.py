import pytest
from pathlib import Path
from server.agent import _extract_sentences, build_stable_prompt, build_volatile_prompt


# ── _extract_sentences ────────────────────────────────────────────────────────

def test_extract_sentences_single_complete():
    sentences, remainder = _extract_sentences("Hello world. ")
    assert sentences == ["Hello world."]
    assert remainder == ""


def test_extract_sentences_multiple():
    sentences, remainder = _extract_sentences("First sentence. Second sentence. ")
    assert len(sentences) == 2
    assert sentences[0] == "First sentence."
    assert sentences[1] == "Second sentence."


def test_extract_sentences_incomplete_stays_in_buffer():
    sentences, remainder = _extract_sentences("This is incomplete")
    assert sentences == []
    assert remainder == "This is incomplete"


def test_extract_sentences_question_mark():
    sentences, remainder = _extract_sentences("Is this right? Yes it is. ")
    assert len(sentences) == 2
    assert sentences[0] == "Is this right?"


def test_extract_sentences_exclamation():
    sentences, remainder = _extract_sentences("Done! Great work. ")
    assert len(sentences) == 2
    assert sentences[0] == "Done!"


def test_extract_sentences_trailing_incomplete():
    sentences, remainder = _extract_sentences("First sentence. Still going")
    assert sentences == ["First sentence."]
    assert remainder == "Still going"


def test_extract_sentences_empty_string():
    sentences, remainder = _extract_sentences("")
    assert sentences == []
    assert remainder == ""


def test_extract_sentences_filters_empty_parts():
    sentences, remainder = _extract_sentences("Hello.  World. ")
    assert all(s for s in sentences)


# ── build_volatile_prompt ─────────────────────────────────────────────────────

def test_build_volatile_prompt_contains_date():
    result = build_volatile_prompt()
    import re
    assert re.search(r"20\d\d", result)


def test_build_volatile_prompt_without_scratchpad():
    result = build_volatile_prompt()
    assert "scratchpad" not in result.lower()


def test_build_volatile_prompt_with_scratchpad():
    result = build_volatile_prompt(scratchpad="Working on auth module")
    assert "Working on auth module" in result
    assert "scratchpad" in result.lower()


# ── build_stable_prompt ───────────────────────────────────────────────────────

def test_build_stable_prompt_contains_base():
    result = build_stable_prompt()
    assert "Clio" in result
    assert "voice" in result.lower()


def test_build_stable_prompt_with_mock_soul(tmp_path, monkeypatch):
    soul_file = tmp_path / "soul.md"
    soul_file.write_text("You are calm and focused.")
    import server.agent as agent_module
    monkeypatch.setattr(agent_module, "SOUL_PATH", soul_file)
    result = build_stable_prompt()
    assert "You are calm and focused." in result


def test_build_stable_prompt_with_mock_memory(tmp_path, monkeypatch):
    memory_file = tmp_path / "memory.md"
    memory_file.write_text("User prefers dark mode.")
    import server.agent as agent_module
    import server.tools as tools_module
    monkeypatch.setattr(agent_module, "MEMORY_PATH", memory_file)
    monkeypatch.setattr(tools_module, "MEMORY_PATH", memory_file)
    result = build_stable_prompt()
    assert "User prefers dark mode." in result


def test_build_stable_prompt_without_soul(tmp_path, monkeypatch):
    missing = tmp_path / "no_soul.md"
    import server.agent as agent_module
    monkeypatch.setattr(agent_module, "SOUL_PATH", missing)
    # Should not raise; soul section simply absent
    result = build_stable_prompt()
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_stable_prompt_without_memory(tmp_path, monkeypatch):
    missing = tmp_path / "no_memory.md"
    import server.agent as agent_module
    import server.tools as tools_module
    monkeypatch.setattr(agent_module, "MEMORY_PATH", missing)
    monkeypatch.setattr(tools_module, "MEMORY_PATH", missing)
    result = build_stable_prompt()
    assert isinstance(result, str)
    assert len(result) > 100
