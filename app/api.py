"""Unified Resident View API.

    python -m app.api [--port 8090]
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from app import config
from app.adapters.benefits_register import BenefitsRegisterClient
from app.adapters.resident_index import ResidentIndexClient
from app.assembly import build_unified_all, build_unified_resident

rest_client = ResidentIndexClient(config.REST_BASE_URL, timeout=config.REST_TIMEOUT)
benefits_client = BenefitsRegisterClient(
    config.XML_BASE_URL,
    timeout=config.XML_TIMEOUT,
    max_retries=config.XML_MAX_RETRIES,
    retry_base_delay=config.XML_RETRY_BASE_DELAY,
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, payload):
        body = json.dumps(payload, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        attempt_match = q.get('match', [''])[0] == 'basic'

        if u.path == '/health':
            return self._send(200, {
                'status': 'ok',
                'service': 'unified-resident-view',
                'resident_index': 'up' if rest_client.health() else 'down',
                'benefits_register': 'up' if benefits_client.health() else 'down',
            })

        if u.path.startswith('/unified/residents/'):
            resident_id = unquote(u.path[len('/unified/residents/'):])
            if not resident_id:
                return self._send(404, {'error': 'no_such_endpoint', 'path': u.path})
            result = build_unified_resident(resident_id, rest_client, benefits_client, attempt_match)
            return self._send(200, result)

        if u.path == '/unified/residents':
            result = build_unified_all(rest_client, benefits_client, attempt_match)
            return self._send(200, result)

        return self._send(404, {'error': 'no_such_endpoint', 'path': u.path})

    def log_message(self, fmt, *a):
        print(f'  [api] {fmt % a}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=config.API_PORT)
    args = ap.parse_args()
    print(f'Unified Resident View on http://127.0.0.1:{args.port}')
    print(f'  resident index    -> {config.REST_BASE_URL}')
    print(f'  benefits register -> {config.XML_BASE_URL}')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
