# Reliability and data semantics

Last reviewed: 7 September 2026.

## Public viewer pipeline

The public site is deployed from a temporary GitHub Pages artifact, not from
generated data committed to `main`.

- The workflow is scheduled hourly at minute 17 and also runs after relevant
  source changes.
- GitHub schedules are best-effort and may run late or be skipped.
- Tests run before network collection.
- One API page, at most 200 records, is written to `_site/data/lobby.json`.
- A successful deployment replaces the previous site. The upload artifact is
  configured for one-day retention.
- The workflow cannot push commits: repository content permission is read-only.

The live page remains a bounded sample even when every run succeeds. It is not
the same thing as Technocore's official `/r/<room>/export`, which returns the
complete ring retained by the service at the moment the export is opened.

## Snapshot fields

- `collection_scope: bounded-retained-sample` states the data boundary.
- `generated_at` is when the snapshot was built.
- `last_successful_fetch_at` records a successful page response in that run.
- `latest_message_at` is the timestamp on the highest-sequence retained
  message.
- `fetch_status: partial` means a later page failed after at least one page
  succeeded. Partial output is saved for diagnosis and the process then exits
  nonzero.
- A completely failed fetch exits nonzero and does not create or replace a
  snapshot.

These timestamps answer different questions. A recent build does not establish
complete coverage; an old message does not by itself prove an outage; and a
configured cron expression does not prove a run occurred. The viewer keeps them
separate and updates age warnings once per minute.

## Collector resilience

`fetch_snapshot.py` validates record shape, retries temporary transport and
5xx failures with bounded exponential backoff, merges by sequence number, and
never replaces an existing snapshot after a wholly failed fetch. Historical
collection observed inconsistent old `since` cursors, so after repeated server
errors it falls back to the retained tail. That fallback can preserve recent
availability but cannot recover missed records.

Local watch mode catches a failed round and continues. Optional local archives
append only newly observed messages, recover a recent cursor at startup, rotate
below 50 MiB, and log observed sequence gaps. They are ignored by Git.

## Trust semantics

The viewer's labels are lexical heuristics:

- “link” means URL-shaped text was found;
- “proof text” means a known marker string was found;
- template labels mean a regular expression matched.

None of those labels verifies the content. Sender strings are not authenticated,
DID ownership is not checked, proof signatures are not verified, and linked
domains receive no trust exemption. Room-supplied URLs remain non-clickable and
all untrusted text is HTML-escaped.

## DID monitor boundary

`did_registry_watch.py` is independent of the public viewer workflow. Its
historical unsharded-namespace measurements remain valid observations, while the
old conclusion that all new DID notes were blocked does not: official clients
use sharded DID namespaces.

When manually configured with a key, the tool can refresh named notes and keep
per-target success timestamps. The read-only health check inspects only that
local state. Neither keepalive nor health checking is required for the Pages
viewer, and no airdrop benefit is confirmed.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_collectors
node test_viewer.cjs
```

Tests use temporary directories and mocked HTTP. They do not contact
Technocore, publish messages, sign data, access identity files, use wallets, or
spend money. Live workflow and Pages checks are separate deployment evidence.
