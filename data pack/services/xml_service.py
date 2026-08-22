#!/usr/bin/env python3
"""
Calder County — Benefits Register (XML)

A legacy XML service. It is slow, and it fails sometimes. Both are normal for it.
Stdlib only.

    python3 xml_service.py [--port 8082] [--failure-rate 0.15]

The failure rate may also be set with BENEFITS_FAILURE_RATE.

Endpoints
    GET  /records
    GET  /records/<ref>
    GET  /health
"""
import json, os, random, time, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, '_xml_data.json'), encoding='utf-8'))
for r in DATA:
    r.pop('_pid', None)

FAILURE_RATE = float(os.environ.get('BENEFITS_FAILURE_RATE', '0.15'))
MIN_DELAY, MAX_DELAY = 0.7, 2.4

def to_xml(records):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<BenefitsRegister>']
    for r in records:
        parts.append('  <Record>')
        parts.append(f'    <Ref>{escape(r["ref"])}</Ref>')
        parts.append(f'    <Name>{escape(r["name"])}</Name>')
        parts.append(f'    <Born>{escape(r["born"])}</Born>')
        parts.append(f'    <Addr>{escape(r["addr"])}</Addr>')
        parts.append(f'    <Town>{escape(r["town"])}</Town>')
        parts.append(f'    <BenefitCode>{escape(r["benefit_code"])}</BenefitCode>')
        parts.append(f'    <ReviewDue>{escape(r["review_due"])}</ReviewDue>')
        parts.append('  </Record>')
    parts.append('</BenefitsRegister>')
    return '\n'.join(parts)

ERROR_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Fault><Code>SRV-500</Code>'
             '<Message>Register temporarily unavailable. Retry.</Message></Fault>')

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body, ctype='application/xml'):
        b = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)

        if u.path == '/health':
            return self._send(200, '<?xml version="1.0"?><Health><Status>ok</Status></Health>')

        # It is slow. Always.
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        # It fails. Sometimes.
        if random.random() < FAILURE_RATE:
            return self._send(500, ERROR_XML)

        if u.path.startswith('/records/'):
            ref = unquote(u.path[len('/records/'):])
            for r in DATA:
                if r['ref'] == ref:
                    return self._send(200, to_xml([r]))
            return self._send(404, '<?xml version="1.0"?><Fault><Code>SRV-404</Code>'
                                   '<Message>No such record</Message></Fault>')

        if u.path == '/records':
            return self._send(200, to_xml(DATA))

        return self._send(404, '<?xml version="1.0"?><Fault><Code>SRV-404</Code>'
                               '<Message>Unknown path</Message></Fault>')

    def log_message(self, fmt, *a):
        print(f'  [xml] {fmt % a}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8082)
    ap.add_argument('--failure-rate', type=float, default=None)
    args = ap.parse_args()
    if args.failure_rate is not None:
        FAILURE_RATE = args.failure_rate
    print(f'Benefits Register (XML) on http://127.0.0.1:{args.port}')
    print(f'  {len(DATA)} records | failure rate {FAILURE_RATE:.0%} | delay {MIN_DELAY}-{MAX_DELAY}s')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
