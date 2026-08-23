"""Client for the Benefits Register (legacy XML, slow and unreliable).

Roughly 1 in 7 calls returns a 500 as a matter of course. A single 500 is not
"the source is down" - it is Tuesday. We retry with backoff before surfacing
SourceUnavailable, so a genuinely dead source still looks different from an
ordinary flaky call.
"""
import random
import time
import urllib.error
import urllib.parse
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
            with e:
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
        """Returns {ref: record}."""
        text = self._get_with_retry('/records')
        if text is None:
            return {}
        return {r['ref']: r for r in self._parse_records(text)}
