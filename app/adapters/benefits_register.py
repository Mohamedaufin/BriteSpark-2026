import random
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

    def _get_with_retry(self, path):
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
