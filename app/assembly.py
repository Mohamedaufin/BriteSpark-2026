"""Assembles a unified resident view from the two sources.

Every field that can be missing is missing *loudly*: each half of the
response carries a status (ok / not_found / no_match / ambiguous /
unavailable / not_attempted) and, where relevant, a plain-language reason.
Nothing here ever raises past this module - a source failure becomes a
status, never a 500 from our own API.

Either identifier opens the view. That is the name of the problem: staff
holding a Benefits Register ref should not be told to go and find an index
id first, and a person who exists only in the register should not be
invisible. `found_by` says which door was used.
"""
import re

from app.errors import SourceUnavailable

# The match rule is deterministic - exact equality on a normalized triple -
# so every match it produces rests on identical evidence. This number is the
# measured precision of that rule against the data pack's hidden ground truth
# (scripts/match_accuracy_check.py: 306 correct, 0 wrong), not a per-pair
# score invented at runtime. It is stated as 0.99 rather than 1.0 because
# "no false positives in 306 cases" is evidence of a very good rule, not
# proof of an infallible one.
MATCH_CONFIDENCE = 0.99
MATCH_BASIS = (
    'exact match on normalized (last name, first name, date of birth), unique on '
    'both sides; measured 306/306 correct with no false positives against the '
    'data pack ground truth - see DECISIONS.md'
)

NOT_ATTEMPTED_REASON = 'cross-source matching was disabled for this request (match=off)'


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


def build_resident_key_index(residents):
    """Groups residents by the same key, so ambiguity can be checked from
    both sides rather than only from the register's."""
    index = {}
    for r in residents.values():
        index.setdefault(_resident_match_key(r), []).append(r)
    return index


def _ambiguity_reason(benefits_candidates, resident_candidates):
    """A key is only safe to act on when it identifies exactly one record on
    each side we can see. Two register rows sharing a key is the obvious
    case; two *residents* sharing one is the quiet one, because a single
    matching register row then cannot be attributed to either of them."""
    if benefits_candidates is not None and len(benefits_candidates) > 1:
        return (
            f'{len(benefits_candidates)} benefits records share this name and date '
            'of birth; declining to guess'
        )
    if resident_candidates is not None and len(resident_candidates) > 1:
        return (
            f'{len(resident_candidates)} residents share this name and date of birth, '
            'so a single benefits record cannot be attributed to one of them; '
            'declining to guess'
        )
    return None


def find_benefits_match(resident, benefits_index, resident_key_index=None):
    """Deterministic match on normalized name + date of birth.

    Returns (status, record, reason, confidence). A wrong silent merge is
    worse than declining, so every path that isn't a unique hit on both
    sides declines with a reason.
    """
    key = _resident_match_key(resident)
    if not key[2]:
        return 'not_attempted', None, 'resident has no date of birth on file', None

    benefits = benefits_index.get(key, []) if benefits_index is not None else None
    residents = resident_key_index.get(key, []) if resident_key_index is not None else None

    reason = _ambiguity_reason(benefits, residents)
    if reason:
        return 'ambiguous', None, reason, None
    if not benefits:
        return 'no_match', None, 'no benefits record with matching name and date of birth', None
    return 'matched', benefits[0], None, MATCH_CONFIDENCE


def find_resident_match(benefit, resident_key_index, benefits_index=None):
    """The same rule, run from the register's side, for the ref door."""
    key = _xml_match_key(benefit)
    if not key[2]:
        return 'not_attempted', None, 'benefits record has no date of birth on file', None

    benefits = benefits_index.get(key, []) if benefits_index is not None else None
    residents = resident_key_index.get(key, []) if resident_key_index is not None else None

    reason = _ambiguity_reason(benefits, residents)
    if reason:
        return 'ambiguous', None, reason, None
    if not residents:
        return 'no_match', None, 'no resident with matching name and date of birth', None
    return 'matched', residents[0], None, MATCH_CONFIDENCE


STALE_NOTE = (
    'this source did not answer, so these values come from the last copy we '
    'successfully fetched - they may have changed since'
)


def _status(status, error=None, confidence=None, stale_seconds=None):
    """Every source block in every response is built here, so they all have
    the same shape: what happened, and why in plain language."""
    payload = {'status': status, 'error': error}
    if confidence is not None:
        payload['confidence'] = confidence
        payload['confidence_basis'] = MATCH_BASIS
    if stale_seconds is not None:
        # Never show old data without saying it is old.
        payload['stale_seconds'] = round(stale_seconds, 1)
        payload['stale_detail'] = STALE_NOTE
    return payload


def _benefits_index_or_error(benefits_client):
    """Returns (index, error_message, seconds_old).

    index is None exactly when error_message is set. seconds_old is None
    unless we fell back to an older copy.
    """
    try:
        records, age = benefits_client.list_all_or_last_known()
        return build_benefits_index(records), None, age
    except SourceUnavailable as e:
        return None, str(e), None


def _resident_index_or_error(rest_client):
    """Returns (index, error_message, seconds_old). Same shape as above."""
    try:
        records, age = rest_client.list_all_or_last_known()
        return build_resident_key_index(records), None, age
    except SourceUnavailable as e:
        return None, str(e), None


def build_unified_resident(identifier, rest_client, benefits_client, attempt_match=True):
    """One resident, opened by whichever identifier the caller holds."""
    unified = {'resident_id': None, 'found_by': None, 'resident': None, 'benefits': None}

    # Door 1: a Resident Index id.
    resident, index_error = None, None
    try:
        resident = rest_client.get_by_id(identifier)
    except SourceUnavailable as e:
        index_error = str(e)

    if resident is not None:
        unified['resident_id'] = resident.get('id', identifier)
        unified['found_by'] = 'resident_id'
        unified['resident'] = resident
        unified['resident_source'] = _status('ok')

        if not attempt_match:
            unified['benefits_source'] = _status('not_attempted', NOT_ATTEMPTED_REASON)
            return unified

        benefits_index, benefits_error, benefits_age = _benefits_index_or_error(benefits_client)
        if benefits_index is None:
            unified['benefits_source'] = _status('unavailable', benefits_error)
            return unified

        # None here means "we could not check the index side", which is
        # different from "we checked and it was unique" - find_benefits_match
        # skips the check rather than assuming it passed.
        resident_keys, _, _ = _resident_index_or_error(rest_client)
        status, record, reason, confidence = find_benefits_match(
            resident, benefits_index, resident_keys
        )
        unified['benefits'] = record
        unified['benefits_source'] = _status(status, reason, confidence, benefits_age)
        return unified

    # Door 2: a Benefits Register ref.
    benefit, register_error = None, None
    try:
        benefit = benefits_client.get_by_ref(identifier)
    except SourceUnavailable as e:
        register_error = str(e)

    if benefit is not None:
        unified['found_by'] = 'register_ref'
        unified['benefits'] = benefit
        unified['benefits_source'] = _status('ok')

        if not attempt_match:
            unified['resident_source'] = _status('not_attempted', NOT_ATTEMPTED_REASON)
            return unified

        resident_keys, keys_error, resident_age = _resident_index_or_error(rest_client)
        if resident_keys is None:
            unified['resident_source'] = _status('unavailable', keys_error)
            return unified

        benefits_index, _, _ = _benefits_index_or_error(benefits_client)
        status, record, reason, confidence = find_resident_match(
            benefit, resident_keys, benefits_index
        )
        unified['resident'] = record
        unified['resident_id'] = record.get('id') if record else None
        unified['resident_source'] = _status(status, reason, confidence, resident_age)
        return unified

    # Neither door opened. A source we could not reach is not evidence of
    # absence, so an unreachable source is reported as unavailable rather
    # than letting the caller read this as "no such person".
    unified['resident_id'] = identifier
    unified['resident_source'] = (
        _status('unavailable', index_error) if index_error
        else _status('not_found', 'no resident with this id in the resident index')
    )
    unified['benefits_source'] = (
        _status('unavailable', register_error) if register_error
        else _status('not_found', 'no record with this ref in the benefits register')
    )
    if index_error or register_error:
        unified['warning'] = (
            'At least one source could not be reached, so this identifier cannot be '
            'confirmed as absent - only as not found in the sources that answered.'
        )
    return unified


def build_unified_all(rest_client, benefits_client, attempt_match=True):
    try:
        residents, resident_age = rest_client.list_all_or_last_known()
    except SourceUnavailable as e:
        # count stays null rather than 0: we did not look and find nothing,
        # we could not look at all, and 0 would assert the former.
        return {
            'resident_source': _status('unavailable', str(e)),
            'benefits_source': _status('not_attempted', 'resident index unavailable; nothing to match against'),
            'count': None,
            'residents': [],
            'warning': (
                'The resident index could not be reached. This is not an empty '
                'population - it is an unknown one.'
            ),
        }

    benefits_index = None
    benefits_source = _status('not_attempted', NOT_ATTEMPTED_REASON)
    if attempt_match:
        benefits_index, benefits_error, benefits_age = _benefits_index_or_error(benefits_client)
        benefits_source = (
            _status('ok', stale_seconds=benefits_age) if benefits_index is not None
            else _status('unavailable', benefits_error)
        )

    resident_keys = build_resident_key_index(residents) if benefits_index is not None else None

    results = []
    for resident_id, resident in residents.items():
        entry = {
            'resident_id': resident_id,
            'found_by': 'resident_id',
            'resident': resident,
            'benefits': None,
        }
        if benefits_index is None:
            entry['benefits_source'] = benefits_source
        else:
            status, record, reason, confidence = find_benefits_match(
                resident, benefits_index, resident_keys
            )
            entry['benefits'] = record
            entry['benefits_source'] = _status(status, reason, confidence)
        results.append(entry)

    return {
        'resident_source': _status('ok', stale_seconds=resident_age),
        'benefits_source': benefits_source,
        'count': len(results),
        'residents': results,
    }
