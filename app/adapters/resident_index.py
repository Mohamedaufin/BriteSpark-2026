import json
import urllib.error
import urllib.parse
import urllib.request

from app.errors import SourceUnavailable

PAGE_SIZE = 25


class ResidentIndexClient:
    def __init__(self, base_url, timeout=5.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def _get_json(self, path):
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.getcode(), json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8')
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, {'error': body}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'resident index unreachable: {e}') from e

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
        return body

    def list_all(self):
        """Walk every page, deduping by id. Returns {id: record}."""
        by_id = {}
        page = 1
        while True:
            code, body = self._get_json(f'/residents?page={page}&page_size={PAGE_SIZE}')
            if code != 200:
                raise SourceUnavailable(f'resident index returned {code}: {body}')
            for r in body.get('results', []):
                by_id[r['id']] = r
            if not body.get('has_more'):
                break
            page += 1
        return by_id
