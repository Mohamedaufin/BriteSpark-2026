"""Client for the Benefits Register (the legacy XML source).

This source is slow (about 1.5 seconds a call) and fails often - 40% of
calls as of day two (raised permanently from 15%; see DECISIONS.md). Both
are normal for it, not faults to wait out, so this file deals with them
itself and nothing outside it has to care. Nothing below names the actual
number: it lives in the running service's configuration, not here, so a
future change to it needs no code change.

Three things happen here, in increasing order of "how bad is it":

1. Retry. One 500 is not an outage, it is this source's normal Tuesday.
   We try up to max_retries times with a growing pause between attempts.
2. Circuit breaker. If the source fails max_retries times in a row on
   several requests running, it is not flaky - it is down. We stop calling
   it for breaker_cooldown seconds instead of making every request wait
   through the full retry budget to learn the same thing.
3. Give up. Raise SourceUnavailable, which assembly.py turns into an
   honest "benefits_source: unavailable" in the response.

There is also a small cache, because this source is slow and the same full
dump is what every matched lookup needs.
"""
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from app.errors import SourceUnavailable


class BenefitsRegisterClient:
    def __init__(
        self, base_url, timeout=5.0, max_retries=3, retry_base_delay=0.3,
        cache_ttl=20.0, max_snapshot_age=120.0,
        breaker_failure_threshold=3, breaker_cooldown=15.0,
    ):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        self.cache_ttl = cache_ttl
        self.max_snapshot_age = max_snapshot_age
        self._cache = None
        self._cached_at = 0.0

        # The whole circuit breaker is these two numbers.
        self.breaker_failure_threshold = breaker_failure_threshold
        self.breaker_cooldown = breaker_cooldown
        self._consecutive_failures = 0   # failed requests in a row
        self._blocked_until = 0.0        # don't call the source before this time

    def _get_once(self, path):
        """One HTTP GET, no retrying. Returns (status_code, text)."""
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.getcode(), resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'benefits register unreachable: {e}') from e

    def _get(self, path):
        """A GET that retries, and that gives up early on a dead source.

        Returns the response text, or None if the source said 404.

        Every data call goes through here, which is why the breaker lives
        here and not in each method separately.
        """
        # Circuit breaker, part 1: are we currently refusing to call it?
        if time.monotonic() < self._blocked_until:
            wait = self._blocked_until - time.monotonic()
            raise SourceUnavailable(
                f'benefits register looks comprehensively down, so the circuit breaker is '
                f'not calling it for another {wait:.0f}s'
            )

        # Circuit breaker, part 2: if we had given up on this source and the
        # cooldown has now passed, this call is the one that finds out
        # whether it came back. Ask /health first - it is exempt from this
        # source's slowness and failures, so it answers that question in one
        # fast request instead of a full retry-and-backoff cycle.
        if self._consecutive_failures >= self.breaker_failure_threshold:
            if not self.health():
                self._blocked_until = time.monotonic() + self.breaker_cooldown
                raise SourceUnavailable(
                    f'benefits register /health is still not answering, so the circuit '
                    f'breaker stays open for another {self.breaker_cooldown:.0f}s'
                )

        last_error = None
        for attempt in range(self.max_retries):
            try:
                code, text = self._get_once(path)
            except SourceUnavailable as e:
                last_error = e          # couldn't reach it at all; worth retrying
            else:
                if code == 200:
                    # Circuit breaker, part 2: any success clears the count.
                    self._consecutive_failures = 0
                    self._blocked_until = 0.0
                    return text
                if code == 404:
                    self._consecutive_failures = 0
                    self._blocked_until = 0.0
                    return None
                last_error = SourceUnavailable(f'benefits register returned {code}: {text}')

            if attempt < self.max_retries - 1:
                # Wait longer after each failure, plus a little randomness so
                # repeated calls don't all retry in lockstep.
                time.sleep(self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.1))

        # Circuit breaker, part 3: that request failed every attempt. Count
        # it, and once enough have failed in a row, stop calling for a while.
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker_failure_threshold:
            self._blocked_until = time.monotonic() + self.breaker_cooldown

        raise last_error or SourceUnavailable('benefits register failed with no detail')

    def health(self):
        """Is this source up?

        Deliberately skips the retrying and the breaker: the mock service's
        own README says /health is exempt from the slowness and the failures,
        so it stays a truthful up/down signal even while /records is failing.
        """
        try:
            code, _ = self._get_once('/health')
            return code == 200
        except SourceUnavailable:
            return False

    @staticmethod
    def _parse_records(xml_text):
        """XML text -> a list of plain dictionaries.

        Unparseable XML is treated as the source failing. It is deliberately
        not retried: a broken response is a fact about that response, so
        asking again would just fetch the same broken thing.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise SourceUnavailable(f'benefits register returned XML we cannot parse: {e}') from e

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
        """One benefits record, or None if this source has no such ref."""
        safe_ref = urllib.parse.quote(ref, safe='')
        text = self._get(f'/records/{safe_ref}')
        if text is None:
            return None
        records = self._parse_records(text)
        return records[0] if records else None

    def list_all(self):
        """Every benefits record, as {ref: record}.

        Cached for cache_ttl seconds, because this source is slow and this
        is the call every matched lookup needs. Only successful fetches are
        cached, so a failure is never hidden behind old data.

        No pagination here - the register hands over everything at once, so
        there is no page-boundary problem like the resident index has.
        """
        if self._cache is not None and time.monotonic() - self._cached_at < self.cache_ttl:
            return self._cache

        text = self._get('/records')
        records = {} if text is None else {r['ref']: r for r in self._parse_records(text)}

        self._cache = records
        self._cached_at = time.monotonic()
        return records

    def list_all_or_last_known(self):
        """Fresh data if we can get it; the last data we got if we can't.

        Returns (records, seconds_old). seconds_old is None when the answer
        is fresh, and the age of the saved copy when it isn't - so the
        caller can always tell the two apart and say so in the response.

        "Partial data beats an error page": if the register is down but we
        fetched it successfully a minute ago, that minute-old data is far
        more use to a staff member than an error - as long as we say how
        old it is. Past max_snapshot_age we stop vouching for it and fail
        like normal, because at some point old data stops being useful and
        starts being misleading.
        """
        try:
            return self.list_all(), None
        except SourceUnavailable:
            if self._cache is not None:
                age = time.monotonic() - self._cached_at
                if age < self.max_snapshot_age:
                    return self._cache, age
            raise
