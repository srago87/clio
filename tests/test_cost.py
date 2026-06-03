from unittest.mock import MagicMock
from server.cost import usd_from_usage, SessionCostTracker


def _usage(input=0, output=0, cache_write=0, cache_read=0):
    u = MagicMock()
    u.input_tokens = input
    u.output_tokens = output
    u.cache_creation_input_tokens = cache_write
    u.cache_read_input_tokens = cache_read
    return u


def test_usd_from_usage_zero():
    assert usd_from_usage(_usage()) == 0.0


def test_usd_from_usage_input_only():
    assert abs(usd_from_usage(_usage(input=1_000_000)) - 3.00) < 1e-6


def test_usd_from_usage_output_only():
    assert abs(usd_from_usage(_usage(output=1_000_000)) - 15.00) < 1e-6


def test_usd_from_usage_cache_read():
    assert abs(usd_from_usage(_usage(cache_read=1_000_000)) - 0.30) < 1e-6


def test_usd_from_usage_cache_write():
    assert abs(usd_from_usage(_usage(cache_write=1_000_000)) - 3.75) < 1e-6


def test_usd_from_usage_combined():
    cost = usd_from_usage(_usage(input=1000, output=200, cache_read=500))
    expected = 1000 * 3e-6 + 200 * 15e-6 + 500 * 0.3e-6
    assert abs(cost - expected) < 1e-9


def test_session_cost_tracker_empty_report():
    assert "no API calls" in SessionCostTracker().report()


def test_session_cost_tracker_records_and_reports():
    tracker = SessionCostTracker()
    tracker.record("turn", _usage(input=1000, output=500))
    tracker.record("turn", _usage(input=500, output=200))
    report = tracker.report()
    assert "turn" in report
    assert "TOTAL" in report
    assert "×2" in report


def test_session_cost_tracker_multiple_labels():
    tracker = SessionCostTracker()
    tracker.record("turn", _usage(input=1000))
    tracker.record("mem-extract", _usage(input=500))
    report = tracker.report()
    assert "turn" in report
    assert "mem-extract" in report


def test_session_cost_tracker_total_is_sum():
    tracker = SessionCostTracker()
    tracker.record("a", _usage(input=1_000_000))
    tracker.record("b", _usage(output=1_000_000))
    report = tracker.report()
    # $3.00 input + $15.00 output = $18.00
    assert "18.0000" in report
