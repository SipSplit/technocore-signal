# Reliability changes — 5 September 2026

## Scope and activation

These changes are local, not yet pushed to GitHub. The installed macOS LaunchAgent
points directly at this checkout's `did_registry_watch.py`, so its next scheduled
run uses the updated code. No manual network write was used to test the changes.
The public collector and website remain unchanged until publication is approved.
Release preparation was checked against upstream `0be887a` without conflicting
code changes. Malformed room payloads are treated as failed fetches, not empty success.

## Keepalive

- Refresh the configured notes before querying namespace inventory. A listing outage
  must not prevent refreshing notes.
- Attempt every configured target when another returns an HTTP failure. Preserve
  each successful target's timestamp; failed writes do not advance it.
- Failed refreshes or listings produce a nonzero one-shot exit status. Watch mode
  logs failures and continues its normal polling cycle.
- The default refresh interval remains 24 hours; no additional identities or targets.
- Logs include the age of the last successful refresh.
- A read-only health check requires neither network access nor a private key:

```sh
python3 did_registry_watch.py --check-health \
  --out data/local-did-shard.ndjson \
  --refresh did-2d/9bf18ff492666a \
  --refresh contrib/2d9bf18ff492666a
```

It fails if history is missing/invalid or any target is older than 48 hours
(`--max-age-hours` can change this warning threshold). This is local evidence of
HTTP success, not independent confirmation that a world-writable note still has
the expected contents. The daily monitor is configured to check these ages;
its first scheduled run with this integration remains to be verified.

## Snapshot semantics

- A completely failed fetch exits with an error and leaves the previous snapshot
  byte-for-byte unchanged; it does not create a new empty snapshot.
- A successful empty response is not a network outage.
- Partial results are saved with `fetch_status: partial`, followed by a nonzero
  exit status. GitHub's normal subsequent commit step therefore does not publish
  partial results as if the run succeeded.
- `generated_at` is the time a snapshot was built after receiving data.
- `last_successful_fetch_at` records successful page retrieval in that run, not
  a guarantee of complete coverage or of catching up with the live room.
- `latest_message_at` is the timestamp of the highest-sequence retained message.
- On total failure, the attempt time is in the job/run log; snapshot timestamps
  are deliberately not advanced.
- The local viewer now displays these fields, flags partial/stale/unknown fetches,
  and distinguishes message age from fetch age.

## Test plan and verification

Run `python3 -m unittest -v test_collectors`.
All HTTP calls in new tests are mocked; files are created only in temporary test
directories. No live notes, production datasets or secrets are used.

Coverage targets: listing outage with independent refresh, partial refresh failure,
refresh throttling, stale local health without networking, full snapshot outage,
first-run outage, successful empty response, partial multi-page response, plus the
four existing archive/retry regression tests and malformed room responses.
All 13 collector tests and 8 viewer test groups passed on 5 September.
The collector workflow now runs these tests before fetching.

Remaining boundaries: this is not a live write test, and does not prove API
availability, note contents, account eligibility or a future airdrop allocation.
