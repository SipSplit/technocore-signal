#!/usr/bin/env python3
"""Fetch a Technocore room into a local JSON snapshot.

The Technocore API sends no CORS headers, so a browser page cannot read it
directly. This script does the fetching and writes a snapshot the static
viewer can load from the same origin. It also makes the viewer independent of
origin availability -- the room's HTTP 502s are frequent under load.
"""

from __future__ import annotations

import argparse
from collections import deque
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
DEFAULT_ARCHIVE_MAX_MB = 50


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


def archive_recent(directory: Path, room: str, keep: int) -> dict[int, dict]:
    """Load only the newest archive records, once when a watcher starts.

    This recovers cleanly if a process stopped after appending to the archive but
    before rewriting the snapshot. It deliberately does not run on every polling
    round: archive size must not make collection progressively slower.
    """
    if not directory.is_dir() or keep <= 0:
        return {}
    paths = sorted(directory.glob(f"{room}-*.ndjson"),
                   key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    lines: deque[str] = deque(maxlen=keep)
    for path in paths:
        remaining = keep - len(lines)
        if remaining <= 0:
            break
        with path.open() as handle:
            recent = deque(handle, maxlen=remaining)
        lines.extendleft(reversed(recent))
    out: dict[int, dict] = {}
    for line in lines:
        try:
            message = json.loads(line)
            out[message["seq"]] = message
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def archive_target(directory: Path, room: str, day: str, max_bytes: int) -> Path:
    """Return an append target, rotating before a file approaches host limits."""
    base = directory / f"{room}-{day}.ndjson"
    if not base.exists() or base.stat().st_size < max_bytes:
        return base
    part = 2
    while True:
        candidate = directory / f"{room}-{day}-part-{part:03d}.ndjson"
        if not candidate.exists() or candidate.stat().st_size < max_bytes:
            return candidate
        part += 1


def archive_append(directory: Path, room: str, messages: dict[int, dict],
                   max_bytes: int) -> int:
    """Append only messages fetched during this round to size-bounded local files."""
    directory.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for seq in sorted(messages):
        by_day.setdefault(messages[seq]["ts"][:10], []).append(messages[seq])

    written = 0
    for day, batch in by_day.items():
        path = archive_target(directory, room, day, max_bytes)
        with path.open("a") as handle:
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
                        help="append new messages to size-bounded local NDJSON files. "
                             "Empty string disables.")
    parser.add_argument("--archive-max-mb", type=int, default=DEFAULT_ARCHIVE_MAX_MB,
                        help="rotate local archive files at this size (default: 50 MiB)")
    parser.add_argument("--gap-log", default="data/coverage-gaps.ndjson",
                        help="append detected room-sequence gaps here. Empty string disables.")
    parser.add_argument("--watch", type=int, metavar="SECONDS", default=None,
                        help="keep collecting every SECONDS until interrupted")
    args = parser.parse_args()

    if args.watch:
        print(f"watching {args.room} every {args.watch}s - Ctrl+C to stop")
        while True:
            try:
                collect(args)
                time.sleep(args.watch)
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
    if args.archive and not getattr(args, "_archive_recovered", False):
        recent = archive_recent(Path(args.archive), args.room, args.keep)
        messages = {**recent, **messages}
        args._archive_recovered = True
    if messages:
        print(f"existing snapshot: {len(messages)} messages "
              f"(seq {min(messages)}-{max(messages)})")

    since = args.from_seq
    if since is None and messages:
        since = max(messages)

    added = 0
    fetched: dict[int, dict] = {}
    successful_pages = 0
    fetch_failed = False
    for page in range(1, args.pages + 1):
        payload = fetch_page(args.base_url, args.room, since, args.timeout)
        valid_payload = (isinstance(payload, dict)
                         and isinstance(payload.get("messages"), list)
                         and all(isinstance(m, dict)
                                 and isinstance(m.get("seq"), int)
                                 and not isinstance(m["seq"], bool)
                                 and m["seq"] >= 0
                                 and all(isinstance(m.get(field), str)
                                         for field in ("ts", "from", "text"))
                                 for m in payload["messages"]))
        if not valid_payload:
            fetch_failed = True
            print(f"page {page}: unavailable or invalid response, stopping this round")
            break
        successful_pages += 1
        batch = payload.get("messages", [])
        if not batch:
            print(f"page {page}: empty, stopping")
            break
        first = payload.get("first_seq", batch[0]["seq"])
        if since is not None and first > since + 1:
            missed = first - since - 1
            print(f"  COVERAGE GAP: expected {since + 1}, received {first} "
                  f"({missed} sequence(s) unavailable)", file=sys.stderr)
            if args.gap_log:
                gap_path = Path(args.gap_log)
                gap_path.parent.mkdir(parents=True, exist_ok=True)
                record = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "room": args.room,
                    "after_seq": since,
                    "first_received": first,
                    "missing_sequences": missed,
                }
                with gap_path.open("a") as handle:
                    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        new = [m for m in batch if m["seq"] not in messages]
        for message in new:
            messages[message["seq"]] = message
            fetched[message["seq"]] = message
        added += len(new)
        last = payload.get("last_seq", batch[-1]["seq"])
        print(f"page {page}: seq {payload.get('first_seq')}-{last}, {len(new)} new")
        if len(batch) < PAGE_LIMIT:
            break            # caught up with the head of the room
        since = last

    if not successful_pages:
        raise RuntimeError("No successful fetch; existing snapshot left unchanged")

    if args.archive:
        archive_dir = Path(args.archive)
        max_bytes = max(args.archive_max_mb, 1) * 1024 * 1024
        archived = archive_append(archive_dir, args.room, fetched, max_bytes)
        if archived:
            print(f"archived {archived} message(s) to {args.archive}/")

    ordered = [messages[s] for s in sorted(messages)][-args.keep:]
    snapshot = {
        "room": args.room,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fetch_status": "partial" if fetch_failed else "ok",
        "last_successful_fetch_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "latest_message_at": ordered[-1].get("ts") if ordered else None,
        "count": len(ordered),
        "first_seq": ordered[0]["seq"] if ordered else None,
        "last_seq": ordered[-1]["seq"] if ordered else None,
        "messages": ordered,
    }
    out_path.write_text(json.dumps(snapshot, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024
    print(f"wrote {out_path} - {len(ordered)} messages, +{added} new, {size_kb:.0f} KB")
    if fetch_failed:
        raise RuntimeError("Partial fetch saved; collection did not complete successfully")


if __name__ == "__main__":
    main()
