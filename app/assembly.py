"""Assembles a unified resident view from the two sources."""
import re

from app.errors import SourceUnavailable


def _normalize_name(s):
    return re.sub(r'[^A-Z]', '', (s or '').upper())


def _xml_name_parts(name):
    last, _, first = (name or '').partition(',')
    return _normalize_name(last), _normalize_name(first)


def _resident_match_key(resident):
    return (
        _normalize_name(resident.get('last_name')),
        _normalize_name(resident.get('first_name')),
        resident.get('date_of_birth') or '',
    )


def _xml_match_key(record):
    last, first = _xml_name_parts(record.get('name'))
    return (last, first, record.get('born') or '')


def build_benefits_index(xml_records):
    """Groups benefits records by normalized (last, first, dob) key."""
    index = {}
    for r in xml_records.values():
        index.setdefault(_xml_match_key(r), []).append(r)
    return index


def find_benefits_match(resident, benefits_index):
    """Best-effort deterministic match on normalized name + date of birth."""
    key = _resident_match_key(resident)
    if not key[2]:
        return 'not_attempted', None, 'resident has no date of birth on file'
    candidates = benefits_index.get(key, [])
    if len(candidates) == 0:
        return 'no_match', None, 'no benefits record with matching name and date of birth'
    if len(candidates) > 1:
        return 'ambiguous', None, (
            f'{len(candidates)} benefits records share this name and date of birth; declining to guess'
        )
    return 'matched', candidates[0], None
