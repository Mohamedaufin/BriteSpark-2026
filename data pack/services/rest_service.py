#!/usr/bin/env python3
"""
Calder County — Resident Index (REST)

A paginated JSON service over the resident index. Stdlib only.

    python3 rest_service.py [--port 8081]

Endpoints
    GET  /residents?page=1&page_size=25
    GET  /residents/<id>
    GET  /health
"""
import json, os, random, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, '_rest_data.json'), encoding='utf-8'))
for r in DATA:
    r.pop('_pid', None)

DEFAULT_PAGE_SIZE = 25

def build_pages(records, size):
    """
    Offset pagination over a result set whose sort key is not stable.

    In production this index is ordered by last_contact, which is updated by
    other processes while a client is paging through. The practical effect is
    that records near a page boundary can be served twice.
    """
    rng = random.Random(97531)
    pages, i = [], 0
    while i < len(records):
        page = records[i:i + size]
        pages.append(page)
        step = size
        if i + size < len(records) and rng.random() < 0.60:
            step = size - rng.choice([1, 2, 3])   # boundary slips back
        i += step
    return pages

PAGES = build_pages(DATA, DEFAULT_PAGE_SIZE)

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, payload):
        body = json.dumps(payload, indent=1).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == '/health':
            return self._send(200, {'status': 'ok', 'service': 'resident-index'})

        if u.path.startswith('/residents/'):
            rid = u.path.rsplit('/', 1)[-1]
            for r in DATA:
                if r['id'] == rid:
                    return self._send(200, r)
            return self._send(404, {'error': 'not_found', 'id': rid})

        if u.path == '/residents':
            try:
                page = int(q.get('page', ['1'])[0])
            except ValueError:
                return self._send(400, {'error': 'bad_page'})
            if page < 1 or page > len(PAGES):
                return self._send(200, {'page': page, 'page_size': DEFAULT_PAGE_SIZE,
                                        'total': len(DATA), 'has_more': False, 'results': []})
            return self._send(200, {
                'page': page,
                'page_size': DEFAULT_PAGE_SIZE,
                'total': len(DATA),
                'has_more': page < len(PAGES),
                'results': PAGES[page - 1],
            })

        return self._send(404, {'error': 'no_such_endpoint', 'path': u.path})

    def log_message(self, fmt, *a):
        print(f'  [rest] {fmt % a}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8081)
    args = ap.parse_args()
    print(f'Resident Index (REST) on http://127.0.0.1:{args.port}')
    print(f'  {len(DATA)} records across {len(PAGES)} pages of {DEFAULT_PAGE_SIZE}')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
