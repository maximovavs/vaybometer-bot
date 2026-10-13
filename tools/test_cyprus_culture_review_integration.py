#!/usr/bin/env python3
"""Pure-offline v6.1 review-policy delivery integration; never call Telegram/network."""
from __future__ import annotations
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cyprus_culture_quiz as quiz
import cyprus_culture_review_policy_v1 as policy

V5=ROOT/'data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl'
V6=ROOT/'data/cyprus_culture/v2026-10-exam-core-v6-1/questions.jsonl'
SIDE=V6.with_name('review_policy_v1.json')


def write_json(p:Path, obj:dict):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False)+'\n','utf-8')


def inputs(root:Path, day:date):
    wd=root/'weather';fd=root/'fx';qd=root/'quiz'
    write_json(wd/f'{(day+timedelta(days=1)).isoformat()}-evening.json',{
        'target_date':(day+timedelta(days=1)).isoformat(),'post_type':'evening','chat_type':'production',
        'text_chunk_count':1,'telegram_message_ids':[101],'sent_at_utc':'2026-11-15T13:00:00Z'})
    write_json(fd/f'{day.isoformat()}.json',{
        'publication_date':day.isoformat(),'chat_type':'production',
        'telegram_message_id':102,'sent_at_utc':'2026-11-15T10:00:00Z'})
    return wd,fd,qd


async def simulate(slot:str, bank:Path, version:str, root:Path, day:date, *, event='schedule', sent:list|None=None):
    wd,fd,qd=inputs(root,day)
    if sent is None:sent=[]
    async def stub(**kwargs):
        sent.append(kwargs['payload'])
        return SimpleNamespace(message_id=123,poll=SimpleNamespace(id='fake-poll-123'))
    return await quiz.deliver_quiz(
        slot=slot,enabled=True,event_name=event,
        event_schedule='0 16 * * *' if slot=='evening_general' else '0 12 * * *',
        chat_id='test-not-a-channel',token='test-not-a-real-token',
        bank_path=bank,bank_version=version,anchor_date_text='2026-10-07',
        weather_receipt_dir=wd,fx_receipt_dir=fd,quiz_receipt_dir=qd,
        now=datetime.combine(day,datetime.min.time(),tzinfo=ZoneInfo('Asia/Nicosia')),
        send_poll=stub,run_id='offline',run_attempt='1')


def test_default_v5_unchanged():
    assert quiz.DEFAULT_BANK_PATH.as_posix().endswith('v2026-10-exam-core-v5/questions.jsonl')
    assert quiz.V6_1_BANK_VERSION=='v2026-10-exam-core-v6-1'
    assert 'cyprus_culture_review_policy_v1' not in (ROOT/'.github/workflows/daily_post.yml').read_text('utf-8')
    day=date(2026,11,15)
    for slot in ('evening_general','fx_economy'):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); sent=[]
            expected=quiz.select_question(quiz.load_verified_questions(V5),slot=slot,quiz_date=day,bank_version='v2026-10-exam-core-v5',receipt_dir=root/'quiz')
            assert expected
            result=asyncio.run(simulate(slot,V5,'v2026-10-exam-core-v5',root,day,sent=sent))
            assert result['result']=='quiz_sent' and result['question_id']==expected.question_id
            assert len(sent)==1 and sent[0]==quiz.assemble_payload(expected)


def test_v6_both_slots_and_duplicate_receipts():
    day=date(2026,11,15)
    questions,entries,suspended=policy.load_candidate(V6,SIDE)
    for slot,total in [('evening_general',56),('fx_economy',16)]:
        eligible=[q for q in questions if q.quiz_slot==slot and policy.candidate_eligibility(q,on=day,entry=entries[q.question_id])]
        assert len(eligible)==total
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);sent=[]
            expected=policy.candidate_select(questions,entries,suspended,slot=slot,quiz_date=day,bank_version=quiz.V6_1_BANK_VERSION,receipt_dir=root/'quiz')
            assert expected
            result=asyncio.run(simulate(slot,V6,quiz.V6_1_BANK_VERSION,root,day,sent=sent))
            assert result['result']=='quiz_sent' and result['question_id']==expected.question_id
            assert len(sent)==1 and sent[0]==quiz.assemble_payload(expected)
            receipt=quiz.quiz_receipt_path(slot,day,root/'quiz')
            assert json.loads(receipt.read_text('utf-8'))['state']=='sent'
            again=asyncio.run(simulate(slot,V6,quiz.V6_1_BANK_VERSION,root,day,sent=sent))
            assert again['result']=='quiz_skipped_receipt_exists' and len(sent)==1
            assert len(quiz.load_verified_questions(V6))==72


def test_invalid_policy_no_send_no_reservation():
    day=date(2026,11,15)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);candidate=root/'v2026-10-exam-core-v6-1';candidate.mkdir()
        bank=candidate/'questions.jsonl'
        shutil.copyfile(V6,bank)
        sent=[]
        first=asyncio.run(simulate('evening_general',bank,quiz.V6_1_BANK_VERSION,root/'case1',day,sent=sent))
        assert first['result']=='quiz_skipped_invalid_question' and first['reason']=='culture_review_policy_invalid'
        assert not sent and not (root/'case1'/'quiz').exists()
        metadata=json.loads(SIDE.read_text('utf-8'))
        for label,change in (
            ('bank-hash',lambda m:m.__setitem__('source_bank_sha256','sha256:'+'0'*64)),
            ('missing-question',lambda m:m['entries'].pop()),
            ('wrong-status',lambda m:m.__setitem__('status','active')),
        ):
            bad=json.loads(json.dumps(metadata))
            change(bad)
            write_json(candidate/'review_policy_v1.json',bad)
            case=root/label
            result=asyncio.run(simulate('fx_economy',bank,quiz.V6_1_BANK_VERSION,case,day,sent=sent))
            assert result['result']=='quiz_skipped_invalid_question' and result['reason']=='culture_review_policy_invalid'
            assert not sent and not (case/'quiz').exists()
        write_json(candidate/'review_policy_v1.json',metadata)
        badver=asyncio.run(simulate('evening_general',bank,'v2026-10-exam-core-v5',root/'version-mismatch',day,sent=sent))
        assert badver['result']=='quiz_skipped_invalid_question' and badver['reason']=='culture_bank_version_mismatch'
        wrongbank=asyncio.run(simulate('evening_general',V5,quiz.V6_1_BANK_VERSION,root/'wrong-bank',day,sent=sent))
        assert wrongbank['result']=='quiz_skipped_invalid_question' and wrongbank['reason']=='culture_review_policy_invalid'
        manual=asyncio.run(simulate('fx_economy',V6,quiz.V6_1_BANK_VERSION,root/'manual',day,event='workflow_dispatch',sent=sent))
        assert manual['result']=='quiz_skipped_non_production' and not sent


def test_guards_and_no_policy_weakening():
    qs,entries,suspended=policy.load_candidate(V6,SIDE)
    assert len(qs)==len(entries)==72 and not suspended
    by={q.question_id:q for q in qs}
    iban=by['full-a-024'];assert iban.options_ru[iban.correct_option_index]=='Справку IBAN'
    assert iban.revalidate_after=='2026-10-20'
    assert policy.candidate_eligibility(iban,on=date(2027,10,14),entry=entries[iban.question_id])
    assert policy.review_due(iban,on=date(2027,10,14),entry=entries[iban.question_id])
    assert not policy.candidate_eligibility(iban,on=date(2028,10,14),entry=entries[iban.question_id])
    for q in qs:
        if q.temporal_policy!='bounded':continue
        e=entries[q.question_id]
        assert e['max_unverified_until']==q.valid_until
        assert not policy.candidate_eligibility(q,on=date.fromisoformat(q.valid_from)-timedelta(days=1),entry=e)
        assert not policy.candidate_eligibility(q,on=date.fromisoformat(q.valid_until)+timedelta(days=1),entry=e)
    day=date(2026,11,15)
    with tempfile.TemporaryDirectory() as td:
        no_iban=policy.candidate_select(qs,entries,{'full-a-024'},slot='evening_general',quiz_date=day,bank_version=quiz.V6_1_BANK_VERSION,receipt_dir=Path(td))
        assert no_iban and no_iban.question_id!='full-a-024'
    assert any(q.temporal_policy=='stable' for q in qs)


def main():
    cases=[test_default_v5_unchanged,test_v6_both_slots_and_duplicate_receipts,test_invalid_policy_no_send_no_reservation,test_guards_and_no_policy_weakening]
    for c in cases:
        c()
        print('PASS',c.__name__)
    print(f'PASS {len(cases)}/{len(cases)} offline v6.1 delivery integration suites; no Telegram/network')

if __name__=='__main__':main()
