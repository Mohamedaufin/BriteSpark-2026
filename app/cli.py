"""Command-line demo of the unified resident view, no HTTP server required.

    python -m app.cli R-10234
    python -m app.cli R-10234 --match
    python -m app.cli --demo
"""
import argparse
import json

from app import config
from app.adapters.benefits_register import BenefitsRegisterClient
from app.adapters.resident_index import ResidentIndexClient
from app.assembly import build_unified_resident


def main():
    parser = argparse.ArgumentParser(description='Unified resident view CLI')
    parser.add_argument('resident_id', nargs='?', help='e.g. R-10234')
    parser.add_argument('--match', action='store_true', help='attempt best-effort name+DOB matching')
    parser.add_argument('--demo', action='store_true', help='walk the index and print the first few residents')
    args = parser.parse_args()

    rest_client = ResidentIndexClient(config.REST_BASE_URL, timeout=config.REST_TIMEOUT)
    benefits_client = BenefitsRegisterClient(
        config.XML_BASE_URL,
        timeout=config.XML_TIMEOUT,
        max_retries=config.XML_MAX_RETRIES,
        retry_base_delay=config.XML_RETRY_BASE_DELAY,
    )

    if args.demo:
        residents = rest_client.list_all()
        print(f'Walked resident index: {len(residents)} unique residents after de-duplication.\n')
        for rid in list(residents.keys())[:3]:
            print(json.dumps(build_unified_resident(rid, rest_client, benefits_client, args.match), indent=2))
            print()
        return

    if not args.resident_id:
        parser.error('resident_id is required unless --demo is given')

    print(json.dumps(build_unified_resident(args.resident_id, rest_client, benefits_client, args.match), indent=2))


if __name__ == '__main__':
    main()
