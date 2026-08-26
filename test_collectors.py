import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import did_registry_watch
import fetch_snapshot


def message(seq: int, day: str = "2026-08-26") -> dict:
    return {"seq": seq, "ts": f"{day}T00:00:00Z", "from": "did:key:test", "text": "ok"}


class ArchiveTests(unittest.TestCase):
    def test_rotates_before_configured_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            base = directory / "lobby-2026-08-26.ndjson"
            base.write_text("x" * 1024)
            written = fetch_snapshot.archive_append(
                directory, "lobby", {1: message(1)}, max_bytes=100
            )
            self.assertEqual(written, 1)
            self.assertTrue((directory / "lobby-2026-08-26-part-002.ndjson").exists())

    def test_recent_records_are_recovered_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "lobby-2026-08-26.ndjson"
            path.write_text("".join(json.dumps(message(seq)) + "\n" for seq in range(1, 6)))
            recovered = fetch_snapshot.archive_recent(directory, "lobby", keep=3)
            self.assertEqual(sorted(recovered), [3, 4, 5])

    def test_archive_target_advances_past_full_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "lobby-2026-08-26.ndjson").write_text("x" * 100)
            (directory / "lobby-2026-08-26-part-002.ndjson").write_text("x" * 100)
            target = fetch_snapshot.archive_target(
                directory, "lobby", "2026-08-26", max_bytes=100
            )
            self.assertEqual(target.name, "lobby-2026-08-26-part-003.ndjson")


class RegistryTests(unittest.TestCase):
    def test_retries_temporary_503(self):
        with patch.object(did_registry_watch, "get",
                          side_effect=[(503, "busy"), (200, "abc\n")]) as mocked, \
             patch.object(did_registry_watch.time, "sleep"):
            self.assertEqual(did_registry_watch.get_with_retry("/kv/did", 1), (200, "abc\n"))
            self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
