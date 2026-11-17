#!/usr/bin/env python3
"""No network. Archive, shrink guard, and Denver date for the nightly refresh."""
import datetime as dt
import json
import os
import sys
import tempfile
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from collect import still_current, today_in_denver  # noqa: E402
from nightly_refresh import append_archive, split_expired, validate_shrink  # noqa: E402

# 04:00 UTC on 13 Nov is still 21:00 the previous evening in Denver (UTC-7 in November).
assert today_in_denver(dt.datetime(2026, 11, 13, 4, 0, tzinfo=dt.timezone.utc)) == "2026-11-12"
assert today_in_denver(dt.datetime(2026, 11, 13, 8, 0, tzinfo=dt.timezone.utc)) == "2026-11-13"
assert today_in_denver(dt.datetime(2026, 11, 13, 4, 0)) == "2026-11-12"  # naive = UTC
assert today_in_denver(dt.datetime(2026, 11, 12, 3, 0, tzinfo=ZoneInfo("America/Denver"))) == "2026-11-12"

today = "2026-11-12"
current, expired = split_expired([
    {"id": "a", "date": "2026-11-12"},
    {"id": "b", "date": "2026-11-04", "endDate": "2026-11-11"},
    {"id": "c", "date": "2026-11-04", "endDate": "2026-11-12"},
    "bad",
], today)
assert [item["id"] for item in current] == ["a", "c"]
assert [item["id"] for item in expired] == ["b"]
assert not still_current({"date": "2026-11-11"}, today)

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "expired.json")
    first = append_archive(path, expired, today, "2026-11-12T10:00:00Z")
    assert first == 1
    again = append_archive(path, expired, today, "2026-11-12T11:00:00Z")
    assert again == 0  # same id is not duplicated
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["timezone"] == "America/Denver"
    assert doc["events"][0]["id"] == "b" and doc["events"][0]["archivedOn"] == today

assert validate_shrink({"FR": 100, "US": 1000}, {"FR": 80, "US": 900}, 0.30) == []
errors = validate_shrink({"FR": 100, "US": 1000}, {"FR": 60, "US": 900}, 0.30)
assert any(item.startswith("FR:") for item in errors) and not any(item.startswith("US:") for item in errors)
# A 30% drop is allowed; 31% is not.
assert validate_shrink({"FR": 100}, {"FR": 70}, 0.30) == []
assert validate_shrink({"FR": 100}, {"FR": 69}, 0.30)

print("nightly refresh: archive, shrink guard and Denver date ok")
