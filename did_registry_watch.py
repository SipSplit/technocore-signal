#!/usr/bin/env python3
"""Watch the /kv/did namespace: measure expiry churn, and claim a slot if one frees.

The namespace is capped at 5120 notes and was already full on 2026-08-25, one day
into the airdrop rush. Notes idle for 7 days are reclaimed, so it is not a registry
but a pool of expiring slots. This records the churn and takes a slot when one opens.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://technocore.chat"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
UA = "technocore-signal-registry-watch/1.0"
MAX_RETRIES = 4


def get(path: str, timeout: float) -> tuple[int, str]:
    request = Request(f"{BASE}{path}", headers={"User-Agent": UA})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (URLError, TimeoutError, OSError) as error:
        return 0, f"{type(error).__name__}: {error}"


def get_with_retry(path: str, timeout: float) -> tuple[int, str]:
    """Retry temporary origin failures without making one bad minute a data gap."""
    delay = 2.0
    status, body = 0, ""
    for attempt in range(1, MAX_RETRIES + 1):
        status, body = get(path, timeout)
        if status == 200 or (status and status < 500):
            return status, body
        if attempt < MAX_RETRIES:
            print(f"  temporary HTTP {status or 'transport'}; retrying in {delay:.0f}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return status, body


def post(path: str, payload: dict, timeout: float) -> tuple[int, str]:
    body = json.dumps(payload).encode()
    request = Request(f"{BASE}{path}", data=body, method="POST",
                      headers={"User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (URLError, TimeoutError, OSError) as error:
        return 0, f"{type(error).__name__}: {error}"


def parse_keys(text: str) -> set[str]:
    """Take the key names out of a namespace listing, tolerant of its exact shape."""
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = line.split()[0].rstrip(",").split("/")[-1]
        if NAME_RE.match(candidate):
            keys.add(candidate)
    return keys


def keepalive(args: argparse.Namespace) -> None:
    """Rewrite our own notes before the 7-day idle reclaim can take them.

    Rewriting every round would burn the write budget for nothing, so each target
    carries its own last-written timestamp and is touched at most once per
    --refresh-hours.
    """
    if not args.refresh:
        return
    state_path = Path(args.out).with_suffix(".refresh.json")
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}

    now = time.time()
    failures = []
    state_path.parent.mkdir(parents=True, exist_ok=True)
    for target in args.refresh:
        parts = target.split("/")
        if len(parts) != 2 or not all(NAME_RE.fullmatch(part) for part in parts):
            failures.append(target)
            continue
        if state.get(target, 0) + args.refresh_hours * 3600 > now:
            print(f"  /kv/{target}: last successful refresh age (hours): "
                  f"{(now - state[target]) / 3600:.2f}; not due")
            continue
        status, body = post(f"/kv/{target}", {"value": args.value}, args.timeout)
        if status == 200:
            state[target] = now
            print(f"  refreshed /kv/{target}")
        else:
            failures.append(target)
            print(f"  refresh of /kv/{target} failed: HTTP {status} {body.strip()[:100]}",
                  file=sys.stderr)
        last_success = state.get(target)
        age = (now - last_success) / 3600 if last_success else None
        print(f"  /kv/{target}: last successful refresh age (hours): {age}")
    state_path.write_text(json.dumps(state))
    if failures:
        raise RuntimeError("refresh failed for: " + ", ".join(failures))


def check_health(args: argparse.Namespace) -> None:
    """Read only local refresh timestamps; do not contact the network or a key."""
    if not args.refresh:
        raise RuntimeError("health check requires at least one --refresh target")
    try:
        state = json.loads(Path(args.out).with_suffix(".refresh.json").read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError("refresh history unavailable") from error
    if not isinstance(state, dict):
        raise RuntimeError("invalid refresh history")
    unhealthy = []
    now = time.time()
    for target in args.refresh:
        stamp = state.get(target)
        valid = (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                 and math.isfinite(stamp) and 0 < stamp <= now)
        age = (now - stamp) / 3600 if valid else None
        print(f"{target}: last successful refresh age (hours): {age}")
        if age is None or age > args.max_age_hours:
            unhealthy.append(target)
    if unhealthy:
        raise RuntimeError("refresh missing or stale: " + ", ".join(unhealthy))


def round_once(args: argparse.Namespace) -> None:
    # Refresh must not depend on an unrelated namespace listing being available.
    refresh_error = None
    try:
        keepalive(args)
    except Exception as error:
        refresh_error = error
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    status, text = get_with_retry(f"/kv/{args.namespace}", args.timeout)
    if status != 200:
        print(f"{stamp} listing failed: HTTP {status} {text[:120]}", file=sys.stderr)
        raise RuntimeError(f"listing failed: HTTP {status}; refresh error: {refresh_error}")
    keys = parse_keys(text)

    state_path = Path(args.out).with_suffix(".keys.json")
    previous = set(json.loads(state_path.read_text())) if state_path.exists() else set()

    added = sorted(keys - previous) if previous else []
    removed = sorted(previous - keys) if previous else []

    record = {"ts": stamp, "namespace": args.namespace, "count": len(keys),
              "added": added, "removed": removed}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "a") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    state_path.write_text(json.dumps(sorted(keys)))

    note = f"{stamp} {args.namespace}: {len(keys)} keys"
    if previous:
        note += f"  (+{len(added)} / -{len(removed)})"
    print(note)

    if refresh_error is not None:
        raise RuntimeError(f"refresh failed: {refresh_error}") from refresh_error

    if not args.key:
        return
    if args.key in keys:
        print(f"  slot {args.key} is held")
        return
    status, body = post(f"/kv/{args.namespace}/{args.key}",
                        {"if_absent": True, "value": args.value}, args.timeout)
    if status == 200:
        print(f"  *** CLAIMED /kv/{args.namespace}/{args.key} -- {body.strip()[:120]}")
    elif status == 409:
        print("  lost the race to another writer")
    else:
        print(f"  claim not possible yet: HTTP {status} {body.strip()[:100]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="did")
    parser.add_argument("--key", default=None, help="key to claim once a slot frees")
    parser.add_argument("--value", default="", help="note body to write on claim")
    parser.add_argument("--out", default="data/did-registry.ndjson")
    parser.add_argument("--refresh", action="append", metavar="NS/KEY", default=[],
                        help="rewrite this note periodically so it is never reclaimed "
                             "(notes idle for 7 days are deleted). Repeatable.")
    parser.add_argument("--refresh-hours", type=float, default=24.0,
                        help="how often a --refresh note is rewritten (default: 24)")
    parser.add_argument("--watch", type=int, metavar="SECONDS", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--check-health", action="store_true",
                        help="read local refresh ages only; never writes or contacts the server")
    parser.add_argument("--max-age-hours", type=float, default=48.0)
    args = parser.parse_args()

    if not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0:
        raise SystemExit("error: --max-age-hours must be finite and positive")
    if args.check_health:
        check_health(args)
        return

    if (args.key or args.refresh) and not args.value:
        raise SystemExit("error: --key and --refresh both need --value")

    if not args.watch:
        round_once(args)
        return
    print(f"watching /kv/{args.namespace} every {args.watch}s - Ctrl+C to stop")
    while True:
        try:
            round_once(args)
        except KeyboardInterrupt:
            print("\nstopped")
            return
        except Exception as error:
            print(f"round failed: {error}", file=sys.stderr)
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
