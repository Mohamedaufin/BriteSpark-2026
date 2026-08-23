"""Offline validation of the name+DOB matching heuristic in app/assembly.py.

This is NOT part of the running solution and never touches the live
services. It reads the raw data files directly - which the data pack README
explicitly permits ("You may read these, but your solution must go through
the services") - to use the hidden `_pid` field as ground truth for which
REST and XML records describe the same person. The running solution never
sees `_pid`; the mock services strip it before serving.

Purpose: put a number on "how good is this match, really" instead of
asserting it. Referenced in DECISIONS.md.

    python scripts/match_accuracy_check.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.assembly import (  # noqa: E402
    build_benefits_index,
    build_resident_key_index,
    find_benefits_match,
)

SERVICES_DIR = os.path.join(ROOT, 'data pack', 'services')


def main():
    with open(os.path.join(SERVICES_DIR, '_rest_data.json'), encoding='utf-8') as f:
        rest = json.load(f)
    with open(os.path.join(SERVICES_DIR, '_xml_data.json'), encoding='utf-8') as f:
        xml_raw = json.load(f)

    xml_records = {r['ref']: r for r in xml_raw}
    xml_pid_by_ref = {r['ref']: r['_pid'] for r in xml_raw}
    index = build_benefits_index(xml_records)

    # Measure the rule the API actually runs, which checks both sides for
    # ambiguity - not a simplified version of it.
    resident_keys = build_resident_key_index({r['id']: r for r in rest})

    true_pairs = matched_correct = matched_wrong = ambiguous_true = no_candidate = 0
    xml_pids = {r['_pid'] for r in xml_raw}

    for r in rest:
        if r['_pid'] not in xml_pids:
            continue  # this resident genuinely has no benefits record - not our problem
        true_pairs += 1
        status, record, _reason, _confidence = find_benefits_match(r, index, resident_keys)
        if status == 'matched':
            if xml_pid_by_ref[record['ref']] == r['_pid']:
                matched_correct += 1
            else:
                matched_wrong += 1
        elif status == 'ambiguous':
            ambiguous_true += 1
        else:
            no_candidate += 1

    print(f'True cross-source pairs in the dataset: {true_pairs}')
    print(f'  matched correctly:                    {matched_correct}')
    print(f'  matched to the WRONG record:           {matched_wrong}  <-- would be a silent false positive')
    print(f'  declined as ambiguous (key collision):  {ambiguous_true}')
    print(f'  no candidate found (declined to guess): {no_candidate}')
    print()
    print(f'Recall:    {matched_correct / true_pairs:.1%} of true matches actually found')
    print(f'Precision: {matched_correct / (matched_correct + matched_wrong) if (matched_correct + matched_wrong) else 1:.1%} '
          f'of matches returned were correct')


if __name__ == '__main__':
    main()
