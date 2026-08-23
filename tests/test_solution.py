"""Integration tests: spins up real copies of both mock services (on
non-default ports, so this can run next to a dev instance) and drives the
adapters and assembly layer against them directly. No mocking - the whole
point of this problem is behaviour under a source that genuinely fails.

    python -m unittest tests.test_solution -v
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.adapters.benefits_register import BenefitsRegisterClient  # noqa: E402
from app.adapters.resident_index import ResidentIndexClient  # noqa: E402
from app.assembly import build_benefits_index, build_unified_resident  # noqa: E402
from app.errors import SourceUnavailable  # noqa: E402

REST_PORT = 18081
XML_PORT_NORMAL = 18082
XML_PORT_DEAD = 18083  # failure_rate=1.0, always fails

PYTHON = sys.executable


def _wait_healthy(url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.getcode() == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f'{url} never became healthy')


def _spawn(script, port, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.Popen(
        [PYTHON, script, '--port', str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_procs = []


def setUpModule():
    services_dir = os.path.join(ROOT, 'data pack', 'services')
    _procs.append(_spawn(os.path.join(services_dir, 'rest_service.py'), REST_PORT))
    _procs.append(_spawn(
        os.path.join(services_dir, 'xml_service.py'), XML_PORT_NORMAL,
        {'BENEFITS_FAILURE_RATE': '0.15'},
    ))
    _procs.append(_spawn(
        os.path.join(services_dir, 'xml_service.py'), XML_PORT_DEAD,
        {'BENEFITS_FAILURE_RATE': '1.0'},
    ))
    _wait_healthy(f'http://127.0.0.1:{REST_PORT}/health')
    _wait_healthy(f'http://127.0.0.1:{XML_PORT_NORMAL}/health')
    _wait_healthy(f'http://127.0.0.1:{XML_PORT_DEAD}/health')


def tearDownModule():
    for p in _procs:
        p.terminate()
    for p in _procs:
        p.wait(timeout=5)


class ResidentIndexDedupTests(unittest.TestCase):
    def test_list_all_has_no_duplicate_ids(self):
        client = ResidentIndexClient(f'http://127.0.0.1:{REST_PORT}')
        residents = client.list_all()
        with open(os.path.join(ROOT, 'data pack', 'services', '_rest_data.json'), encoding='utf-8') as f:
            expected_total = len(json.load(f))
        self.assertEqual(len(residents), expected_total)

    def test_list_all_is_idempotent_across_calls(self):
        client = ResidentIndexClient(f'http://127.0.0.1:{REST_PORT}')
        first = client.list_all()
        second = client.list_all()
        self.assertEqual(set(first.keys()), set(second.keys()))
        self.assertEqual(first, second)


class BenefitsRegisterResilienceTests(unittest.TestCase):
    def test_dead_source_raises_source_unavailable_not_a_crash(self):
        client = BenefitsRegisterClient(
            f'http://127.0.0.1:{XML_PORT_DEAD}', max_retries=2, retry_base_delay=0.05,
        )
        with self.assertRaises(SourceUnavailable):
            client.list_all()

    def test_flaky_source_eventually_succeeds_with_retries(self):
        client = BenefitsRegisterClient(
            f'http://127.0.0.1:{XML_PORT_NORMAL}', max_retries=5, retry_base_delay=0.05,
        )
        # 0.15 failure rate ^ 5 retries is a ~0.008% chance of a false failure here.
        records = client.list_all()
        self.assertGreater(len(records), 0)


class UnifiedAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.rest = ResidentIndexClient(f'http://127.0.0.1:{REST_PORT}')
        self.healthy_benefits = BenefitsRegisterClient(
            f'http://127.0.0.1:{XML_PORT_NORMAL}', max_retries=5, retry_base_delay=0.05,
        )
        self.dead_benefits = BenefitsRegisterClient(
            f'http://127.0.0.1:{XML_PORT_DEAD}', max_retries=2, retry_base_delay=0.05,
        )
        residents = self.rest.list_all()
        self.some_id = next(iter(residents))

    def test_default_does_not_attempt_matching(self):
        result = build_unified_resident(self.some_id, self.rest, self.healthy_benefits)
        self.assertEqual(result['benefits_source']['status'], 'not_attempted')
        self.assertIsNone(result['benefits'])

    def test_finds_a_true_cross_source_match(self):
        # R-10697 / Daniel Delgado is a confirmed real pair in the data pack
        # (verified against the hidden _pid ground truth - see
        # scripts/match_accuracy_check.py).
        result = build_unified_resident('R-10697', self.rest, self.healthy_benefits, attempt_match=True)
        self.assertEqual(result['benefits_source']['status'], 'matched')
        self.assertIsNotNone(result['benefits'])
        self.assertEqual(result['benefits']['ref'], 'NO/2019/4697')

    def test_no_match_when_no_benefits_record_shares_the_key(self):
        # R-10394 / Paul Quill has no counterpart in the benefits register.
        result = build_unified_resident('R-10394', self.rest, self.healthy_benefits, attempt_match=True)
        self.assertEqual(result['benefits_source']['status'], 'no_match')
        self.assertIsNone(result['benefits'])

    def test_degrades_gracefully_when_benefits_source_is_dead(self):
        result = build_unified_resident(self.some_id, self.rest, self.dead_benefits, attempt_match=True)
        self.assertEqual(result['resident_source']['status'], 'ok')
        self.assertIsNotNone(result['resident'])
        self.assertEqual(result['benefits_source']['status'], 'unavailable')
        self.assertIsNotNone(result['benefits_source']['error'])
        self.assertIsNone(result['benefits'])

    def test_unknown_resident_is_not_found_not_an_error(self):
        result = build_unified_resident('R-does-not-exist', self.rest, self.healthy_benefits)
        self.assertEqual(result['resident_source']['status'], 'not_found')

    def test_repeated_calls_are_idempotent(self):
        first = build_unified_resident(self.some_id, self.rest, self.healthy_benefits, attempt_match=True)
        second = build_unified_resident(self.some_id, self.rest, self.healthy_benefits, attempt_match=True)
        self.assertEqual(first, second)

    def test_matching_never_returns_a_false_positive_on_a_key_collision(self):
        # Build a synthetic collision: two different benefits records that
        # share a normalized name+dob key must never be silently merged.
        xml_records = {
            'A': {'ref': 'A', 'name': 'SMITH, John', 'born': '1980-01-01'},
            'B': {'ref': 'B', 'name': 'SMITH, John', 'born': '1980-01-01'},
        }
        index = build_benefits_index(xml_records)
        resident = {'first_name': 'John', 'last_name': 'Smith', 'date_of_birth': '1980-01-01'}
        from app.assembly import find_benefits_match
        status, record, reason = find_benefits_match(resident, index)
        self.assertEqual(status, 'ambiguous')
        self.assertIsNone(record)


def _stub_server(response_map):
    """A throwaway HTTP server returning canned (status, content_type, body)
    per path, ignoring query strings. Used to simulate malformed upstream
    responses the real mock services never actually send."""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, ctype, body = response_map.get(
                urlparse(self.path).path, (404, 'text/plain', 'not found'),
            )
            b = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, fmt, *a):
            pass

    server = HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _counting_stub_server(response_map):
    """Like _stub_server, but tracks how many requests actually reached
    each path - the only way to prove caching and the circuit breaker are
    doing anything rather than just not raising."""
    hits = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            hits[path] = hits.get(path, 0) + 1
            status, ctype, body = response_map.get(path, (404, 'text/plain', 'not found'))
            b = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, fmt, *a):
            pass

    server = HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


EMPTY_REGISTER_XML = '<?xml version="1.0"?><BenefitsRegister></BenefitsRegister>'


class CachingAndCircuitBreakerTests(unittest.TestCase):
    """Both stretch goals from the problem doc: 'a slow source is not slow
    on every call' (caching) and 'a source that is comprehensively down
    stops being called on every request' (circuit breaking)."""

    def test_list_all_is_cached_within_ttl(self):
        server, hits = _counting_stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/records': (200, 'application/xml', EMPTY_REGISTER_XML),
        })
        try:
            port = server.server_address[1]
            client = BenefitsRegisterClient(f'http://127.0.0.1:{port}', cache_ttl=60)
            client.list_all()
            client.list_all()
            client.list_all()
            self.assertEqual(hits.get('/records', 0), 1, 'three calls within the TTL should hit upstream once')
        finally:
            server.shutdown()
            server.server_close()

    def test_cache_expires_after_ttl(self):
        server, hits = _counting_stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/records': (200, 'application/xml', EMPTY_REGISTER_XML),
        })
        try:
            port = server.server_address[1]
            client = BenefitsRegisterClient(f'http://127.0.0.1:{port}', cache_ttl=0.2)
            client.list_all()
            time.sleep(0.3)
            client.list_all()
            self.assertEqual(hits.get('/records', 0), 2, 'a call after the TTL expires should hit upstream again')
        finally:
            server.shutdown()
            server.server_close()

    def test_circuit_breaker_opens_after_threshold_and_fails_fast(self):
        server, hits = _counting_stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/records': (500, 'application/xml', '<Fault/>'),
        })
        try:
            port = server.server_address[1]
            client = BenefitsRegisterClient(
                f'http://127.0.0.1:{port}', max_retries=1, retry_base_delay=0.01,
                breaker_failure_threshold=2, breaker_cooldown=60,
            )
            with self.assertRaises(SourceUnavailable):
                client.list_all()
            with self.assertRaises(SourceUnavailable):
                client.list_all()
            hits_once_open = hits.get('/records', 0)

            with self.assertRaises(SourceUnavailable) as ctx:
                client.list_all()
            self.assertIn('circuit breaker', str(ctx.exception))
            self.assertEqual(hits.get('/records', 0), hits_once_open, 'an open breaker must not touch the network')
        finally:
            server.shutdown()
            server.server_close()

    def test_circuit_breaker_recovers_after_cooldown(self):
        response_map = {
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/records': (500, 'application/xml', '<Fault/>'),
        }
        server, hits = _counting_stub_server(response_map)
        try:
            port = server.server_address[1]
            client = BenefitsRegisterClient(
                f'http://127.0.0.1:{port}', max_retries=1, retry_base_delay=0.01,
                breaker_failure_threshold=1, breaker_cooldown=0.2,
            )
            with self.assertRaises(SourceUnavailable):
                client.list_all()  # trips the breaker

            with self.assertRaises(SourceUnavailable) as ctx:
                client.list_all()  # breaker open, should fail fast
            self.assertIn('circuit breaker', str(ctx.exception))
            hits_while_open = hits.get('/records', 0)

            response_map['/records'] = (200, 'application/xml', EMPTY_REGISTER_XML)
            time.sleep(0.25)  # let the cooldown elapse

            data = client.list_all()  # half-open trial call, source now "recovered"
            self.assertEqual(data, {})
            self.assertGreater(hits.get('/records', 0), hits_while_open, 'the recovery trial must actually call upstream')
            self.assertEqual(client._circuit_state, 'closed')
        finally:
            server.shutdown()
            server.server_close()


class MalformedUpstreamTests(unittest.TestCase):
    """The real mock services never send garbage - these simulate the case
    anyway, since a well-behaved adapter shouldn't only survive the failure
    modes it was told to expect."""

    def test_malformed_xml_is_a_source_failure_not_a_crash(self):
        server = _stub_server({
            '/health': (200, 'application/xml', '<Health><Status>ok</Status></Health>'),
            '/records': (200, 'application/xml', '<BenefitsRegister><Record><Ref>NO/1</Ref>'),  # truncated
        })
        try:
            port = server.server_address[1]
            client = BenefitsRegisterClient(f'http://127.0.0.1:{port}', max_retries=1)
            with self.assertRaises(SourceUnavailable):
                client.list_all()
        finally:
            server.shutdown()
            server.server_close()

    def test_rest_record_missing_id_is_a_source_failure_not_a_crash(self):
        server = _stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/residents': (200, 'application/json', '{"has_more": false, "results": [{"first_name": "No Id"}]}'),
        })
        try:
            port = server.server_address[1]
            client = ResidentIndexClient(f'http://127.0.0.1:{port}')
            with self.assertRaises(SourceUnavailable):
                client.list_all()
        finally:
            server.shutdown()
            server.server_close()

    def test_pagination_walk_is_bounded_against_a_source_that_never_stops(self):
        # A source that pathologically never sets has_more=false would
        # otherwise page forever - each individual call has a timeout, but
        # the walk itself didn't, until MAX_PAGES capped it.
        server = _stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/residents': (200, 'application/json', '{"has_more": true, "results": [{"id": "R-1"}]}'),
        })
        try:
            port = server.server_address[1]
            client = ResidentIndexClient(f'http://127.0.0.1:{port}')
            import app.adapters.resident_index as resident_index_module
            with patch.object(resident_index_module, 'MAX_PAGES', 3):
                with self.assertRaises(SourceUnavailable):
                    client.list_all()
        finally:
            server.shutdown()
            server.server_close()

    def test_non_json_rest_response_is_a_source_failure_not_a_crash(self):
        server = _stub_server({
            '/health': (200, 'application/json', '{"status": "ok"}'),
            '/residents/R-1': (200, 'text/plain', 'this is not json'),
        })
        try:
            port = server.server_address[1]
            client = ResidentIndexClient(f'http://127.0.0.1:{port}')
            with self.assertRaises(SourceUnavailable):
                client.get_by_id('R-1')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
