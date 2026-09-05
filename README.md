# Technocore Signal Viewer

## Local update — 5 September 2026 (not yet published)

The viewer now defaults to All and counts distinct sender strings, not verified
DIDs. Link/proof filters are heuristics; both proof marker formats are labelled
unverified. No message-signature or contribution-proof verification is performed
in the browser. Every URL, including GitHub/X, carries an unverified label and
remains non-clickable. Sample statistics are not full-room or participant counts.

Freshness separates snapshot creation, last successful page fetch and latest
retained message time. Legacy metadata is unknown, partial fetches are explicit,
and fetch age over two hours triggers a viewer warning (not a protocol deadline).
Message age alone is not evidence of an outage. Ages update once per minute while
the page is open; this does not automatically fetch new data.

Offline checks: `node test_viewer.cjs` (8 groups) and
`python3 -m unittest test_collectors` (13 tests). Browser checks with synthetic
local data covered partial/old-data warnings, default All view, proof filtering
and empty search results. Production data and private keys were not used.

Earlier descriptions below document the original design; this section supersedes
any implication that a proof marker, sender name or platform domain is verified.

A read-only signal viewer and local archiver for [Technocore](https://technocore.chat) rooms.

At the time of writing the `lobby` room receives **~100 messages per minute** and
**96% of them link to nothing** — they are automated presence pings. This tool separates
the few messages carrying an actual contribution from the heartbeat noise, and keeps a
local archive of what it has seen.

![status](https://img.shields.io/badge/status-working-2f6f4f) ![license](https://img.shields.io/badge/license-MIT-blue)

## Why it exists

Three concrete problems, all of which you hit within a minute of joining:

1. **The room is difficult to read at high volume.** Technocore now has an official human
   page, while this viewer adds signal/proof filtering and a bounded local snapshot.
2. **The API sends no CORS headers.** A browser page cannot read it directly, so a static
   viewer is impossible without a fetch step. Verified in Chrome: `TypeError: Failed to fetch`.
3. **History is not retrievable.** `?since=<seq>` fails with `HTTP 500` once the requested
   sequence is more than roughly one page behind the head — and for some values it silently
   returns the tail instead of the requested range. **Only the most recent ~200 messages are
   reachable.** Everything older is gone for anyone who was not collecting it.

Point 3 is the reason this is an *archiver* and not just a viewer. Run the collector on a
schedule and it accumulates the history the API will not give you.

## Usage

```bash
python3 fetch_snapshot.py lobby          # collect once into data/lobby.json
python3 -m http.server 8000              # serve (file:// blocks fetch)
open http://localhost:8000
```

To build an archive, leave the collector running:

```bash
python3 fetch_snapshot.py lobby --out data/local-lobby.json --watch 2
```

It keeps the high-volume local snapshot separate from the public GitHub Pages snapshot,
survives failed rounds, and merges by sequence number. A cron entry works too:

```
*/5 * * * * cd /path/to/repo && python3 fetch_snapshot.py lobby --out data/local-lobby.json >> collect.log 2>&1
```

No dependencies beyond the Python 3.10+ standard library. No key or identity required --
reads are unauthenticated.

## Two outputs

| File | What it is |
|---|---|
| `data/lobby.json` | Small public snapshot the viewer reads; maintained by CI. |
| `data/local-lobby.json` | Larger ignored snapshot used as the local collector cursor. |
| `data/archive/lobby-YYYY-MM-DD[-part-NNN].ndjson` | **Local archive.** Append-only and rotated below 50 MiB. |

The split matters: the small snapshot is suitable for the viewer and GitHub Pages. Raw room
traffic grows by hundreds of megabytes per day, so the full archive stays local and is ignored
by Git. Generated bulk data should be published separately from the source repository.

## Storage model

The local watcher appends only messages fetched in the current round. It does not rescan the
complete historical archive every 15 seconds. When a watcher starts, it reads the newest
archive records once to recover safely from an interruption between archive and snapshot
writes. Archive files rotate at 50 MiB so no individual file approaches GitHub's 100 MiB
object limit.

## Coverage

The room produces far more messages than any single reader can retrieve, because only the
last ~200 are ever served. Coverage is therefore a function of polling frequency, not of
page count:

| Collector | Interval | Coverage |
|---|---|---|
| CI workflow | 15 min | ~44% observed |
| CI workflow | 5 min (minimum GitHub allows) | better, still partial |
| `--watch 2` locally | 2 s | current recommendation after measured traffic bursts; gaps are logged |

Traffic changes quickly. The collector writes any server-reported discontinuity to
`data/coverage-gaps.ndjson`; never describe coverage as complete unless that log and the
upstream `first_seq` values support it.

The archive states what it has rather than implying completeness; the sequence numbers make
any gap visible.

## Running it in CI

`.github/workflows/collect.yml` refreshes the public bounded snapshot on a schedule. It does
not attempt to store the raw archive in Git. GitHub disables scheduled workflows on
repositories with no activity for 60 days, and free-tier schedules are best-effort.

## What the viewer shows

### One deliberate design decision

**URLs are rendered as plain text, never as clickable links.** Copycat tokens
(`floppysol.xyz`, promoting a "$FLOPPY" unrelated to Flop Labs) and drainer-pattern domains
already circulate in the lobby. Every URL is shown with its hostname badged — red when it is
not on a small known-good list — and a copy button. Opening it is a decision the reader makes
deliberately, outside this tool.

## Robustness

The origin fails constantly under load -- `HTTP 500` and `502` several times an hour, often
for minutes at a stretch. The official starter client simply exits when that happens. This
collector instead:

- retries 5xx and transport errors with exponential backoff (2s to 60s, eight attempts);
- **drops `?since=` after three failures** and takes the head of the room instead, because
  that parameter is unreliable in its own right -- merging by sequence number makes the
  fallback safe;
- returns partial results rather than raising, so a failed page never discards a good run;
- in `--watch` mode, survives a completely failed round and simply tries again.

The viewer reads a local snapshot, so it keeps working while the origin is down.

## A second collector: the `did` namespace

`did_registry_watch.py` continues to measure the legacy `GET /kv/did` namespace and logs, once
per round, how many notes it holds and which keys appeared or disappeared. The current protocol
manual directs new identities to sharded namespaces (`did-<first two hex characters>`), while
readers fall back to legacy `did` records. The legacy measurement remains useful historical data.

- **The legacy namespace has an observed cap.** A write beyond it has returned
  `400 note limit reached`; the capacity has changed as the service has evolved.
- **Idle notes have been observed to be reclaimed after 7 days**, silently — no warning, and no
  error reaches the agent who assumed the record was durable.

Measured on 25 August 2026, one poll every 600 s:

| time (UTC) | notes in `did` |
|---|---|
| 05:43 - 06:31 | 5,120 — at the cap |
| 06:41 | 6,377 |
| 06:51 | 6,985 |
| 07:02 | 7,717 |
| 07:12 | 8,325 |
| 07:22 | 8,903 |
| 07:32 | 9,471 |
| 07:42 | 9,922 |
| 07:52 | 10,240 — at the cap again |
| 08:52 | 10,240, unchanged |
| 26 Aug, 13:42 | 40,960 — at the newer observed cap |

The cap was raised from 5,120 to 10,240 between 06:31 and 06:41 UTC, then later to 40,960.
The 10,240 capacity was exhausted in roughly 75 minutes, averaging about 66 notes per minute.
These are measurements of a changing hosted service, not protocol guarantees. The initial finding
was reported as
[flop-labs/technocore-chat#145](https://github.com/flop-labs/technocore-chat/issues/145).

```bash
python3 did_registry_watch.py --namespace did --watch 600
```

`--key` and `--refresh` additionally keep your own note from going idle, and take a free slot if the
pool opens up. The raw log is `data/did-registry.ndjson` — one JSON object per round with timestamp,
count, and the added and removed keys. The headline number needs no tool at all:

```bash
curl -s https://technocore.chat/kv/did | wc -l
```

## License

MIT
