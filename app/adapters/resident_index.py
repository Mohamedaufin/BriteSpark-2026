"""Client for the Resident Index (the REST source).

This file has one job: get residents out of the REST service, and hide
everything odd about that service from the rest of the app.

The odd thing is that the index sometimes serves the same record on two
consecutive pages, so walking the pages naively gives you duplicates.
list_all() fixes that by storing records in a dictionary keyed on the
resident id - the same record arriving twice just overwrites itself.

Nothing outside this file knows this source is paginated, or that it has
that quirk. That is the point: if the REST source changes on day two, this
file changes and nothing else does.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.errors import SourceUnavailable

PAGE_SIZE = 25
MAX_PAGES = 10_000  # a source that never says has_more=false must not loop forever


class ResidentIndexClient:
    def __init__(self, base_url, timeout=5.0, cache_ttl=20.0, max_snapshot_age=120.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.max_snapshot_age = max_snapshot_age
        # The cache is just the last successful list_all() result plus the
        # time we fetched it. Nothing cleverer than that.
        self._cache = None
        self._cached_at = 0.0

    def _get(self, path):
        """One HTTP GET. Returns (status_code, parsed_json).

        A response we cannot parse as JSON is treated exactly like an
        unreachable source: if we can't trust the body, we have no reason to
        trust the status code either.
        """
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                code, raw = resp.getcode(), resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            # An error response (404, 500...) still has a body worth reading.
            with e:
                code, raw = e.code, e.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'resident index unreachable: {e}') from e

        try:
            return code, json.loads(raw)
        except json.JSONDecodeError as e:
            raise SourceUnavailable(
                f'resident index returned something that is not JSON (status {code}): {e}'
            ) from e

    def health(self):
        """Is this source up? Used by GET /health, not by the data calls."""
        try:
            code, _ = self._get('/health')
            return code == 200
        except SourceUnavailable:
            return False

    def get_by_id(self, resident_id):
        """One resident, or None if this source has never heard of them."""
        safe_id = urllib.parse.quote(resident_id, safe='')
        code, body = self._get(f'/residents/{safe_id}')
        if code == 404:
            return None
        if code != 200:
            raise SourceUnavailable(f'resident index returned {code}: {body}')
        if not isinstance(body, dict) or 'id' not in body:
            raise SourceUnavailable(f'resident index returned a malformed resident record: {body!r}')
        return body

    def list_all(self):
        """Every resident, as {id: record}.

        Cached for cache_ttl seconds. Only successful walks are cached, so a
        failure is never hidden behind old data.
        """
        if self._cache is not None and time.monotonic() - self._cached_at < self.cache_ttl:
            return self._cache

        by_id = {}
        page = 1
        while True:
            if page > MAX_PAGES:
                raise SourceUnavailable(
                    f'resident index never said has_more=false after {MAX_PAGES} pages'
                )

            code, body = self._get(f'/residents?page={page}&page_size={PAGE_SIZE}')
            if code != 200:
                raise SourceUnavailable(f'resident index returned {code}: {body}')

            # A response in an unexpected shape is the source misbehaving,
            # exactly like a 500 is - so it gets the same treatment, rather
            # than a KeyError crashing the API.
            if not isinstance(body, dict) or not isinstance(body.get('results'), list):
                raise SourceUnavailable(f'resident index returned an unexpected shape on page {page}: {body!r}')

            for record in body['results']:
                if not isinstance(record, dict) or 'id' not in record:
                    raise SourceUnavailable(f'resident index returned a malformed record on page {page}: {record!r}')
                # Keying on id is what removes the duplicates. A record that
                # arrives on two pages simply overwrites itself here.
                by_id[record['id']] = record

            if not body.get('has_more'):
                break
            page += 1

        self._cache = by_id
        self._cached_at = time.monotonic()
        return by_id

    def list_all_or_last_known(self):
        """Fresh data if we can get it; the last data we got if we can't.

        Returns (records, seconds_old). seconds_old is None when the answer
        is fresh, and the age of the saved copy when it isn't - so the
        caller can always tell the two apart and say so in the response.

        "Partial data beats an error page": if the source is down but we
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
