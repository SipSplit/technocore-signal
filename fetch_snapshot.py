#!/usr/bin/env python3
"""Fetch a Technocore room into a local JSON snapshot.

The Technocore API sends no CORS headers, so a browser page cannot read it
directly. This script does the fetching and writes a snapshot the static
viewer can load from the same origin. It also makes the viewer independent of
origin availability -- the room's HTTP 502s are frequent under load.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://technocore.chat"
PAGE_LIMIT = 200          # server-side maximum
MAX_RETRIES = 8
USER_AGENT = "technocore-signal-viewer/1.0 (+https://github.com/)"


def _get(url: str, timeout: float) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_page(base_url: str, room: str, since: int | None, timeout: float) -> dict | None:
    """Fetch one page. Returns None instead of raising, so a run never loses data.

    The origin returns 500s intermittently, and `?since=` is unreliable in its own
    right: for older sequences it either fails or silently answers with the tail
    instead of the requested range. So after repeated 500s we drop `since` and take
    whatever the head of the room gives us -- merging by sequence number makes that
    safe.
    """
    def build(with_since: bool) -> str:
        params = {"format": "json", "limit": str(PAGE_LIMIT)}
        if with_since and since is not None:
            params["since"] = str(since)
        return f"{base_url.rstrip('/')}/r/{room}?{urlencode(params)}"

    delay = 2.0
    use_since = True
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _get(build(use_since), timeout)
        except HTTPError as error:
            if error.code < 500:
                print(f"  HTTP {error.code} - giving up on this page", file=sys.stderr)
                return None
            reason = f"HTTP {error.code}"
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
            reason = type(error).__name__
        if attempt == 3 and use_since and since is not None:
            use_since = False
            print("  repeated failures - retrying without ?since", file=sys.stderr)
        if attempt == MAX_RETRIES:
            print(f"  {reason} - {MAX_RETRIES} attempts exhausted, keeping what we have",
                  file=sys.stderr)
            return None
        print(f"  {reason}, retry {attempt}/{MAX_RETRIES} in {delay:.0f}s", file=sys.stderr)
        time.sleep(delay)
        delay = min(delay * 2, 60.0)
    return None


def archive_load(directory: Path, room: str) -> dict[int, dict]:
    """Read every archived message back, keyed by sequence number."""
    out: dict[int, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{room}-*.ndjson")):
        with path.open() as handle:
            for line in handle:
                try:
                    message = json.loads(line)
                    out[message["seq"]] = message
                except (json.JSONDecodeError, KeyError):
                    continue
    return out


def archive_dedupe(directory: Path, room: str) -> int:
    """Rewrite archive files that contain duplicate sequences.

    A `merge=union` git merge (see .gitattributes) resolves concurrent appends by
    keeping both sides' lines, which can duplicate a message. Collapsing them here
    keeps the archive canonical without anyone having to resolve a conflict by hand.
    """
    removed = 0
    for path in sorted(directory.glob(f"{room}-*.ndjson")):
        seen: set[int] = set()
        kept: list[str] = []
        with path.open() as handle:
            for line in handle:
                try:
                    seq = json.loads(line)["seq"]
                except (json.JSONDecodeError, KeyError):
                    continue
                if seq in seen:
                    removed += 1
                    continue
                seen.add(seq)
                kept.append(line if line.endswith("\n") else line + "\n")
        if removed:
            path.write_text("".join(kept))
    return removed


def archive_append(directory: Path, room: str, messages: dict[int, dict]) -> int:
    """Append messages to per-day NDJSON files, skipping sequences already stored.

    The snapshot the viewer reads is bounded and gets rewritten every round. The
    archive is append-only and partitioned by day, so a git history of it stays small
    and nothing is ever silently dropped.
    """
    directory.mkdir(parents=True, exist_ok=True)
    known: set[int] = set()
    for existing in directory.glob(f"{room}-*.ndjson"):
        with existing.open() as handle:
            for line in handle:
                try:
                    known.add(json.loads(line)["seq"])
                except (json.JSONDecodeError, KeyError):
                    continue

    by_day: dict[str, list[dict]] = {}
    for seq in sorted(messages):
        if seq in known:
            continue
        by_day.setdefault(messages[seq]["ts"][:10], []).append(messages[seq])

    written = 0
    for day, batch in by_day.items():
        with (directory / f"{room}-{day}.ndjson").open("a") as handle:
            for message in batch:
                handle.write(json.dumps(message, separators=(",", ":")) + "\n")
                written += 1
    return written


def load_existing(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {m["seq"]: m for m in data.get("messages", []) if "seq" in m}


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot a Technocore room to JSON.")
    parser.add_argument("room", nargs="?", default="lobby")
    parser.add_argument("--pages", type=int, default=10,
                        help="pages to fetch, %d messages each (default: 10)" % PAGE_LIMIT)
    parser.add_argument("--out", default="data/lobby.json")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--keep", type=int, default=25000,
                        help="max messages kept in the snapshot (default: 25000)")
    parser.add_argument("--from-seq", type=int, default=None,
                        help="start walking forward from this sequence number")
    parser.add_argument("--archive", metavar="DIR", default="data/archive",
                        help="append new messages to DIR/<room>-YYYY-MM-DD.ndjson "
                             "(append-only; the real archive). Empty string disables.")
    parser.add_argument("--watch", type=int, metavar="SECONDS", default=None,
                        help="keep collecting every SECONDS until interrupted")
    args = parser.parse_args()

    if args.watch:
        print(f"watching {args.room} every {args.watch}s - Ctrl+C to stop")
        while True:
            try:
                collect(args)
            except KeyboardInterrupt:
                print("\nstopped")
                return
            except Exception as error:          # never let one bad round kill the collector
                print(f"round failed: {error}", file=sys.stderr)
            time.sleep(args.watch)

    collect(args)


def collect(args: argparse.Namespace) -> None:

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    messages = load_existing(out_path)
    if messages:
        print(f"existing snapshot: {len(messages)} messages "
              f"(seq {min(messages)}-{max(messages)})")

    since = args.from_seq
    if since is None and messages:
        since = max(messages)

    added = 0
    for page in range(1, args.pages + 1):
        payload = fetch_page(args.base_url, args.room, since, args.timeout)
        if payload is None:
            print(f"page {page}: unavailable, stopping this round")
            break
        batch = payload.get("messages", [])
        if not batch:
            print(f"page {page}: empty, stopping")
            break
        new = [m for m in batch if m["seq"] not in messages]
        for message in new:
            messages[message["seq"]] = message
        added += len(new)
        last = payload.get("last_seq", batch[-1]["seq"])
        print(f"page {page}: seq {payload.get('first_seq')}-{last}, {len(new)} new")
        if len(batch) < PAGE_LIMIT:
            break            # caught up with the head of the room
        since = last

    if args.archive:
        archive_dir = Path(args.archive)
        archived = archive_append(archive_dir, args.room, messages)
        if archived:
            print(f"archived {archived} message(s) to {args.archive}/")
        collapsed = archive_dedupe(archive_dir, args.room)
        if collapsed:
            print(f"collapsed {collapsed} duplicate line(s) from a union merge")
        # The archive is the source of truth; the snapshot is derived from it, so a
        # conflicted snapshot never needs resolving -- it is simply regenerated.
        messages = {**archive_load(archive_dir, args.room), **messages}

    ordered = [messages[s] for s in sorted(messages)][-args.keep:]
    snapshot = {
        "room": args.room,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(ordered),
        "first_seq": ordered[0]["seq"] if ordered else None,
        "last_seq": ordered[-1]["seq"] if ordered else None,
        "messages": ordered,
    }
    out_path.write_text(json.dumps(snapshot, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024
    print(f"wrote {out_path} - {len(ordered)} messages, +{added} new, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
