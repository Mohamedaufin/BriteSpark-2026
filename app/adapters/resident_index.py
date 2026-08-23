"""Client for the Resident Index (REST, paginated).

The index is mostly reliable but its sort key can shift while a client is
paging, which serves some records on two consecutive pages. list_all()
collapses that by keying on id rather than trusting page boundaries.

list_all() is cached on the same terms as the benefits register's: only
successful walks are cached, and a failure is never served from stale data.
The index isn't slow the way the register is, so this isn't a latency fix -
it's here because a full page walk is now on the single-resident path (both
the register-ref door and the reverse-ambiguity check need the whole
population), and paying 25 page requests per lookup would be careless.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.errors import SourceUnavailable

PAGE_SIZE = 25
MAX_PAGES = 10_000  # generous cap against a source that never sets has_more=false


class ResidentIndexClient:
    def __init__(self, base_url, timeout=5.0, cache_ttl=20.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._list_all_cache = None  # (cached_at, {id: record})

    def _get_json(self, path):
        """Fetch and parse. A response we can't even parse as JSON is treated
        the same as an unreachable source - we have no basis to trust its
        status code either, so we don't try to special-case it."""
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                code, raw = resp.getcode(), resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read().decode('utf-8')
            code = e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'resident index unreachable: {e}') from e

        try:
            return code, json.loads(raw)
        except json.JSONDecodeError as e:
            raise SourceUnavailable(f'resident index returned unparseable JSON (status {code}): {e}') from e

    def health(self):
        try:
            code, _ = self._get_json('/health')
            return code == 200
        except SourceUnavailable:
            return False

    def get_by_id(self, resident_id):
        safe_id = urllib.parse.quote(resident_id, safe='')
        code, body = self._get_json(f'/residents/{safe_id}')
        if code == 404:
            return None
        if code != 200:
            raise SourceUnavailable(f'resident index returned {code}: {body}')
        if not isinstance(body, dict) or 'id' not in body:
            raise SourceUnavailable(f'resident index returned a malformed resident record: {body!r}')
        return body

    def list_all(self):
        """Walk every page, deduping by id. Returns {id: record}.

        Any page whose shape doesn't match what we expect (missing
        'results', a record with no 'id', a non-dict entry) is treated as a
        source failure rather than a KeyError - a shape violation is exactly
        as much "the source misbehaving" as a 500 is. A source that never
        sets has_more=false gets the same treatment once MAX_PAGES is hit -
        an unbounded walk is a hang by another name, just one no per-call
        timeout catches on its own.
        """
        if self._list_all_cache is not None:
            cached_at, data = self._list_all_cache
            if time.monotonic() - cached_at < self.cache_ttl:
                return data

        by_id = {}
        page = 1
        while True:
            if page > MAX_PAGES:
                raise SourceUnavailable(f'resident index never signalled has_more=false after {MAX_PAGES} pages')
            code, body = self._get_json(f'/residents?page={page}&page_size={PAGE_SIZE}')
            if code != 200:
                raise SourceUnavailable(f'resident index returned {code}: {body}')
            if not isinstance(body, dict) or not isinstance(body.get('results'), list):
                raise SourceUnavailable(f'resident index returned an unexpected shape on page {page}: {body!r}')
            for r in body['results']:
                if not isinstance(r, dict) or 'id' not in r:
                    raise SourceUnavailable(f'resident index returned a malformed record on page {page}: {r!r}')
                by_id[r['id']] = r
            if not body.get('has_more'):
                break
            page += 1

        self._list_all_cache = (time.monotonic(), by_id)
        return by_id
