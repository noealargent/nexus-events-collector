#!/usr/bin/env python3
"""Merge shard outputs into the app's public/events files and archive expired rows.

Churches a shard crawled successfully (status "ok") are replaced with that shard's
events, even if the list is empty. Churches that were not crawled, or that failed
(robots, timeout, HTTP error), keep their still-current rows from the previous
live files. Manual events are kept as-is; scraped duplicates of them are dropped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from collect import dedupe_scraped, still_current, today_in_denver  # noqa: E402
from nightly_refresh import (  # noqa: E402
    append_archive,
    counts_by_file,
    country_totals,
    load_doc,
    validate_shrink,
    write_doc,
)

OK_STATUSES = {"ok"}


def load_logs(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            rows.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict) and isinstance(data.get("logs"), list):
            rows.extend(item for item in data["logs"] if isinstance(item, dict))
    return rows


def visited_ok(logs: list[dict]) -> set[str]:
    return {str(item["id"]) for item in logs if item.get("id") and item.get("status") in OK_STATUSES}


def events_of(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        doc = load_doc(path)
        rows.extend(event for event in doc.get("events") or [] if isinstance(event, dict))
    return rows


def merge_country(previous: list[dict], incoming: list[dict], refreshed: set[str], today: str) -> list[dict]:
    kept = []
    for event in previous:
        if not still_current(event, today):
            continue
        cid = str(event.get("churchId") or "")
        if cid and cid in refreshed:
            continue
        kept.append(event)
    for event in incoming:
        if not still_current(event, today):
            continue
        cid = str(event.get("churchId") or "")
        if cid and cid not in refreshed:
            # A shard wrote a row for a church it did not successfully refresh; ignore it.
            continue
        kept.append(event)
    return kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge collector shards into Nexus public event files.")
    ap.add_argument("--shards-dir", required=True, help="Directory containing shard-*-{FR,US}.json and logs")
    ap.add_argument("--app-root", required=True, help="Checkout of studiopainperdu-app")
    ap.add_argument("--today", default="")
    ap.add_argument("--summary", default="")
    args = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    today = args.today or today_in_denver(now)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    public = os.path.join(args.app_root, "public", "events")
    archive = os.path.join(args.app_root, "docs", "events-archive", "expired.json")
    coverage_path = os.path.join(args.app_root, "docs", "events-archive", "coverage.json")
    files = {
        "FR": os.path.join(public, "index.json"),
        "US": os.path.join(public, "us.json"),
        "manual": os.path.join(public, "manual-events.json"),
    }

    previous = {key: load_doc(path) for key, path in files.items()}
    before = counts_by_file(previous, today)

    # Archive expired rows from the previous live files first.
    expired_all = []
    current_prev = {}
    archived = {}
    for key, doc in previous.items():
        current, expired = [], []
        for event in doc.get("events") or []:
            if isinstance(event, dict) and still_current(event, today):
                current.append(event)
            elif isinstance(event, dict):
                expired.append(event)
        archived[key] = len(expired)
        expired_all.extend(expired)
        current_prev[key] = current
    archived_new = append_archive(archive, expired_all, today, now_iso)

    logs = load_logs(sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-log-*.json"))))
    refreshed = visited_ok(logs)
    incoming = {
        "FR": events_of(sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-FR.json")))),
        "US": events_of(sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-US.json")))),
    }

    merged = {
        "FR": merge_country(current_prev["FR"], incoming["FR"], refreshed, today),
        "US": merge_country(current_prev["US"], incoming["US"], refreshed, today),
        "manual": current_prev["manual"],
    }
    merged["FR"] = dedupe_scraped(merged["FR"], merged["manual"])
    merged["US"] = dedupe_scraped(merged["US"], merged["manual"])

    for key, path in files.items():
        doc = dict(previous[key])
        doc["events"] = merged[key]
        doc["updatedAt"] = now_iso
        write_doc(path, doc)

    after = counts_by_file({key: load_doc(path) for key, path in files.items()}, today)
    totals = country_totals({key: load_doc(path) for key, path in files.items()}, today)
    shrink = validate_shrink(before, after, 0.30)

    # Lightweight coverage snapshot from this night's logs (all sites visited when shards complete).
    coverage = {
        "updatedAt": now_iso,
        "timezone": "America/Denver",
        "noSignalDays": 0,
        "countries": {},
        "hasSignal": {},
        "noSignal": {},
        "refreshedOk": len(refreshed),
        "logCount": len(logs),
    }
    for log in logs:
        sid = log.get("id")
        if not sid or log.get("status") != "ok":
            continue
        if log.get("hasSignal") or log.get("agenda") or log.get("sources"):
            coverage["hasSignal"][sid] = today
        else:
            coverage["noSignal"][sid] = today
    os.makedirs(os.path.dirname(coverage_path), exist_ok=True)
    write_doc(coverage_path, coverage)

    shard_files = {
        "FR": sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-FR.json"))),
        "US": sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-US.json"))),
        "logs": sorted(glob.glob(os.path.join(args.shards_dir, "shard-*-log-*.json"))),
    }
    summary = {
        "today": today,
        "timezone": "America/Denver",
        "archived": archived,
        "archivedAppended": archived_new,
        "currentBefore": before,
        "currentAfter": after,
        "totals": totals,
        "refreshedOk": len(refreshed),
        "logCount": len(logs),
        "shardFiles": {key: len(paths) for key, paths in shard_files.items()},
        "ok": not shrink,
        "errors": shrink,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    last_run = os.path.join(args.app_root, "docs", "events-archive", "last-run.json")
    write_doc(last_run, summary)
    if args.summary:
        write_doc(args.summary, summary)
    if shrink:
        print("VALIDATION FAILED: " + "; ".join(shrink), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
