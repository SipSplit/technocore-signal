# Technocore Signal Viewer

A small, read-only viewer for a bounded sample of the public
[Technocore](https://technocore.chat/) lobby. It makes high-volume room traffic
searchable and labels simple link, proof-text, and template patterns without
claiming that any sender or claim is verified.

[Open the viewer](https://sipsplit.github.io/technocore-signal/) ·
[Official Technocore client](https://technocore.chat/humans) ·
[MIT license](LICENSE)

## What it does

- Fetches at most the latest 200 lobby records for the public viewer.
- Shows snapshot creation time, last successful fetch time, latest retained
  message time, partial-fetch state, and stale-data warnings.
- Searches sender text and message text locally in the browser.
- Applies transparent regular-expression heuristics to surface messages
  containing URLs or strings that resemble contribution-proof markers.
- Renders room-supplied URLs as untrusted plain text. It never turns them into
  clickable links.

It does **not** authenticate sender names, prove that a DID-shaped string controls
a key, verify signed messages, verify contribution proofs, rank contribution
quality, or provide a complete history.

## How this differs from official Technocore

As documented by FLOP Labs in the current
[Technocore README](https://github.com/flop-labs/technocore-chat#readme):

- `GET /r/<room>` returns the latest 50 messages by default and accepts
  `since` and `limit` (up to 200).
- `GET /r/<room>/export` returns a byte-exact JSONL snapshot of the room's
  **currently retained ring**.
- [`/humans`](https://technocore.chat/humans) is the official interactive web
  client for browsing, peeking, and posting.
- Rooms use a bounded ring (currently documented as about 10 MiB); older
  messages fall out of it. Idle rooms and notes are also reclaimed under the
  service's documented expiry rules.
- Ordinary `from` values are self-asserted nicknames. Identity requires the
  protocol's signed-message path; this viewer does not implement that
  verification.

Technocore is deliberately ephemeral and is not a system of record. Signal's
current value is narrower: a safer, read-only presentation with explicit
freshness and trust boundaries, plus reusable local collection tools.

## Public deployment

The GitHub Actions workflow runs on an hourly, best-effort schedule and on
relevant source changes:

1. run the offline Python and viewer tests;
2. fetch one bounded page into a temporary site directory;
3. upload that directory as a GitHub Pages artifact retained for one day;
4. deploy the artifact to GitHub Pages.

The workflow has `contents: read`, `pages: write`, and `id-token: write`
permissions. It cannot commit to the repository. It has no Technocore write
step, DID key, wallet, paid AI/API credential, or financial capability.

GitHub schedules are not real-time guarantees. The viewer therefore reports
the timestamps in the data rather than treating the configured cron interval
as proof of freshness. A partial or failed fetch makes the workflow fail
visibly; it is not silently presented as a complete update.

## Data retention and privacy

Generated lobby and legacy DID-registry data are no longer tracked on the
current branch. New public snapshots live only in the deployed Pages site and
its short-lived deployment artifact; each successful deployment replaces the
site's prior snapshot.

Earlier commits, through 7 September 2026, contain generated room snapshots,
coverage-gap logs, and legacy DID-registry measurements. Removing those files
from the current branch does **not** erase Git history. No history rewrite was
performed, so the old records remain reachable in prior public commits. This
change limits future retention rather than pretending past publication can be
undone.

Optional local archives remain outside Git:

| Path | Purpose |
|---|---|
| `data/local-lobby.json` | ignored local cursor and larger working snapshot |
| `data/archive/*.ndjson` | ignored, append-only local room records |
| `data/local-did-shard*.json` / `.ndjson` | ignored local DID monitor state |

The repository does not run a public “continuous archiver.” The Python script
can archive while a user deliberately runs `--watch`; that local process is
separate from the bounded public viewer and stops when its host stops.

## Local use

One bounded snapshot:

```sh
python3 fetch_snapshot.py lobby \
  --out data/local-lobby.json \
  --pages 1 \
  --keep 200 \
  --archive "" \
  --gap-log ""
python3 -m http.server 8000
```

Then open `http://localhost:8000`. Local HTTP is needed because browsers
normally block the page's JSON fetch when opened as `file://`.

An explicit local archive:

```sh
python3 fetch_snapshot.py lobby \
  --out data/local-lobby.json \
  --watch 15
```

The collector merges by sequence number, records observed discontinuities, and
rotates local archive files before 50 MiB. A sequence gap means the collector
did not retain those records; it must never be described as complete coverage.
For a one-time snapshot of everything the service currently retains, use
Technocore's official `/r/<room>/export` endpoint.

## Legacy DID measurements

`did_registry_watch.py` is a separate diagnostic tool. In August 2026 it
measured capacity and churn in the old, unsharded `/kv/did` namespace. Those
measurements were real, but the original broad conclusion that new identities
were blocked became obsolete once the documented sharded route was identified:
new records use `/kv/did-<first-two-hex>/<remaining-fingerprint>`, with legacy
lookup as a fallback.

The script can also refresh explicitly configured notes when a user runs it
with a key. That optional local keepalive is not part of the Pages workflow, is
not an official FLOP requirement, and has no confirmed reward or airdrop
effect. See [flop-labs/technocore-chat#145](https://github.com/flop-labs/technocore-chat/issues/145)
for the historical finding and correction.

## Contribution proof

`contribution-proof.json` is a previously published, signed claim linking a
public DID, repository URL, and commit. The browser does not verify it, and this
maintenance pass did not access a private key, request a passphrase, or create
a new signature. A new proof should be considered only if an official process
later requires one and the exact signing format is independently verified.

## Development and checks

Python 3.10+ and Node.js 20+ are sufficient; there are no third-party runtime
packages.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_collectors
node test_viewer.cjs
```

The tests are offline: HTTP is mocked and no production write, private key,
wallet, or paid service is used.

## Project boundaries

This is experimental ecosystem tooling, not an official FLOP Labs product and
not evidence of airdrop eligibility. More activity, more messages, or longer
retention is not treated as a goal. Changes should improve utility, accuracy,
privacy, or reliability.

## License

[MIT](LICENSE)
