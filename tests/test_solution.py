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
import time
import unittest
import urllib.request

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


if __name__ == '__main__':
    unittest.main()
