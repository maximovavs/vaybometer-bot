#!/usr/bin/env python3
"""Network-free opt-in v6.1 review-policy and fail-closed rotation acceptance."""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from datetime import date,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cyprus_culture_quiz as quiz
import cyprus_culture_review_policy_v1 as policy

V5=ROOT/'data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl'
V6=ROOT/'data/cyprus_culture/v2026-10-exam-core-v6-1/questions.jsonl'
SIDE=V6.with_name('review_policy_v1.json')
WF=ROOT/'.github/workflows/daily_post.yml'


def test_exact_72_and_no_production_activation():
    qs,entries,suspended=policy.load_candidate(V6,SIDE)
    assert len(qs)==len(entries)==72 and suspended==set()
    assert Counter(q.quiz_slot for q in qs)=={'evening_general':56,'fx_economy':16}
    assert Counter(q.temporal_policy for q in qs)=={'stable':50,'open_ended':17,'snapshot':3,'bounded':2}
    assert len({q.question_id for q in qs})==len({q.fact_key for q in qs})==72
    assert sum(entries[q.question_id]['review_group']=='historical_stable' for q in qs)==43
    assert sum(entries[q.question_id]['review_group']=='stable_monitor' for q in qs)==7
    assert sum(entries[q.question_id]['review_after'] is not None for q in qs)==29
    assert all(quiz.assemble_payload(q) is not None for q in qs)
    assert str(quiz.DEFAULT_BANK_PATH).endswith('v2026-10-exam-core-v5/questions.jsonl')
    assert quiz.load_verified_questions(V5)
    assert 'CY_CULTURE_QUIZ_BANK_VERSION: "v2026-10-exam-core-v5"' in WF.read_text('utf-8')
    assert 'CY_CULTURE_QUIZ_BANK_PATH: "data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl"' in WF.read_text('utf-8')
    runtime=(ROOT/'cyprus_culture_quiz.py').read_text('utf-8')
    # v6.1 may import the new policy only in its explicitly selected branch.
    # The active v5 bank stays on the original selector; no production switch.
    assert 'V6_1_BANK_VERSION="v2026-10-exam-core-v6-1"' in runtime
    assert 'if is_review_policy_bank_name(bank_version) or is_review_policy_bank_name(bank_path.parent.name):' in runtime
    assert 'qs=load_verified_questions(bank_path);selected=select_question(qs,' in runtime
    assert 'culture_review_policy_invalid' in runtime
    assert 'culture_bank_version_mismatch' in runtime
    assert not (ROOT/'data/cyprus_culture/v2026-10-exam-core-v5/review_policy_v1.json').exists()


def test_review_only_vs_hard_stop():
    qs,entries,suspended=policy.load_candidate(V6,SIDE)
    by={q.question_id:q for q in qs}
    iban=by['full-a-024']; assert iban.options_ru[iban.correct_option_index]=='Справку IBAN'
    assert iban.verified_as_of=='2026-10-13' and iban.revalidate_after=='2026-10-20'
    assert policy.candidate_eligibility(iban,on=date(2026,10,21),entry=entries[iban.question_id])
    assert not policy.review_due(iban,on=date(2026,10,21),entry=entries[iban.question_id])
    assert policy.candidate_eligibility(iban,on=date(2027,10,14),entry=entries[iban.question_id])
    assert policy.review_due(iban,on=date(2027,10,14),entry=entries[iban.question_id])
    assert not policy.candidate_eligibility(iban,on=date(2028,10,14),entry=entries[iban.question_id])
    assert not policy.candidate_eligibility(iban,on=date(2026,10,15),entry=entries[iban.question_id],suspended=True)
    stable_review=by['full-a-016']
    assert policy.review_due(stable_review,on=date(2027,10,15),entry=entries[stable_review.question_id])
    assert policy.candidate_eligibility(stable_review,on=date(2027,10,15),entry=entries[stable_review.question_id])
    appointed=by['full-b-cur-001']; c=entries[appointed.question_id]
    assert policy.candidate_eligibility(appointed,on=date(2027,1,14),entry=c)
    assert policy.review_due(appointed,on=date(2027,1,14),entry=c)
    assert not policy.candidate_eligibility(appointed,on=date(2027,4,14),entry=c)
    trans=by['exam-v3-hall-b15']; t=entries[trans.question_id]
    assert policy.candidate_eligibility(trans,on=date(2026,10,16),entry=t)
    assert not policy.candidate_eligibility(trans,on=date(2026,8,5),entry=t)
    for q in qs:
        if q.temporal_policy=='stable':
            assert policy.candidate_eligibility(q,on=date(2032,1,1),entry=entries[q.question_id])


def test_bounded_legal_limits_and_fail_closed():
    qs,entries,_=policy.load_candidate(V6,SIDE)
    by={q.question_id:q for q in qs}
    for id,first,last in [('full-b-eco-010','2026-05-01','2027-03-31'),('full-b-eco-011','2026-10-01','2027-05-31')]:
        q=by[id];e=entries[id]
        assert q.valid_from==first and q.valid_until==last
        assert e['max_unverified_until']==last
        assert not policy.candidate_eligibility(q,on=date.fromisoformat(first)-timedelta(days=1),entry=e)
        assert policy.candidate_eligibility(q,on=date.fromisoformat(last),entry=e)
        assert not policy.candidate_eligibility(q,on=date.fromisoformat(last)+timedelta(days=1),entry=e)
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); raw=json.loads(SIDE.read_text('utf-8'))
        def bad(version):
            file=d/'bad.json';file.write_text(json.dumps(version,ensure_ascii=False),'utf-8')
            try:policy.load_candidate(V6,file)
            except (ValueError,TypeError):return
            raise AssertionError('invalid policy was accepted')
        missing=json.loads(json.dumps(raw));missing['entries'].pop();bad(missing)
        unknown=json.loads(json.dumps(raw));unknown['entries'][0]['question_id']='unknown-id';bad(unknown)
        invalid=json.loads(json.dumps(raw));invalid['source_bank_sha256']='sha256:'+'0'*64;bad(invalid)
        invalid=json.loads(json.dumps(raw));invalid['entries'][0]['max_unverified_until']='2030-01-01';bad(invalid)
        invalid=json.loads(json.dumps(raw));a=next(e for e in invalid['entries'] if e['question_id']=='full-b-eco-010');a['max_unverified_until']='2028-01-01';bad(invalid)
        invalid=json.loads(json.dumps(raw));a=next(e for e in invalid['entries'] if e['question_id']=='full-a-024');a['review_after']=None;bad(invalid)
        invalid=json.loads(json.dumps(raw));invalid['suspended_ids']=['not-in-bank'];bad(invalid)


def test_rotations_and_receipt_safety():
    qs,entries,suspended=policy.load_candidate(V6,SIDE)
    after_old_ttl=date(2026,11,15)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        for slot,expected in [('evening_general',56),('fx_economy',16)]:
            d=root/slot
            eligible={q.question_id for q in qs if q.quiz_slot==slot and policy.candidate_eligibility(q,on=after_old_ttl,entry=entries[q.question_id])}
            assert len(eligible)==expected  # old 7-day/30-day TTL does not empty the pool
            seen=[]
            for i in range(expected):
                q=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=after_old_ttl,bank_version='v2026-10-exam-core-v6-1',receipt_dir=d)
                assert q is not None and q.question_id in eligible and q.question_id not in seen
                seen.append(q.question_id)
                receipt=quiz.quiz_receipt_path(slot,after_old_ttl-timedelta(days=i+1),d)
                receipt.parent.mkdir(parents=True,exist_ok=True)
                receipt.write_text(json.dumps({'state':'sent','quiz_slot':slot,'question_id':q.question_id}),encoding='utf-8')
            assert set(seen)==eligible
            a=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=after_old_ttl,bank_version='v2026-10-exam-core-v6-1',receipt_dir=d)
            b=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=after_old_ttl,bank_version='v2026-10-exam-core-v6-1',receipt_dir=d)
            assert a and b and a.question_id==b.question_id
        sample=policy.candidate_select(qs,entries,{'full-a-024'},slot='evening_general',quiz_date=after_old_ttl,bank_version='v2026-10-exam-core-v6-1',receipt_dir=root/'suspended')
        assert sample is not None and sample.question_id!='full-a-024'
        assert policy.candidate_select(qs,{},set(),slot='evening_general',quiz_date=after_old_ttl,bank_version='x',receipt_dir=root/'bad') is None
        for day in [date(2026,10,7),date(2027,4,14),date(2027,6,1),date(2028,11,1)]:
            for slot in quiz.QUIZ_SLOTS:
                q=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=day,bank_version='v2026-10-exam-core-v6-1',receipt_dir=root/'dates')
                if q:assert policy.candidate_eligibility(q,on=day,entry=entries[q.question_id])


def main():
    tests=[test_exact_72_and_no_production_activation,test_review_only_vs_hard_stop,test_bounded_legal_limits_and_fail_closed,test_rotations_and_receipt_safety]
    for t in tests:t();print('PASS',t.__name__)
    print(f'PASS {len(tests)}/{len(tests)} review policy suites; all 72 records covered; no production activation')
if __name__=='__main__':main()
