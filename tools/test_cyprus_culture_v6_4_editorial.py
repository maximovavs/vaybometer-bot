#!/usr/bin/env python3
"""Offline v6.4 successor acceptance; fail closed, never send Telegram/network."""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
import cyprus_culture_quiz as quiz
import cyprus_culture_review_policy_v1 as policy
from test_cyprus_culture_v6_3_runtime import simulate, DAY

DATA=ROOT/'data/cyprus_culture'
V63=DATA/'v2026-10-exam-core-v6-3/questions.jsonl'
V64=DATA/'v2026-10-exam-core-v6-4/questions.jsonl'
BANK_SHA='7c5d32f456a2074cf9fe4e093b53018a2b1115143200da3545426b4f7c49b954'
POLICY_SHA='c696a98cab22b1d5851d4a8f7034467a7a89bda319d2dd316f1f34afd959884f'
MANIFEST_SHA='03988f26fa672b9dba33a809bb5f9f1752b2aee3893bacea3b0ab08cac61fba5'
ALLOWED={('full-a-003','explanation_ru'),('full-a-035','options_el'),
         ('full-a-035','options_ru'),('full-a-035','explanation_ru')}

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def records(p):return [json.loads(l) for l in p.read_text('utf-8').splitlines() if l.strip()]

def test_immutability_manifest_policy_and_72_records():
    assert digest(V63)=='67f931b910e77fb4fdd631538a71f3bed2670ccb69718193c4f336f43edc3346'
    assert (digest(V64),digest(V64.with_name('review_policy_v1.json')),digest(V64.with_name('manifest.json')))==(BANK_SHA,POLICY_SHA,MANIFEST_SHA)
    assert quiz.V6_4_BANK_VERSION=='v2026-10-exam-core-v6-4'
    assert quiz.V6_4_BANK_VERSION in quiz.REVIEW_POLICY_BANK_VERSIONS
    assert quiz.V6_4_BANK_VERSION not in quiz.LEGACY_BANK_VERSIONS
    assert policy.PINNED_REVIEW_BANKS[quiz.V6_4_BANK_VERSION]=={
        'policy_version':'cy-culture-v6-4-review-v1','bank_sha256':'sha256:'+BANK_SHA,
        'policy_sha256':'sha256:'+POLICY_SHA,'manifest_sha256':'sha256:'+MANIFEST_SHA}
    m=json.loads(V64.with_name('manifest.json').read_text('utf-8'))
    assert m['immutability'] is True and m['question_count']==m['verified_count']==72
    assert m['bank_version']==quiz.V6_4_BANK_VERSION and m['bank_sha256']=='sha256:'+BANK_SHA
    assert m['slot_counts']=={'evening_general':56,'fx_economy':16}
    review=json.loads(V64.with_name('review_policy_v1.json').read_text('utf-8'))
    assert review['status']=='candidate_inactive' and len(review['entries'])==72
    assert review['suspended_ids']==[]
    assert review['source_bank_sha256']=='sha256:'+BANK_SHA
    assert [q['question_id'] for q in records(V64)]==m['ordered_question_ids']

def test_exact_two_record_four_field_delta():
    a,b=records(V63),records(V64)
    assert len(a)==len(b)==72
    changed={(x['question_id'],k) for x,y in zip(a,b)
             for k in x if x[k]!=y[k]}
    assert changed==ALLOWED,changed
    assert [q['correct_option_index'] for q in a]==[q['correct_option_index'] for q in b]
    assert [q['quiz_slot'] for q in a]==[q['quiz_slot'] for q in b]
    assert [q['rotation_rank'] for q in a]==[q['rotation_rank'] for q in b]
    assert [(q['answer_source'],q['answer_sources'],q['source'],q['verified_as_of'],
             q['valid_from'],q['valid_until'],q['revalidate_after'],q['temporal_policy']) for q in a]==[
            (q['answer_source'],q['answer_sources'],q['source'],q['verified_as_of'],
             q['valid_from'],q['valid_until'],q['revalidate_after'],q['temporal_policy']) for q in b]
    assert Counter(q['quiz_slot'] for q in b)=={'evening_general':56,'fx_economy':16}

def test_greek_russian_errata_and_single_wrong_answer():
    q={x['question_id']:x for x in records(V64)}
    arch=q['full-a-003']
    assert 'Νεολιθικός — «неолитический»' in arch['explanation_ru']
    assert arch['correct_option_index']==2
    road=q['full-a-035']
    assert road['correct_option_index']==2
    assert 'Οδηγοί χωρίς μειωμένο όριο' in road['options_el'][3]
    assert '22 μg/100 ml' in road['options_el'][3]
    assert 'Водители без сниженного лимита' in road['options_ru'][3]
    assert '22 мкг/100 мл' in road['options_ru'][3]
    assert '9.' in road['explanation_ru']
    assert road['options_el'][2]=='Όλα τα φαρμακεία μένουν ανοικτά ως τις 19:30'
    assert road['options_ru'][2]=='Все аптеки открыты до 19:30'

def test_candidate_loader_telegram_payloads_and_partition():
    qs,entries,suspended=policy.load_candidate(V64,V64.with_name('review_policy_v1.json'),bank_version=quiz.V6_4_BANK_VERSION)
    assert len(qs)==len(entries)==72 and not suspended
    assert Counter(q.quiz_slot for q in qs)=={'evening_general':56,'fx_economy':16}
    for q in qs:
        p=quiz.assemble_payload(q)
        assert p is not None,q.question_id
        assert len(p.question)<=300 and len(p.explanation)<=200,q.question_id
        assert len(p.options)==4 and all(len(o)<=100 for o in p.options),q.question_id

def test_review_policy_fail_closed_on_tampering():
    for name in ('questions.jsonl','manifest.json','review_policy_v1.json'):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)/quiz.V6_4_BANK_VERSION;d.mkdir()
            for other in ('questions.jsonl','manifest.json','review_policy_v1.json'):
                shutil.copyfile(V64.with_name(other),d/other)
            p=d/name;p.write_bytes(p.read_bytes()+b' ')
            try:policy.load_candidate(d/'questions.jsonl',d/'review_policy_v1.json',bank_version=quiz.V6_4_BANK_VERSION)
            except (ValueError,RuntimeError,TypeError,KeyError,UnicodeError,json.JSONDecodeError):pass
            else:raise AssertionError(f'Fail-open on mutated {name}')
    # A valid v6.3 file cannot be silently treated as v6.4.
    try:policy.load_candidate(V63,V63.with_name('review_policy_v1.json'),bank_version=quiz.V6_4_BANK_VERSION)
    except ValueError:pass
    else:raise AssertionError('Cross-version bank accepted')

def test_two_slots_receipts_idempotency_no_legacy_selector():
    qs,entries,suspended=policy.load_candidate(V64,V64.with_name('review_policy_v1.json'),bank_version=quiz.V6_4_BANK_VERSION)
    for slot in ('evening_general','fx_economy'):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);sent=[];legacy=[]
            expected=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=DAY,bank_version=quiz.V6_4_BANK_VERSION,receipt_dir=root/'quiz')
            assert expected is not None
            first=simulate(slot,V64,quiz.V6_4_BANK_VERSION,root,sent=sent,legacy=legacy)
            assert first['result']=='quiz_sent' and first['question_id']==expected.question_id
            assert sent==[quiz.assemble_payload(expected)] and legacy==[]
            receipt=json.loads(quiz.quiz_receipt_path(slot,DAY,root/'quiz').read_text('utf-8'))
            assert receipt['state']=='sent' and receipt['bank_version']==quiz.V6_4_BANK_VERSION
            again=simulate(slot,V64,quiz.V6_4_BANK_VERSION,root,sent=sent,legacy=legacy)
            assert again['result']=='quiz_skipped_receipt_exists' and len(sent)==1

def main():
    tests=[test_immutability_manifest_policy_and_72_records,test_exact_two_record_four_field_delta,
           test_greek_russian_errata_and_single_wrong_answer,test_candidate_loader_telegram_payloads_and_partition,
           test_review_policy_fail_closed_on_tampering,test_two_slots_receipts_idempotency_no_legacy_selector]
    for t in tests:t();print('PASS',t.__name__)
    print(f'PASS {len(tests)}/{len(tests)} v6.4 editorial/runtime offline tests; zero network')

if __name__=='__main__':main()
