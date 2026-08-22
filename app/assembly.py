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


def _status(status, error=None):
    return {'status': status, 'error': error}


NOT_ATTEMPTED_REASON = 'cross-source matching is opt-in (pass ?match=basic)'


def build_unified_resident(resident_id, rest_client, benefits_client, attempt_match=False):
    unified = {'resident_id': resident_id, 'resident': None, 'benefits': None}

    try:
        resident = rest_client.get_by_id(resident_id)
    except SourceUnavailable as e:
        unified['resident_source'] = _status('unavailable', str(e))
        unified['benefits_source'] = _status('not_attempted', 'resident lookup failed; nothing to match against')
        return unified

    if resident is None:
        unified['resident_source'] = _status('not_found')
        unified['benefits_source'] = _status('not_attempted', 'no such resident')
        return unified

    unified['resident'] = resident
    unified['resident_source'] = _status('ok')

    if not attempt_match:
        unified['benefits_source'] = _status('not_attempted', NOT_ATTEMPTED_REASON)
        return unified

    try:
        xml_records = benefits_client.list_all()
    except SourceUnavailable as e:
        unified['benefits_source'] = _status('unavailable', str(e))
        return unified

    index = build_benefits_index(xml_records)
    status, record, reason = find_benefits_match(resident, index)
    unified['benefits'] = record
    unified['benefits_source'] = _status(status, reason)
    return unified


def build_unified_all(rest_client, benefits_client, attempt_match=False):
    try:
        residents = rest_client.list_all()
    except SourceUnavailable as e:
        return {'resident_source': _status('unavailable', str(e)), 'count': 0, 'residents': []}

    benefits_index = None
    benefits_source = _status('not_attempted', NOT_ATTEMPTED_REASON)
    if attempt_match:
        try:
            xml_records = benefits_client.list_all()
            benefits_index = build_benefits_index(xml_records)
            benefits_source = _status('ok')
        except SourceUnavailable as e:
            benefits_source = _status('unavailable', str(e))

    results = []
    for resident_id, resident in residents.items():
        entry = {'resident_id': resident_id, 'resident': resident, 'benefits': None}
        if benefits_index is None:
            entry['benefits_source'] = benefits_source
        else:
            status, record, reason = find_benefits_match(resident, benefits_index)
            entry['benefits'] = record
            entry['benefits_source'] = _status(status, reason)
        results.append(entry)

    return {
        'resident_source': _status('ok'),
        'benefits_source': benefits_source,
        'count': len(results),
        'residents': results,
    }
