import json
import urllib.error
import urllib.request

from app.errors import SourceUnavailable


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
