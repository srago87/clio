import pytest
from pathlib import Path
from server.session import VoiceSession, CONSOLIDATED_MARKER


@pytest.fixture(autouse=True)
def patch_logs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("server.session.LOGS_DIR", tmp_path)


def test_session_creates_log():
    session = VoiceSession()
    assert session.log_path.exists()
    content = session.log_path.read_text()
    assert "# Claude Voice Session" in content
    assert "Date" in content


def test_add_exchange_writes_both_sides():
    session = VoiceSession()
    session.add_exchange("What time is it?", "It's noon.")
    content = session.log_path.read_text()
    assert "What time is it?" in content
    assert "It's noon." in content


def test_add_exchange_increments_count():
    session = VoiceSession()
    assert session.exchange_count == 0
    session.add_exchange("a", "b")
    assert session.exchange_count == 1
    session.add_exchange("c", "d")
    assert session.exchange_count == 2


def test_session_end_writes_footer():
    session = VoiceSession()
    session.add_exchange("hi", "hello")
    session.end()
    content = session.log_path.read_text()
    assert "Session ended" in content
    assert "Exchanges: 1" in content


def test_get_unconsolidated_logs_empty(tmp_path):
    assert VoiceSession.get_unconsolidated_logs() == []


def test_get_unconsolidated_logs_returns_unprocessed():
    s1 = VoiceSession()
    s2 = VoiceSession()
    VoiceSession.mark_consolidated(s1.log_path)
    logs = VoiceSession.get_unconsolidated_logs()
    assert s2.log_path in logs
    assert s1.log_path not in logs


def test_mark_consolidated_appends_marker():
    session = VoiceSession()
    VoiceSession.mark_consolidated(session.log_path)
    assert CONSOLIDATED_MARKER in session.log_path.read_text()


def test_get_unconsolidated_excludes_current():
    session = VoiceSession()
    logs = VoiceSession.get_unconsolidated_logs(exclude_path=session.log_path)
    assert session.log_path not in logs


def test_multiple_sessions_each_get_own_log():
    s1 = VoiceSession()
    s2 = VoiceSession()
    assert s1.log_path != s2.log_path
    assert s1.log_path.exists()
    assert s2.log_path.exists()
