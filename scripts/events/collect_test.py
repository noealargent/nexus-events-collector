#!/usr/bin/env python3
"""No network. Dedup, opt-out and organisation rows for the events importer."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from collect import dedupe_scraped, in_horizon, load_store_state, organisation_sites, record_opted_out, still_current, today_in_denver  # noqa: E402

manual = [{"churchId": "ch_rive", "churchSlug": "eglise-de-la-rive", "title": "Soirée louange", "date": "2026-12-01"}]
scraped = [
    {"churchId": "ch_rive", "churchSlug": "eglise-de-la-rive", "title": "Soiree louange", "date": "2026-12-01", "id": "imp-1"},
    {"churchId": "ch_rive", "churchSlug": "eglise-de-la-rive", "title": "Conférence mission", "date": "2026-12-02", "id": "imp-2"},
]
kept = dedupe_scraped(scraped, manual)
assert [item["id"] for item in kept] == ["imp-2"], kept
assert len(manual) == 1

assert record_opted_out({"id": "ch_rive", "slug": "eglise-de-la-rive", "eventsImportOptOut": True}, set(), set())
assert record_opted_out({"id": "ch_rive", "slug": "eglise-de-la-rive"}, {"ch_rive"}, set())
assert record_opted_out({"website": "https://www.example.org/agenda"}, set(), {"example.org"})
assert not record_opted_out({"id": "ch_other", "website": "https://other.example"}, {"ch_rive"}, {"example.org"})

orgs = organisation_sites([
    {"id": "org_cnef", "slug": "cnef", "name": "CNEF", "websiteUrl": "https://www.lecnef.org/", "city": "Paris"},
    {"id": "org_empty", "name": "Sans site"},
])
assert len(orgs) == 1 and orgs[0]["kind"] == "organisation" and orgs[0]["website"].startswith("https://")
import datetime as dt
import json
import os
import tempfile

assert still_current({"date": "2026-11-11"}, "2026-11-11")
assert still_current({"date": "2026-11-04", "endDate": "2026-11-12"}, "2026-11-11")
assert not still_current({"date": "2026-11-04", "endDate": "2026-11-08"}, "2026-11-11")
assert not still_current({"date": ""}, "2026-11-11")
assert in_horizon(dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc))
assert not in_horizon(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc))
assert not in_horizon(None, dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc))

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "nexus-store.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "churches": [{
                "id": "ch_rive",
                "slug": "eglise-de-la-rive",
                "websiteUrl": "https://www.eglisedelarive.example",
                "eventsImportOptOut": True,
            }],
            "events": [{"churchId": "ch_rive", "title": "Soirée louange", "date": "2026-12-01"}],
        }, handle)
    state = load_store_state(path)
assert "ch_rive" in state["ids"] and "eglise-de-la-rive" in state["ids"]
assert "eglisedelarive.example" in state["hosts"]
shard = {"id": "osm-other", "slug": "autre-eglise", "website": "https://www.eglisedelarive.example/agenda"}
assert record_opted_out(shard, state["ids"], state["hosts"])
assert state["manual"][0]["title"] == "Soirée louange"
assert load_store_state("/tmp/nexus-store-does-not-exist.json")["manual"] == []

import datetime as dt
assert today_in_denver(dt.datetime(2026, 11, 13, 4, 0, tzinfo=dt.timezone.utc)) == "2026-11-12"
print("collect: ok")
