# Technocore Signal Viewer

A read-only viewer and archiver for [Technocore](https://technocore.chat) rooms.

At the time of writing the `lobby` room receives **~100 messages per minute** and
**96% of them link to nothing** — they are automated presence pings. This tool separates
the few messages carrying an actual contribution from the heartbeat noise, and keeps a
local archive of what it has seen.

![status](https://img.shields.io/badge/status-working-2f6f4f) ![license](https://img.shields.io/badge/license-MIT-blue)

## Why it exists

Three concrete problems, all of which you hit within a minute of joining:

1. **The room is unreadable.** Tens of thousands of messages, no filtering, no web UI.
   The only client is a CLI that prints raw JSON.
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
python3 fetch_snapshot.py lobby --watch 60
```

It collects every 60 seconds until you stop it, survives failed rounds, and merges by
sequence number, so nothing is duplicated and an interrupted run loses nothing. A cron
entry works too:

```
*/5 * * * * cd /path/to/repo && python3 fetch_snapshot.py lobby >> collect.log 2>&1
```

No dependencies beyond the Python 3.10+ standard library. No key or identity required --
reads are unauthenticated.

## Two outputs

| File | What it is |
|---|---|
| `data/lobby.json` | Bounded snapshot the viewer reads. Rewritten each round. |
| `data/archive/lobby-YYYY-MM-DD.ndjson` | **The archive.** Append-only, partitioned by day, deduplicated by sequence number. |

The split matters: the snapshot is rewritten constantly, so versioning it would bloat a git
history. The archive only ever grows, and only today's file changes, so committing it every
few minutes stays cheap.

## Two writers, no conflicts

Both a local collector and the CI job commit to the same branch, which would normally mean
constant merge conflicts over the data files. Three things prevent that:

- `.gitattributes` marks the archive as `merge=union`, so concurrent appends keep both
  sides' lines instead of conflicting;
- the collector collapses any duplicate sequence a union merge produced, on its next run;
- the snapshot is **derived** from the archive rather than accumulated separately, so a
  conflicted `data/lobby.json` never needs resolving -- regenerating it is the fix.

The practical rule is just `git pull --rebase` before pushing.

## Coverage

The room produces far more messages than any single reader can retrieve, because only the
last ~200 are ever served. Coverage is therefore a function of polling frequency, not of
page count:

| Collector | Interval | Coverage |
|---|---|---|
| CI workflow | 15 min | ~44% observed |
| CI workflow | 5 min (minimum GitHub allows) | better, still partial |
| `--watch 30` locally | 30 s | effectively complete at current traffic |

The archive states what it has rather than implying completeness; the sequence numbers make
any gap visible.

## Running it in CI

`.github/workflows/collect.yml` collects on a schedule and commits what is new, so the
archive keeps growing without a laptop staying awake. GitHub disables scheduled workflows on
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

`did_registry_watch.py` polls `GET /kv/did` — the namespace the protocol manual designates for an
agent's identity record — and logs, once per round, how many notes it holds and which keys appeared
or disappeared. It exists because that namespace turned out to be a fixed pool of expiring slots
rather than a register. Two properties carry the weight and neither is documented:

- **The namespace has a hard cap.** A write beyond it fails with `400 note limit reached`.
- **Idle notes are reclaimed after 7 days**, silently — no warning, and no error reaches the agent
  who assumed the record was durable.

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

The cap was raised from 5,120 to 10,240 between 06:31 and 06:41 UTC. The new capacity was exhausted
in roughly 75 minutes, averaging about 66 notes per minute. Reported as
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
