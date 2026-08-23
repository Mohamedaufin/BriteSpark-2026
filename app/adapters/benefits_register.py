"""Client for the Benefits Register (legacy XML, slow and unreliable).

Roughly 1 in 7 calls returns a 500 as a matter of course. A single 500 is not
"the source is down" - it is Tuesday. We retry with backoff before surfacing
SourceUnavailable, so a genuinely dead source still looks different from an
ordinary flaky call to everything upstream of this adapter.

Two stretch-goal behaviours live here too, both scoped to this adapter only
(the REST index doesn't fail in this problem's fixtures, so there's nothing
to protect it against - see DECISIONS.md):

- A short-TTL cache on list_all(), the one call path actually on the hot
  route (?match=basic hits it on every request). Only successful fetches
  are cached; a failure is never masked behind stale data - see DECISIONS.md
  for why that's a deliberate line, not an oversight.
- A circuit breaker around the shared retry path, so a source that is
  *comprehensively* down (not just its usual ~15%) stops being hammered on
  every request and fails fast instead of paying the full retry budget
  every time.
"""
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from app.errors import SourceUnavailable

CIRCUIT_CLOSED = 'closed'
CIRCUIT_OPEN = 'open'
CIRCUIT_HALF_OPEN = 'half_open'


class BenefitsRegisterClient:
    def __init__(
        self, base_url, timeout=5.0, max_retries=3, retry_base_delay=0.3,
        cache_ttl=20.0, breaker_failure_threshold=3, breaker_cooldown=15.0,
    ):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        self.cache_ttl = cache_ttl
        self._list_all_cache = None  # (cached_at, {ref: record})

        self.breaker_failure_threshold = breaker_failure_threshold
        self.breaker_cooldown = breaker_cooldown
        self._circuit_state = CIRCUIT_CLOSED
        self._consecutive_failures = 0
        self._circuit_opened_at = None

    def _get(self, path):
        """One raw attempt. Returns (status_code, text)."""
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.getcode(), resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'benefits register unreachable: {e}') from e

    def _breaker_allows_call(self):
        if self._circuit_state == CIRCUIT_OPEN:
            if time.monotonic() - self._circuit_opened_at >= self.breaker_cooldown:
                self._circuit_state = CIRCUIT_HALF_OPEN
                return True
            return False
        return True

    def _breaker_record_success(self):
        self._circuit_state = CIRCUIT_CLOSED
        self._consecutive_failures = 0
        self._circuit_opened_at = None

    def _breaker_record_failure(self):
        self._consecutive_failures += 1
        if self._circuit_state == CIRCUIT_HALF_OPEN or self._consecutive_failures >= self.breaker_failure_threshold:
            self._circuit_state = CIRCUIT_OPEN
            self._circuit_opened_at = time.monotonic()

    def _get_with_retry(self, path):
        """Every caller of the register (list_all, get_by_ref) goes through
        here, which is exactly why the circuit breaker lives at this single
        choke point rather than being duplicated per method."""
        if not self._breaker_allows_call():
            raise SourceUnavailable(
                f'benefits register circuit breaker is open (comprehensively down; '
                f'retrying in {self.breaker_cooldown:.0f}s)'
            )
        try:
            result = self._get_with_retry_uncircuited(path)
        except SourceUnavailable:
            self._breaker_record_failure()
            raise
        self._breaker_record_success()
        return result

    def _get_with_retry_uncircuited(self, path):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                code, text = self._get(path)
            except SourceUnavailable as e:
                last_error = e
            else:
                if code == 200:
                    return text
                if code == 404:
                    return None
                last_error = SourceUnavailable(f'benefits register returned {code}: {text}')
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.1))
        raise last_error or SourceUnavailable('benefits register failed with no detail')

    def health(self):
        """Deliberately bypasses the breaker and the retry loop - the mock
        service's own README says /health is exempt from both slowness and
        failure specifically so it stays a reliable up/down signal even
        while /records is being circuit-broken."""
        try:
            code, _ = self._get('/health')
            return code == 200
        except SourceUnavailable:
            return False

    @staticmethod
    def _parse_records(xml_text):
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise SourceUnavailable(f'benefits register returned unparseable XML: {e}') from e
        records = []
        for rec in root.findall('Record'):
            records.append({
                'ref': rec.findtext('Ref', ''),
                'name': rec.findtext('Name', ''),
                'born': rec.findtext('Born', ''),
                'addr': rec.findtext('Addr', ''),
                'town': rec.findtext('Town', ''),
                'benefit_code': rec.findtext('BenefitCode', ''),
                'review_due': rec.findtext('ReviewDue', ''),
            })
        return records

    def get_by_ref(self, ref):
        safe_ref = urllib.parse.quote(ref, safe='')
        text = self._get_with_retry(f'/records/{safe_ref}')
        if text is None:
            return None
        records = self._parse_records(text)
        return records[0] if records else None

    def list_all(self):
        """Returns {ref: record}. One retried call for the full dump - the
        register has no pagination, so there is no boundary case to dedupe.

        Cached for cache_ttl seconds: only a *successful* fetch is cached,
        and a cache miss/expiry that then fails is never papered over with
        the old data - see DECISIONS.md for why staleness is bounded to the
        TTL and never silently extended past it.
        """
        if self._list_all_cache is not None:
            cached_at, data = self._list_all_cache
            if time.monotonic() - cached_at < self.cache_ttl:
                return data

        text = self._get_with_retry('/records')
        data = {} if text is None else {r['ref']: r for r in self._parse_records(text)}
        self._list_all_cache = (time.monotonic(), data)
        return data
