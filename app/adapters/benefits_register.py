import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from app.errors import SourceUnavailable


class BenefitsRegisterClient:
    def __init__(self, base_url, timeout=5.0, max_retries=3, retry_base_delay=0.3):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    def _get(self, path):
        """One raw attempt. Returns (status_code, text)."""
        url = f'{self.base_url}{path}'
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.getcode(), resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f'benefits register unreachable: {e}') from e

    def health(self):
        try:
            code, _ = self._get('/health')
            return code == 200
        except SourceUnavailable:
            return False
