#!/usr/bin/env python3
"""Nightly FR/US events refresh: archive expired rows, collect a rolling FR/US batch, validate.

No network is used until --collect is on (the default). Expired events (last day < today
in America/Denver) leave public/events/*.json and are appended to docs/events-archive/expired.json.
Opt-outs in public/events/optout.json are honoured by import_events.py, which this script calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from collect import still_current, today_in_denver  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PUBLIC = os.path.join(ROOT, "public", "events")
FILES = {
    "FR": os.path.join(PUBLIC, "index.json"),
    "US": os.path.join(PUBLIC, "us.json"),
    "manual": os.path.join(PUBLIC, "manual-events.json"),
}
DEFAULT_ARCHIVE = os.path.join(ROOT, "docs", "events-archive", "expired.json")
IMPORTER = os.path.join(os.path.dirname(__file__), "import_events.py")
MAX_SHRINK = 0.30
# Collect budget for two daily GitHub Actions runs on a private repo's 2000-minute
# monthly allowance (ubuntu-latest). Limit 0 = no church cap; --seconds stops the crawl.
# France gets a shorter slice so the much larger US queue keeps most of the budget.
DEFAULT_SECONDS = 1200
DEFAULT_FR_SECONDS = 180
DEFAULT_FR_LIMIT = 0
DEFAULT_US_LIMIT = 0
DEFAULT_WORKERS = 10
DEFAULT_COVERAGE = os.path.join(ROOT, "docs", "events-archive", "coverage.json")


def load_doc(path: str) -> dict:
    if not os.path.exists(path):
        return {"events": []}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    if not isinstance(data.get("events", []), list):
        raise ValueError(f"{path} events must be a list")
    return data


def write_doc(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


def country_of(event: dict, default: str) -> str:
    value = (event.get("country") or "").strip()
    if value in ("France", "FR"):
        return "FR"
    if value in ("United States", "United States of America", "USA", "US"):
        return "US"
    return default


def split_expired(events: list, today: str) -> tuple[list, list]:
    current, expired = [], []
    for event in events:
        if isinstance(event, dict) and still_current(event, today):
            current.append(event)
        elif isinstance(event, dict):
            expired.append(event)
    return current, expired


def append_archive(path: str, expired: list, today: str, now_iso: str) -> int:
    if not expired:
        return 0
    doc = load_doc(path) if os.path.exists(path) else {
        "_doc": "Events whose last day is before today (America/Denver), moved out of public/events. Not listed on the site.",
        "timezone": "America/Denver",
        "events": [],
    }
    seen = {item.get("id") for item in doc.get("events") or [] if isinstance(item, dict)}
    added = 0
    rows = list(doc.get("events") or [])
    for event in expired:
        eid = event.get("id")
        if eid and eid in seen:
            continue
        if eid:
            seen.add(eid)
        row = dict(event)
        row["archivedAt"] = now_iso
        row["archivedOn"] = today
        rows.append(row)
        added += 1
    doc["updatedAt"] = now_iso
    doc["timezone"] = "America/Denver"
    doc["events"] = rows
    write_doc(path, doc)
    return added


def counts_by_file(docs: dict[str, dict], today: str) -> dict[str, int]:
    out = {}
    for key, doc in docs.items():
        out[key] = sum(1 for event in doc.get("events") or [] if isinstance(event, dict) and still_current(event, today))
    return out


def country_totals(docs: dict[str, dict], today: str) -> dict[str, int]:
    totals = {"FR": 0, "US": 0, "other": 0}
    for key, doc in docs.items():
        default = key if key in ("FR", "US") else ""
        for event in doc.get("events") or []:
            if not isinstance(event, dict) or not still_current(event, today):
                continue
            bucket = country_of(event, default or "other")
            totals[bucket if bucket in totals else "other"] += 1
    return totals


def church_ids(doc: dict) -> set[str]:
    return {event.get("churchId") for event in doc.get("events") or [] if isinstance(event, dict) and event.get("churchId")}


def run_importer(country: str, out: str, limit: int, seconds: int, remaining: float, *,
                 workers: int, coverage: str, hot_cap: int, shard: int, shards: int) -> dict:
    budget = max(30, int(remaining)) if seconds else 0
    cmd = [
        sys.executable, IMPORTER,
        "--countries", country,
        "--merge",
        "--out", out,
        "--optout", os.path.join(PUBLIC, "optout.json"),
        "--limit", str(limit),
        "--workers", str(workers),
        "--always-orgs",
        "--prioritize-events",
        "--hot-cap", str(hot_cap),
        "--matrix-shard", str(shard),
        "--matrix-shards", str(shards),
    ]
    if coverage:
        cmd += ["--coverage", coverage]
    if budget:
        cmd += ["--seconds", str(budget)]
    env = os.environ.copy()
    env.setdefault("NEXUS_EVENTS_ALLOW_HTTP", "1")
    print(f"collect {country}: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"import_events.py --countries {country} exited {proc.returncode}")
    doc = load_doc(out)
    return doc.get("counts") or {}


def validate_shrink(before: dict[str, int], after: dict[str, int], max_shrink: float) -> list[str]:
    errors = []
    for key, start in before.items():
        end = after.get(key, 0)
        if start <= 0:
            continue
        dropped = (start - end) / start
        if dropped > max_shrink + 1e-9:
            errors.append(f"{key}: current events shrank from {start} to {end} ({dropped:.0%} > {max_shrink:.0%})")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Archive expired events and refresh a rolling FR/US batch.")
    ap.add_argument("--today", default="", help="YYYY-MM-DD override (America/Denver if omitted)")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    ap.add_argument("--collect", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--fr-limit", type=int, default=DEFAULT_FR_LIMIT, help="max France church window this run (0 = uncapped, time budget applies)")
    ap.add_argument("--us-limit", type=int, default=DEFAULT_US_LIMIT, help="max US church window this run (0 = uncapped, time budget applies)")
    ap.add_argument("--seconds", type=int, default=DEFAULT_SECONDS, help="total collect budget in seconds (0 = no cap)")
    ap.add_argument("--fr-seconds", type=int, default=DEFAULT_FR_SECONDS, help="max seconds spent on France before the US slice")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--coverage", default=DEFAULT_COVERAGE, help="rolling cursor file")
    ap.add_argument("--hot-cap", type=int, default=60)
    ap.add_argument("--matrix-shard", type=int, default=0)
    ap.add_argument("--matrix-shards", type=int, default=1)
    ap.add_argument("--max-shrink", type=float, default=MAX_SHRINK)
    ap.add_argument("--summary", default="", help="write a JSON summary to this path")
    args = ap.parse_args(argv)

    started = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    today = args.today or today_in_denver(now)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    docs = {key: load_doc(path) for key, path in FILES.items()}
    before_current = counts_by_file(docs, today)
    archived = {}
    expired_all = []
    for key, path in FILES.items():
        current, expired = split_expired(docs[key].get("events") or [], today)
        archived[key] = len(expired)
        expired_all.extend(expired)
        docs[key]["events"] = current
        docs[key]["updatedAt"] = now_iso
        write_doc(path, docs[key])
    archived_new = append_archive(args.archive, expired_all, today, now_iso)

    collect_counts = {"FR": {}, "US": {}}
    if args.collect:
        remaining = args.seconds
        # Organisation sites are always included by the importer. France is a short first slice so
        # the much larger US church list keeps most of the GitHub Actions budget.
        fr_budget = 0
        if remaining:
            cap = args.fr_seconds if args.fr_seconds > 0 else remaining
            fr_budget = min(remaining, cap)
        collect_counts["FR"] = run_importer(
            "FR", FILES["FR"], args.fr_limit, remaining, fr_budget,
            workers=args.workers, coverage=args.coverage, hot_cap=args.hot_cap,
            shard=args.matrix_shard, shards=args.matrix_shards,
        )
        used = time.time() - started
        left = max(0, remaining - used) if remaining else 0
        collect_counts["US"] = run_importer(
            "US", FILES["US"], args.us_limit, remaining, left,
            workers=args.workers, coverage=args.coverage, hot_cap=args.hot_cap,
            shard=args.matrix_shard, shards=args.matrix_shards,
        )

    docs = {key: load_doc(path) for key, path in FILES.items()}
    after_current = counts_by_file(docs, today)
    totals = country_totals(docs, today)
    shrink_errors = validate_shrink(before_current, after_current, args.max_shrink)
    # Valid JSON already checked by load_doc. Re-parse the archive too.
    if os.path.exists(args.archive):
        load_doc(args.archive)

    coverage_snap = {}
    if args.collect and args.coverage and os.path.exists(args.coverage):
        try:
            coverage_doc = json.load(open(args.coverage, encoding="utf-8"))
            coverage_snap = {
                "updatedAt": coverage_doc.get("updatedAt"),
                "countries": coverage_doc.get("countries") or {},
                "hasSignal": len(coverage_doc.get("hasSignal") or {}),
                "noSignal": len(coverage_doc.get("noSignal") or {}),
            }
        except (OSError, json.JSONDecodeError, AttributeError):
            coverage_snap = {}
    summary = {
        "today": today,
        "timezone": "America/Denver",
        "runtimeSeconds": round(time.time() - started, 1),
        "archived": archived,
        "archivedAppended": archived_new,
        "currentBefore": before_current,
        "currentAfter": after_current,
        "totals": totals,
        "collect": collect_counts,
        "coverage": coverage_snap,
        "ok": not shrink_errors,
        "errors": shrink_errors,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary:
        write_doc(args.summary, summary)
    if shrink_errors:
        print("VALIDATION FAILED: " + "; ".join(shrink_errors), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
