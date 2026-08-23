"""Unified Resident View API.

    python -m app.api [--port 8090]

Endpoints
    GET /                                    optional demo page (display client only, see below)
    GET /health
    GET /unified/residents/<identifier>[?match=off]
    GET /unified/residents[?match=off]

<identifier> is either a Resident Index id (R-10697) or a Benefits Register
ref (NO/2019/4697). Whichever the caller holds opens the same view.

The problem statement is explicit that interface quality isn't assessed and
a CLI/curl demonstration is fine - GET / exists purely to make manual
poking easier, is a pure client of the three endpoints above (fetch, then
render; nothing recomputed), and isn't required by anything in DECISIONS.md
or the floor.
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from app import config
from app.adapters.benefits_register import BenefitsRegisterClient
from app.adapters.resident_index import ResidentIndexClient
from app.assembly import build_unified_all, build_unified_resident

DEMO_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo.html')

rest_client = ResidentIndexClient(
    config.REST_BASE_URL,
    timeout=config.REST_TIMEOUT,
    cache_ttl=config.REST_CACHE_TTL,
    max_snapshot_age=config.REST_MAX_SNAPSHOT_AGE,
)
benefits_client = BenefitsRegisterClient(
    config.XML_BASE_URL,
    timeout=config.XML_TIMEOUT,
    max_retries=config.XML_MAX_RETRIES,
    retry_base_delay=config.XML_RETRY_BASE_DELAY,
    cache_ttl=config.XML_CACHE_TTL,
    max_snapshot_age=config.XML_MAX_SNAPSHOT_AGE,
    breaker_failure_threshold=config.XML_BREAKER_FAILURE_THRESHOLD,
    breaker_cooldown=config.XML_BREAKER_COOLDOWN,
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

    def _send_html(self, code, html):
        body = html.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            return self._route()
        except Exception as e:
            return self._send(500, {'error': 'internal_error', 'detail': str(e)})

    def _route(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        # Matching is on by default: the problem asks for "one call, one
        # resident, everything known about them", and a flag the caller has
        # to know about does not deliver that. Declining is always safe -
        # the worst case is no_match or ambiguous, never a wrong merge - so
        # there is nothing to protect the floor from by defaulting it off.
        # ?match=off (or none/false/0) opts out.
        attempt_match = q.get('match', [''])[0].lower() not in ('off', 'none', 'false', '0')

        if u.path == '/':
            with open(DEMO_HTML_PATH, encoding='utf-8') as f:
                return self._send_html(200, f.read())

        if u.path == '/health':
            return self._send(200, {
                'status': 'ok',
                'service': 'unified-resident-view',
                'resident_index': 'up' if rest_client.health() else 'down',
                'benefits_register': 'up' if benefits_client.health() else 'down',
            })

        if u.path.startswith('/unified/residents/'):
            # Everything after the prefix, not just the next segment: a
            # register ref (NO/2019/4697) contains slashes, and it is a
            # legitimate identifier here. Percent-encoded refs work too.
            identifier = unquote(u.path[len('/unified/residents/'):])
            if not identifier:
                return self._send(404, {'error': 'no_such_endpoint', 'path': u.path})
            result = build_unified_resident(identifier, rest_client, benefits_client, attempt_match)
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
