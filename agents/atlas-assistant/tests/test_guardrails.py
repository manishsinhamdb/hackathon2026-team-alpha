"""Unit tests for add_ip_access guardrails (no LLM, no platform, no Atlas calls)."""

from datetime import datetime, timedelta, timezone

import pytest

from agent_atlas_assistant.write_tools import MAX_EXPIRY_HOURS, build_access_entry


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "10.0.0.0/16", "203.0.113.0/23", "::/0", "2001:db8::/48"])
def test_refuses_ranges_wider_than_limit(bad):
    entry, refusal = build_access_entry(bad, "test")
    assert entry is None
    assert "wider than the allowed" in refusal


@pytest.mark.parametrize("junk", ["not-an-ip", "", "999.1.1.1"])
def test_refuses_invalid_input(junk):
    entry, refusal = build_access_entry(junk, "test")
    assert entry is None
    assert "Not a valid IP" in refusal


def test_accepts_single_ip():
    entry, refusal = build_access_entry("203.0.113.10", "test")
    assert refusal is None
    assert entry["ipAddress"] == "203.0.113.10"
    assert "cidrBlock" not in entry


def test_accepts_slash_24_as_cidr_block():
    entry, refusal = build_access_entry("203.0.113.0/24", "test")
    assert refusal is None
    assert entry["cidrBlock"] == "203.0.113.0/24"


def _hours_until(entry):
    expires = datetime.strptime(entry["deleteAfterDate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (expires - datetime.now(timezone.utc)) / timedelta(hours=1)


def test_expiry_is_capped_at_max():
    entry, _ = build_access_entry("203.0.113.10", "test", expiry_hours=1000)
    assert MAX_EXPIRY_HOURS - 0.1 < _hours_until(entry) <= MAX_EXPIRY_HOURS


def test_expiry_has_minimum_of_one_hour():
    entry, _ = build_access_entry("203.0.113.10", "test", expiry_hours=0)
    assert 0.9 < _hours_until(entry) <= 1


def test_default_expiry_is_24_hours():
    entry, _ = build_access_entry("203.0.113.10", "test")
    assert 23.9 < _hours_until(entry) <= 24


def test_comment_is_tagged_and_limited():
    entry, _ = build_access_entry("203.0.113.10", "x" * 200)
    assert entry["comment"].startswith("Atlas Assistant: ")
    assert len(entry["comment"]) <= 80
