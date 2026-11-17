#!/usr/bin/env python3
"""No network. Rolling coverage: always-orgs, cursor, no-signal skip, hot refresh, shards."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from coverage import (  # noqa: E402
    HOT_CAP,
    advance_coverage,
    assemble_batch,
    empty_coverage,
    in_no_signal_cooldown,
    load_coverage,
    save_coverage,
    shard_list,
    signal_ids,
)


def rec(n, kind="church", cc="US"):
    row = {"id": n, "slug": n, "name": n, "website": f"https://{n}.example", "cc": cc}
    if kind == "organisation":
        row["kind"] = "organisation"
    return row


churches = [rec(f"c{i}") for i in range(10)]
orgs = [rec("org_a", "organisation"), rec("org_b", "organisation")]

# All organisation sites are first and do not count against --limit.
batch, meta = assemble_batch(churches, orgs, country="US", today="2026-11-13", limit=3, always_orgs=True, prioritize=False)
assert [r["id"] for r in batch] == ["org_a", "org_b", "c0", "c1", "c2"], [r["id"] for r in batch]
assert meta["orgIds"] == ["org_a", "org_b"]
assert meta["windowIds"] == ["c0", "c1", "c2"]
assert meta["queueSize"] == 10
assert meta["cursor"] == 0

# Cursor rotation only walks churches; orgs stay at the front every night.
cov = empty_coverage()
cov["countries"]["US"] = {"cursor": 8}
batch, meta = assemble_batch(churches, orgs, country="US", today="2026-11-13", limit=4, coverage=cov, always_orgs=True, prioritize=False)
assert [r["id"] for r in batch] == ["org_a", "org_b", "c8", "c9", "c0", "c1"], [r["id"] for r in batch]
assert meta["cursor"] == 8

# Sites with a recent "no signal" crawl are skipped in the window and the cursor walks past them.
cov = empty_coverage()
cov["noSignal"] = {"c1": "2026-11-10", "c2": "2026-10-01"}
assert in_no_signal_cooldown(cov, "c1", "2026-11-13", 14) is True
assert in_no_signal_cooldown(cov, "c2", "2026-11-13", 14) is False  # 43 days ago, cooldown expired
batch, meta = assemble_batch(churches, orgs, country="US", today="2026-11-13", limit=3, coverage=cov, always_orgs=True, prioritize=False)
assert meta["windowIds"] == ["c0", "c2", "c3"], meta["windowIds"]
assert meta["cooldownSkipped"] == ["c1"]

# Known event-page / feed sites are refreshed first (after orgs), capped, and not duplicated in the window.
cov = empty_coverage()
cov["hasSignal"] = {"c5": "2026-11-01", "c6": "2026-11-01", "c7": "2026-11-01"}
prev = [{"churchId": "c8"}]
batch, meta = assemble_batch(
    churches, orgs, country="US", today="2026-11-13", limit=2, coverage=cov,
    always_orgs=True, prioritize=True, hot_cap=2, previous_events=prev,
)
assert meta["hotIds"] == ["c5", "c6"], meta["hotIds"]  # cap 2, pool c5,c6,c7,c8; cursor 0
assert meta["windowIds"] == ["c0", "c1"], meta["windowIds"]
assert "c5" not in meta["windowIds"]
assert [r["id"] for r in batch[:4]] == ["org_a", "org_b", "c5", "c6"]

# After a finished window, the cursor advances past finished and cooldown sites, not past unstarted ones.
cov = empty_coverage()
_, meta = assemble_batch(churches, [], country="US", today="2026-11-13", limit=5, always_orgs=True, prioritize=False)
logs = [{"id": "c0", "status": "ok", "sources": ["jsonld"], "agenda": False},
        {"id": "c1", "status": "ok", "sources": [], "agenda": False},
        {"id": "c2", "status": "ConnectionError", "sources": []}]
# c3 and c4 were in the window but not started (time budget).
cov = advance_coverage(cov, meta, logs, "2026-11-13", "2026-11-13T12:00:00Z")
assert cov["countries"]["US"]["cursor"] == 3, cov["countries"]["US"]
assert cov["hasSignal"]["c0"] == "2026-11-13"
assert cov["noSignal"]["c1"] == "2026-11-13"
assert "c2" not in cov["noSignal"]  # failed fetch is retried, not marked no-signal
assert signal_ids(cov) == {"c0"}

# A later assemble starts at c3 (the first unstarted window site).
batch, meta2 = assemble_batch(churches, [], country="US", today="2026-11-13", limit=2, coverage=cov, prioritize=True, always_orgs=True)
assert meta2["cursor"] == 3
assert meta2["windowIds"] == ["c3", "c4"], meta2["windowIds"]
assert meta2["hotIds"] == ["c0"]  # previous signal, cap default but only one known

# Completing a full lap records a cycle.
cov = empty_coverage()
tiny = [rec(f"c{i}") for i in range(3)]
batch, meta = assemble_batch(tiny, [], country="FR", today="2026-11-13", limit=0, prioritize=False)
assert meta["windowIds"] == ["c0", "c1", "c2"]
logs = [{"id": sid, "status": "ok", "sources": [], "agenda": True} for sid in meta["windowIds"]]
cov = advance_coverage(cov, meta, logs, "2026-11-13", "2026-11-13T12:00:00Z")
assert cov["countries"]["FR"]["cursor"] == 0
assert cov["countries"]["FR"]["completedCycles"] == 1
assert cov["countries"]["FR"]["lastFullPassAt"] == "2026-11-13"
assert set(cov["hasSignal"]) == {"c0", "c1", "c2"}

# Matrix shards partition the stable church list without dropping or duplicating ids.
parts = shard_list(churches, 0, 3) + shard_list(churches, 1, 3) + shard_list(churches, 2, 3)
assert [r["id"] for r in parts] == [r["id"] for r in churches] or set(r["id"] for r in parts) == set(r["id"] for r in churches)
assert len(parts) == 10
assert [r["id"] for r in shard_list(churches, 0, 3)] == ["c0", "c3", "c6", "c9"]
assert [r["id"] for r in shard_list(churches, 1, 3)] == ["c1", "c4", "c7"]
b0, m0 = assemble_batch(churches, orgs, country="US", today="2026-11-13", limit=10, shard=0, shards=3, prioritize=False)
assert m0["queueSize"] == 4 and m0["windowIds"] == ["c0", "c3", "c6", "c9"]
assert m0["orgIds"] == ["org_a", "org_b"]  # orgs still always included on every shard

# Coverage file round-trip.
import tempfile
with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "coverage.json")
    save_coverage(path, cov)
    loaded = load_coverage(path)
    assert loaded["countries"]["FR"]["completedCycles"] == 1
    assert load_coverage(os.path.join(folder, "missing.json"))["countries"] == {}

assert HOT_CAP == 60
print("coverage: always-orgs, cursor, no-signal skip, hot refresh and shards ok")

# organisation-sites.json must list the AWMI Tribe feed (Make Heaven Crowded and other AWMI events).
orgs = json.load(open(os.path.join(os.path.dirname(__file__), "organisation-sites.json"), encoding="utf-8"))
assert any("awmi.net" in (row.get("website") or "") and row.get("id") == "org_awmi" for row in orgs), orgs
print("organisation-sites: awmi.net present")
