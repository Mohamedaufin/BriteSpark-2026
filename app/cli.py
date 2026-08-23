"""Command-line demo of the unified resident view, no HTTP server required.

    python -m app.cli R-10697
    python -m app.cli NO/2019/4697        (either identifier opens the view)
    python -m app.cli R-10697 --no-match
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
    parser.add_argument('identifier', nargs='?', help='resident id (R-10697) or register ref (NO/2019/4697)')
    parser.add_argument('--no-match', dest='match', action='store_false',
                        help='skip cross-source matching (on by default)')
    parser.add_argument('--demo', action='store_true', help='walk the index and print the first few residents')
    args = parser.parse_args()

    rest_client = ResidentIndexClient(
        config.REST_BASE_URL, timeout=config.REST_TIMEOUT, cache_ttl=config.REST_CACHE_TTL
    )
    benefits_client = BenefitsRegisterClient(
        config.XML_BASE_URL,
        timeout=config.XML_TIMEOUT,
        max_retries=config.XML_MAX_RETRIES,
        retry_base_delay=config.XML_RETRY_BASE_DELAY,
        cache_ttl=config.XML_CACHE_TTL,
        breaker_failure_threshold=config.XML_BREAKER_FAILURE_THRESHOLD,
        breaker_cooldown=config.XML_BREAKER_COOLDOWN,
    )

    if args.demo:
        residents = rest_client.list_all()
        print(f'Walked resident index: {len(residents)} unique residents after de-duplication.\n')
        for rid in list(residents.keys())[:3]:
            print(json.dumps(build_unified_resident(rid, rest_client, benefits_client, args.match), indent=2))
            print()
        return

    if not args.identifier:
        parser.error('an identifier is required unless --demo is given')

    print(json.dumps(build_unified_resident(args.identifier, rest_client, benefits_client, args.match), indent=2))


if __name__ == '__main__':
    main()
