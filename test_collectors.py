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
        self.assertEqual(data['collection_scope'], 'bounded-retained-sample')
        self.assertEqual(data['source_endpoint'], '/r/lobby')

    def test_partial_fetch_keeps_received_data_but_reports_failure(self):
        page = {'messages': [message(i) for i in range(1, 201)],
                'first_seq': 1, 'last_seq': 200}
        with patch.object(fetch_snapshot, 'fetch_page', side_effect=[page, None]):
            with self.assertRaises(RuntimeError):
                fetch_snapshot.collect(self.collect)
        self.assertFalse(Path(self.collect.out).exists())
        state = json.loads(fetch_snapshot.status_path(Path(self.collect.out)).read_text())
        self.assertEqual(state['last_attempt_outcome'], 'failed')
        self.assertIsNone(state['last_successful_collection_at'])

    def test_success_failure_recovery_preserves_historical_snapshot(self):
        page = {'messages': [message(1)]}
        path = Path(self.collect.out)
        state_path = fetch_snapshot.status_path(path)
        with patch.object(fetch_snapshot, 'fetch_page', return_value=page):
            fetch_snapshot.collect(self.collect)
        first = path.read_bytes()
        success = json.loads(state_path.read_text())
        self.assertEqual(success['last_attempt_outcome'], 'succeeded')
        self.assertEqual(success['last_successful_collection_at'],
                         json.loads(first)['collection_completed_at'])
        self.assertEqual(success['max_age_seconds'], fetch_snapshot.DEFAULT_MAX_AGE_SECONDS)

        with patch.object(fetch_snapshot, 'fetch_page', return_value=None):
            with self.assertRaises(RuntimeError):
                fetch_snapshot.collect(self.collect)
        failed = json.loads(state_path.read_text())
        self.assertEqual(failed['last_attempt_outcome'], 'failed')
        self.assertEqual(failed['last_successful_collection_at'],
                         success['last_successful_collection_at'])
        self.assertEqual(path.read_bytes(), first)

        with patch.object(fetch_snapshot, 'fetch_page', return_value=page):
            fetch_snapshot.collect(self.collect)
        recovered = json.loads(state_path.read_text())
        self.assertEqual(recovered['last_attempt_outcome'], 'succeeded')
        self.assertEqual(recovered['last_successful_collection_at'],
                         json.loads(path.read_text())['collection_completed_at'])

    def test_crash_after_attempt_record_leaves_running_not_success(self):
        path = Path(self.collect.out)
        state_path = fetch_snapshot.status_path(path)
        original = fetch_snapshot.atomic_json
        writes = []
        def capture_then_crash(target, value):
            original(target, value)
            writes.append((target, value))
            if target == state_path and value['last_attempt_outcome'] == 'running':
                raise KeyboardInterrupt()
        with patch.object(fetch_snapshot, 'atomic_json', side_effect=capture_then_crash):
            with self.assertRaises(KeyboardInterrupt):
                fetch_snapshot.collect(self.collect)
        self.assertEqual(json.loads(state_path.read_text())['last_attempt_outcome'], 'running')
        self.assertFalse(path.exists())

    def test_invalid_existing_snapshot_fails_without_erasing_it(self):
        path = Path(self.collect.out)
        path.write_text('{broken')
        with patch.object(fetch_snapshot, 'fetch_page') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'Invalid existing snapshot'):
                fetch_snapshot.collect(self.collect)
            fetch.assert_not_called()
        self.assertEqual(path.read_text(), '{broken')
        self.assertEqual(json.loads(fetch_snapshot.status_path(path).read_text())
                         ['last_attempt_outcome'], 'failed')

    def test_overlapping_collector_cannot_replace_active_attempt(self):
        path = Path(self.collect.out)
        with fetch_snapshot.exclusive_collection(path):
            with patch.object(fetch_snapshot, 'fetch_page') as fetch:
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    fetch_snapshot.collect(self.collect)
                fetch.assert_not_called()
        self.assertFalse(fetch_snapshot.status_path(path).exists())


if __name__ == "__main__":
    unittest.main()
