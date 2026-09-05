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


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.watch = argparse.Namespace(
            out=str(self.root / 'registry.ndjson'), namespace='did-2d',
            refresh=['did-2d/first', 'contrib/second'], refresh_hours=24,
            value='public test note', timeout=1, key=None, max_age_hours=48)
        self.collect = argparse.Namespace(
            out=str(self.root / 'lobby.json'), room='lobby', archive='',
            pages=2, keep=3000, from_seq=None, timeout=1, gap_log='',
            base_url='https://example.invalid')

    def test_listing_failure_does_not_skip_refresh(self):
        with patch.object(did_registry_watch, 'get_with_retry', return_value=(503, 'busy')), \
             patch.object(did_registry_watch, 'post', return_value=(200, 'ok')) as post:
            with self.assertRaises(RuntimeError):
                did_registry_watch.round_once(self.watch)
            self.assertEqual(post.call_count, 2)

    def test_failed_refresh_preserves_success_and_attempts_other_target(self):
        with patch.object(did_registry_watch, 'post', side_effect=[(503, 'busy'), (200, 'ok')]) as post:
            with self.assertRaises(RuntimeError):
                did_registry_watch.keepalive(self.watch)
            self.assertEqual(post.call_count, 2)
        state = json.loads(Path(self.watch.out).with_suffix('.refresh.json').read_text())
        self.assertNotIn('did-2d/first', state)
        self.assertIn('contrib/second', state)

    def test_successful_refresh_not_repeated_before_due(self):
        with patch.object(did_registry_watch, 'post', return_value=(200, 'ok')) as post:
            did_registry_watch.keepalive(self.watch)
            did_registry_watch.keepalive(self.watch)
            self.assertEqual(post.call_count, 2)

    def test_health_check_detects_stale_target_without_network(self):
        Path(self.watch.out).with_suffix('.refresh.json').write_text(json.dumps({
            'did-2d/first': 1000000, 'contrib/second': 1000000 - 49 * 3600}))
        with patch.object(did_registry_watch.time, 'time', return_value=1000000), \
             patch.object(did_registry_watch, 'post') as post, \
             patch.object(did_registry_watch, 'get') as get:
            with self.assertRaises(RuntimeError):
                did_registry_watch.check_health(self.watch)
            post.assert_not_called()
            get.assert_not_called()

    def test_failed_fetch_leaves_snapshot_byte_for_byte_unchanged(self):
        path = Path(self.collect.out)
        original = json.dumps({'generated_at': 'old', 'messages': [message(1)]})
        path.write_text(original)
        with patch.object(fetch_snapshot, 'fetch_page', return_value=None):
            with self.assertRaises(RuntimeError):
                fetch_snapshot.collect(self.collect)
        self.assertEqual(path.read_text(), original)

    def test_failed_first_fetch_does_not_create_snapshot(self):
        with patch.object(fetch_snapshot, 'fetch_page', return_value=None):
            with self.assertRaises(RuntimeError):
                fetch_snapshot.collect(self.collect)
        self.assertFalse(Path(self.collect.out).exists())

    def test_malformed_response_does_not_refresh_old_snapshot(self):
        path = Path(self.collect.out)
        original = json.dumps({'generated_at': 'old', 'messages': [message(1)]})
        path.write_text(original)
        for invalid in ({}, [], {'messages': None}, {'messages': [None]},
                        {'messages': [{'seq': True}]}, {'error': 'busy'}):
            with self.subTest(payload=invalid), \
                 patch.object(fetch_snapshot, 'fetch_page', return_value=invalid):
                with self.assertRaises(RuntimeError):
                    fetch_snapshot.collect(self.collect)
                self.assertEqual(path.read_text(), original)

    def test_empty_success_is_not_an_outage(self):
        with patch.object(fetch_snapshot, 'fetch_page', return_value={'messages': []}):
            fetch_snapshot.collect(self.collect)
        data = json.loads(Path(self.collect.out).read_text())
        self.assertEqual(data['fetch_status'], 'ok')
        self.assertIsNone(data['latest_message_at'])

    def test_partial_fetch_keeps_received_data_but_reports_failure(self):
        page = {'messages': [message(i) for i in range(1, 201)],
                'first_seq': 1, 'last_seq': 200}
        with patch.object(fetch_snapshot, 'fetch_page', side_effect=[page, None]):
            with self.assertRaises(RuntimeError):
                fetch_snapshot.collect(self.collect)
        data = json.loads(Path(self.collect.out).read_text())
        self.assertEqual(data['fetch_status'], 'partial')
        self.assertEqual(data['count'], 200)
        self.assertEqual(data['latest_message_at'], message(200)['ts'])


if __name__ == "__main__":
    unittest.main()
