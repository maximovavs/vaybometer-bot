#!/usr/bin/env python3
"""Offline v6.3 review-policy runtime acceptance; never call Telegram/network."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import socket
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cyprus_culture_quiz as quiz
import cyprus_culture_review_policy_v1 as policy

DATA=ROOT/'data/cyprus_culture'
V5=DATA/'v2026-10-exam-core-v5/questions.jsonl'
V61=DATA/'v2026-10-exam-core-v6-1/questions.jsonl'
V63=DATA/'v2026-10-exam-core-v6-3/questions.jsonl'
V61_SHA='2574297b91f10341f9e173661913867aeeb8b12d33aa1fa2591774340f28ef99'
V63_SHA='67f931b910e77fb4fdd631538a71f3bed2670ccb69718193c4f336f43edc3346'
V61_POLICY_SHA='d96caf7f6179d19c5135597a0b634afb6185b58d028eb947d38fdab5c2f2ea5f'
V61_MANIFEST_SHA='e35c92a1df7adb02523eecf5042e3610fa3946a0f492532057ff0b825cd7bcc5'
V63_POLICY_SHA='0b20d4b617f8f5a6797eb7e6ed18bfaa748400a43fb250ec234b40a403e887fa'
V63_MANIFEST_SHA='59c013a5046f47555fe6d9ffe319bca78939703e6c4b50ba9a2f98055927a8a5'
# Superseded, never shipped: full-a-001 option 2 rendered as 110 characters.
V62_VERSION='v2026-10-exam-core-v6-2'
EDITED={('full-a-001','options_el'),('full-a-001','explanation_ru'),('full-a-035','explanation_ru'),
        ('full-a-004','explanation_ru'),('full-b-cur-012','question_ru'),('full-b-cur-012','explanation_ru'),
        ('full-a-001','options_ru')}
DAY=date(2026,11,15)


def _deny_network(*_a,**_k):
    raise AssertionError('Culture v6.3 runtime tests forbid network access')
socket.socket.connect=_deny_network
socket.create_connection=_deny_network


def write_json(p:Path,obj:dict):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False)+'\n','utf-8')


def simulate(slot:str,bank:Path,version:str,root:Path,*,sent:list,legacy:list):
    """Deliver with stubbed Telegram; record any use of the legacy loader on the bank path."""
    wd,fd,qd=root/'weather',root/'fx',root/'quiz'
    write_json(wd/f'{(DAY+timedelta(days=1)).isoformat()}-evening.json',{
        'target_date':(DAY+timedelta(days=1)).isoformat(),'post_type':'evening','chat_type':'production',
        'text_chunk_count':1,'telegram_message_ids':[101],'sent_at_utc':'2026-11-15T13:00:00Z'})
    write_json(fd/f'{DAY.isoformat()}.json',{'publication_date':DAY.isoformat(),'chat_type':'production',
        'telegram_message_id':102,'sent_at_utc':'2026-11-15T10:00:00Z'})
    async def stub(**kwargs):
        sent.append(kwargs['payload'])
        return SimpleNamespace(message_id=123,poll=SimpleNamespace(id='fake-poll-123'))
    original=quiz.select_question
    def spy(questions,**kwargs):
        if kwargs.get('bank_version')==version and not getattr(spy,'inside_policy',False):
            legacy.append(version)
        return original(questions,**kwargs)
    original_candidate=policy.candidate_select
    def candidate(*a,**k):
        spy.inside_policy=True
        try:return original_candidate(*a,**k)
        finally:spy.inside_policy=False
    quiz.select_question=spy;policy.candidate_select=candidate
    try:
        return asyncio.run(quiz.deliver_quiz(
            slot=slot,enabled=True,event_name='schedule',
            event_schedule='0 16 * * *' if slot=='evening_general' else '0 12 * * *',
            chat_id='test-not-a-channel',token='test-not-a-real-token',
            bank_path=bank,bank_version=version,anchor_date_text='2026-10-07',
            weather_receipt_dir=wd,fx_receipt_dir=fd,quiz_receipt_dir=qd,
            now=datetime.combine(DAY,time(16,1) if slot=='evening_general' else time(12,1),tzinfo=ZoneInfo('Asia/Nicosia')),
            send_poll=stub,run_id='offline',run_attempt='1'))
    finally:
        quiz.select_question=original;policy.candidate_select=original_candidate


def copy_bank(dst_dir:Path,src:Path=V63)->Path:
    dst_dir.mkdir(parents=True,exist_ok=True)
    for name in ('questions.jsonl','manifest.json','review_policy_v1.json'):
        shutil.copyfile(src.with_name(name),dst_dir/name)
    return dst_dir/'questions.jsonl'


def test_v63_bank_is_the_pinned_immutable_candidate():
    assert hashlib.sha256(V63.read_bytes()).hexdigest()==V63_SHA
    assert hashlib.sha256(V61.read_bytes()).hexdigest()==V61_SHA
    assert policy.PINNED_REVIEW_BANKS[quiz.V6_3_BANK_VERSION]=={'policy_version':'cy-culture-v6-3-review-v1','bank_sha256':'sha256:'+V63_SHA,
        'policy_sha256':'sha256:'+V63_POLICY_SHA,'manifest_sha256':'sha256:'+V63_MANIFEST_SHA}
    assert policy.PINNED_REVIEW_BANKS[quiz.V6_1_BANK_VERSION]=={'policy_version':'cy-culture-v6-1-review-v1','bank_sha256':'sha256:'+V61_SHA,
        'policy_sha256':'sha256:'+V61_POLICY_SHA,'manifest_sha256':'sha256:'+V61_MANIFEST_SHA}
    for bank,pins in ((V61,(V61_POLICY_SHA,V61_MANIFEST_SHA)),(V63,(V63_POLICY_SHA,V63_MANIFEST_SHA))):
        assert (hashlib.sha256(bank.with_name('review_policy_v1.json').read_bytes()).hexdigest(),
                hashlib.sha256(bank.with_name('manifest.json').read_bytes()).hexdigest())==pins
    qs,entries,suspended=policy.load_candidate(V63,V63.with_name('review_policy_v1.json'))
    assert len(qs)==len(entries)==72 and suspended==set()
    assert Counter(q.quiz_slot for q in qs)=={'evening_general':56,'fx_economy':16}
    assert all(quiz.assemble_payload(q) is not None for q in qs)
    assert (quiz.MAX_QUESTION_CHARS,quiz.MAX_OPTION_CHARS,quiz.MAX_EXPLANATION_CHARS)==(300,100,200)
    for q in qs:
        payload=quiz.assemble_payload(q)
        assert all(len(option)<=100 for option in payload.options),(q.question_id,payload.options)
        assert len(payload.question)<=300 and len(payload.explanation)<=200,q.question_id
        assert payload.options==tuple(quiz._display_option(q,i) for i in range(len(q.options_el)))
    side=json.loads(V63.with_name('review_policy_v1.json').read_text('utf-8'))
    assert side['status']=='candidate_inactive' and side['policy_version']=='cy-culture-v6-3-review-v1'
    old=[json.loads(x) for x in V61.read_text('utf-8').splitlines() if x.strip()]
    new=[json.loads(x) for x in V63.read_text('utf-8').splitlines() if x.strip()]
    assert [r['question_id'] for r in old]==[r['question_id'] for r in new]
    changed={(n['question_id'],k) for o,n in zip(old,new) for k in n if n[k]!=o[k]}
    assert changed==EDITED
    for o,n in zip(old,new):
        assert n['correct_option_index']==o['correct_option_index']
        assert n['options_ru']==o['options_ru'] or n['question_id']=='full-a-001'
        assert n['quiz_slot']==o['quiz_slot'] and n['answer_sources']==o['answer_sources']
    a001=next(r for r in new if r['question_id']=='full-a-001')
    old001=next(r for r in old if r['question_id']=='full-a-001')
    assert a001['options_el'][2]=='Το πολίτευμα είναι προεδρευόμενη κοινοβουλευτική δημοκρατία'
    assert a001['options_ru'][2]=='Кипр — парламентская республика'
    assert a001['options_ru'][:2]+a001['options_ru'][3:]==old001['options_ru'][:2]+old001['options_ru'][3:]
    assert a001['correct_option_index']==old001['correct_option_index']==2
    shown=quiz.assemble_payload(next(q for q in qs if q.question_id=='full-a-001')).options[2]
    assert shown=='Το πολίτευμα είναι προεδρευόμενη κοινοβουλευτική δημοκρατία — Кипр — парламентская республика' and len(shown)<=100
    v61_policy=json.loads(V61.with_name('review_policy_v1.json').read_text('utf-8'))
    assert side['entries']==v61_policy['entries'] and side['suspended_ids']==v61_policy['suspended_ids']


def test_v63_delivers_both_slots_with_receipts_and_idempotency():
    qs,entries,suspended=policy.load_candidate(V63,V63.with_name('review_policy_v1.json'))
    for slot in ('evening_general','fx_economy'):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);sent=[];legacy=[]
            expected=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=DAY,bank_version=quiz.V6_3_BANK_VERSION,receipt_dir=root/'quiz')
            first=simulate(slot,V63,quiz.V6_3_BANK_VERSION,root,sent=sent,legacy=legacy)
            assert first['result']=='quiz_sent' and first['question_id']==expected.question_id
            assert sent==[quiz.assemble_payload(expected)] and not legacy
            receipt=json.loads(quiz.quiz_receipt_path(slot,DAY,root/'quiz').read_text('utf-8'))
            assert receipt['state']=='sent' and receipt['bank_version']==quiz.V6_3_BANK_VERSION
            again=simulate(slot,V63,quiz.V6_3_BANK_VERSION,root,sent=sent,legacy=legacy)
            assert again['result']=='quiz_skipped_receipt_exists' and len(sent)==1


def test_v63_rotation_and_suspensions():
    qs,entries,suspended=policy.load_candidate(V63,V63.with_name('review_policy_v1.json'))
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        for slot,total in (('evening_general',56),('fx_economy',16)):
            seen=[]
            for i in range(total):
                q=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=DAY,bank_version=quiz.V6_3_BANK_VERSION,receipt_dir=root)
                assert q is not None and q.question_id not in seen
                seen.append(q.question_id)
                write_json(quiz.quiz_receipt_path(slot,DAY-timedelta(days=i+1),root),{'state':'sent','quiz_slot':slot,'question_id':q.question_id})
            assert len(seen)==total
        held=policy.candidate_select(qs,entries,{'full-a-024'},slot='evening_general',quiz_date=DAY,bank_version=quiz.V6_3_BANK_VERSION,receipt_dir=root/'s')
        assert held is not None and held.question_id!='full-a-024'
        iban=next(q for q in qs if q.question_id=='full-a-024')
        assert not policy.candidate_eligibility(iban,on=date(2028,10,14),entry=entries[iban.question_id])


def test_review_policy_family_never_uses_legacy_selector():
    for name in ('v2026-10-exam-core-v6','v2026-10-exam-core-v6-1','v2026-10-exam-core-v6-2','v2026-10-exam-core-v6-3',
                 'v2026-10-exam-core-v6-1-hotfix','v2027-01-exam-core-v7','v2026-10-exam-core-V6-2'):
        assert quiz.is_review_policy_bank_name(name),name
    for name in ('v2026-10-exam-core-v5','v2026-10-exam-core-v4','v2026-10-full','v2026-10-pilot',''):
        assert not quiz.is_review_policy_bank_name(name),name


def test_fail_closed_cases_send_nothing_and_reserve_nothing():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        def corrupt(bank:Path):
            lines=bank.read_text('utf-8').splitlines(keepends=True)
            lines[0]=lines[0].replace('"verified":true','"verified":false')
            bank.write_text(''.join(lines),'utf-8')
        def repin(bank:Path):
            # Re-pin manifest and policy to the corrupted bytes: the runtime pin must still refuse it.
            sha='sha256:'+hashlib.sha256(bank.read_bytes()).hexdigest()
            for name,key in (('manifest.json','bank_sha256'),('review_policy_v1.json','source_bank_sha256')):
                p=bank.with_name(name);o=json.loads(p.read_text('utf-8'));o[key]=sha;write_json(p,o)
        def edit(bank:Path,name:str,key:str,value):
            p=bank.with_name(name);o=json.loads(p.read_text('utf-8'));o[key]=value;write_json(p,o)
        cases=[]
        def case(label,version,make,reason):
            cases.append((label,version,make,reason))
        case('v6-4-with-v6-3-bytes',  'v2026-10-exam-core-v6-4',lambda d:copy_bank(d/'v2026-10-exam-core-v6-4'),'culture_review_policy_invalid')
        case('bare-v6',       'v2026-10-exam-core-v6',  lambda d:copy_bank(d/'v2026-10-exam-core-v6'),'culture_bank_version_unsupported')
        case('v6-4-on-v5-path','v2026-10-exam-core-v6-4',lambda d:V5,'culture_review_policy_invalid')
        def superseded_v62(d):
            b=copy_bank(d/V62_VERSION);rows=b.read_text('utf-8').splitlines(keepends=True)
            first=next(i for i,l in enumerate(rows) if json.loads(l)['question_id']=='full-a-001');r=json.loads(rows[first])
            r['options_ru'][2]='Государственный строй — парламентская республика'
            rows[first]=json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n';b.write_text(''.join(rows),'utf-8')
            assert hashlib.sha256(b.read_bytes()).hexdigest()=='39b1763bd4b914d5a1561cee9b1abf7c4df981fda9be479159921c9fcdb022e3'
            return b
        case('superseded-v62-bank',V62_VERSION,superseded_v62,'culture_bank_version_unsupported')
        case('v62-version-on-v63-path',V62_VERSION,lambda d:V63,'culture_bank_version_mismatch')
        case('v63-version-on-v61-path',quiz.V6_3_BANK_VERSION,lambda d:V61,'culture_bank_version_mismatch')
        case('v61-version-on-v63-path',quiz.V6_1_BANK_VERSION,lambda d:V63,'culture_bank_version_mismatch')
        case('v5-version-on-v63-path','v2026-10-exam-core-v5',lambda d:V63,'culture_bank_version_mismatch')
        case('empty-version-on-v63-path','',lambda d:V63,'culture_bank_version_mismatch')
        case('v63-version-on-v5-path',quiz.V6_3_BANK_VERSION,lambda d:V5,'culture_review_policy_invalid')
        case('v63-in-renamed-dir',quiz.V6_3_BANK_VERSION,lambda d:copy_bank(d/'renamed'),'culture_review_policy_invalid')
        case('v63-in-unknown-v6-dir',quiz.V6_3_BANK_VERSION,lambda d:copy_bank(d/'v2026-10-exam-core-v6-9'),'culture_review_policy_invalid')
        def v61_bytes_in_v63_dir(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');shutil.copyfile(V61,b);return b
        case('v61-bank-bytes-in-v63-dir',quiz.V6_3_BANK_VERSION,v61_bytes_in_v63_dir,'culture_review_policy_invalid')
        def v61_policy(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');shutil.copyfile(V61.with_name('review_policy_v1.json'),b.with_name('review_policy_v1.json'));return b
        case('v61-policy-with-v63-bank',quiz.V6_3_BANK_VERSION,v61_policy,'culture_review_policy_invalid')
        def corrupted(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');corrupt(b);return b
        case('corrupted-bank',quiz.V6_3_BANK_VERSION,corrupted,'culture_review_policy_invalid')
        def corrupted_repinned(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');corrupt(b);repin(b);return b
        case('corrupted-bank-repinned',quiz.V6_3_BANK_VERSION,corrupted_repinned,'culture_review_policy_invalid')
        def tampered_answer_repinned(d):
            # Still a fully valid 72-record bank, but with a different correct answer;
            # only the runtime's immutable SHA-256 pin can refuse it.
            b=copy_bank(d/'v2026-10-exam-core-v6-3');rows=b.read_text('utf-8').splitlines(keepends=True)
            first=json.loads(rows[0]);first['correct_option_index']=(first['correct_option_index']+1)%len(first['options_el'])
            rows[0]=json.dumps(first,ensure_ascii=False,separators=(',',':'))+'\n';b.write_text(''.join(rows),'utf-8');repin(b)
            assert len(quiz.load_verified_questions(b))==72
            return b
        case('tampered-answer-repinned',quiz.V6_3_BANK_VERSION,tampered_answer_repinned,'culture_review_policy_invalid')
        def no_manifest(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');b.with_name('manifest.json').unlink();return b
        case('missing-manifest',quiz.V6_3_BANK_VERSION,no_manifest,'culture_review_policy_invalid')
        for label,key,value in (('manifest-foreign-sha','bank_sha256','sha256:'+V61_SHA),
                                ('manifest-wrong-version','bank_version',quiz.V6_1_BANK_VERSION),
                                ('manifest-mutable','immutability',False)):
            case(label,quiz.V6_3_BANK_VERSION,lambda d,k=key,v=value:(lambda b:(edit(b,'manifest.json',k,v),b)[1])(copy_bank(d/'v2026-10-exam-core-v6-3')),'culture_review_policy_invalid')
        for label,key,value in (('policy-foreign-sha','source_bank_sha256','sha256:'+V61_SHA),
                                ('policy-wrong-bank-version','source_bank_version',quiz.V6_1_BANK_VERSION),
                                ('policy-v61-version-label','policy_version','cy-culture-v6-1-review-v1'),
                                ('policy-activated','status','active'),
                                ('policy-unknown-suspension','suspended_ids',['not-in-bank'])):
            case(label,quiz.V6_3_BANK_VERSION,lambda d,k=key,v=value:(lambda b:(edit(b,'review_policy_v1.json',k,v),b)[1])(copy_bank(d/'v2026-10-exam-core-v6-3')),'culture_review_policy_invalid')
        def missing_entry(d):
            b=copy_bank(d/'v2026-10-exam-core-v6-3');p=b.with_name('review_policy_v1.json');o=json.loads(p.read_text('utf-8'));o['entries'].pop();write_json(p,o);return b
        case('policy-missing-entry',quiz.V6_3_BANK_VERSION,missing_entry,'culture_review_policy_invalid')
        assert len(cases)>=20
        for label,version,make,reason in cases:
            work=root/label;bank=make(work);sent=[];legacy=[]
            for slot in ('evening_general','fx_economy'):
                result=simulate(slot,bank,version,work/slot,sent=sent,legacy=legacy)
                assert result['result']=='quiz_skipped_invalid_question' and result['reason']==reason,(label,slot,result)
                assert not (work/slot/'quiz').exists(),(label,slot)
            assert not sent and not legacy,label


def test_pinned_policy_and_manifest_bytes_fail_closed():
    """Policy/manifest content is only trusted at its exact pinned bytes (v6.1 and v6.3)."""
    def rewrite(path:Path,mutate,*,compact=False):
        obj=json.loads(path.read_text('utf-8'));mutate(obj)
        text=json.dumps(obj,ensure_ascii=False,separators=(',',':')) if compact else json.dumps(obj,ensure_ascii=False,indent=2)
        path.write_text(text+'\n','utf-8')
    def dynamic_entry(obj):
        return next(e for e in obj['entries'] if e['max_unverified_until'])
    def extend_expiry(o):dynamic_entry(o)['max_unverified_until']='2099-01-01'
    def move_review(o):
        e=next(e for e in o['entries'] if e['review_after']);e['review_after']=e['max_unverified_until'] or '2099-01-01'
    def suspend(o):o['suspended_ids']=['full-a-024']
    def manifest_relabel(o):
        # Internal bank pin, count, slots and order stay valid; only the file pin can refuse it.
        o['editorial_revision']={'status':'approved_for_production'}
    def bank_tamper_recomputed(b):
        # A different correct answer, with the manifest and policy internal SHA fields recomputed.
        rows=b.read_text('utf-8').splitlines(keepends=True);first=json.loads(rows[0])
        first['correct_option_index']=(first['correct_option_index']+1)%len(first['options_el'])
        rows[0]=json.dumps(first,ensure_ascii=False,separators=(',',':'))+'\n';b.write_text(''.join(rows),'utf-8')
        sha='sha256:'+hashlib.sha256(b.read_bytes()).hexdigest()
        rewrite(b.with_name('manifest.json'),lambda o:o.__setitem__('bank_sha256',sha))
        rewrite(b.with_name('review_policy_v1.json'),lambda o:o.__setitem__('source_bank_sha256',sha))
    cases=[]
    for bank in (V61,V63):
        ver=bank.parent.name;short=ver.rsplit('-',2)[-2]+'-'+ver.rsplit('-',1)[-1]
        P,M='review_policy_v1.json','manifest.json'
        cases+=[
            (f'{short}-policy-expiry-2099',bank,lambda b:rewrite(b.with_name(P),extend_expiry)),
            (f'{short}-policy-review-after-moved',bank,lambda b:rewrite(b.with_name(P),move_review)),
            (f'{short}-policy-suspended-ids-changed',bank,lambda b:rewrite(b.with_name(P),suspend)),
            (f'{short}-policy-same-meaning-compact',bank,lambda b:rewrite(b.with_name(P),lambda o:None,compact=True)),
            (f'{short}-policy-same-meaning-key-order',bank,lambda b:b.with_name(P).write_text(json.dumps(dict(reversed(list(json.loads(b.with_name(P).read_text('utf-8')).items()))),ensure_ascii=False,indent=2)+'\n','utf-8')),
            (f'{short}-policy-trailing-whitespace',bank,lambda b:b.with_name(P).write_bytes(b.with_name(P).read_bytes()+b' ')),
            (f'{short}-policy-missing',bank,lambda b:b.with_name(P).unlink()),
            (f'{short}-policy-corrupted',bank,lambda b:b.with_name(P).write_bytes(b.with_name(P).read_bytes()[:-40])),
            (f'{short}-manifest-relabelled-internal-pins-intact',bank,lambda b:rewrite(b.with_name(M),manifest_relabel)),
            (f'{short}-manifest-same-meaning-compact',bank,lambda b:rewrite(b.with_name(M),lambda o:None,compact=True)),
            (f'{short}-manifest-missing',bank,lambda b:b.with_name(M).unlink()),
            (f'{short}-bank-tampered-internal-shas-recomputed',bank,bank_tamper_recomputed),
            (f'{short}-manifest-corrupted',bank,lambda b:b.with_name(M).write_bytes(b'{"bank_version":')),
        ]
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        for label,src,mutate in cases:
            bank=copy_bank(root/label/src.parent.name,src);mutate(bank)
            raised=False
            try:policy.load_candidate(bank,bank.with_name('review_policy_v1.json'),bank_version=src.parent.name)
            except (ValueError,OSError):raised=True
            assert raised,label
            sent=[];legacy=[]
            for slot in ('evening_general','fx_economy'):
                result=simulate(slot,bank,src.parent.name,root/label/'run'/slot,sent=sent,legacy=legacy)
                assert result['result']=='quiz_skipped_invalid_question' and result['reason']=='culture_review_policy_invalid',(label,slot,result)
                assert not (root/label/'run'/slot/'quiz').exists(),(label,slot)
            assert not sent and not legacy,label
        assert len(cases)==26
        for src in (V61,V63):
            bank=copy_bank(root/'pristine'/src.parent.name,src)
            qs,entries,suspended=policy.load_candidate(bank,bank.with_name('review_policy_v1.json'),bank_version=src.parent.name)
            assert len(qs)==len(entries)==72 and suspended==set()
        # Bank bytes swapped after the pin check but while loading are refused.
        bank=copy_bank(root/'swap'/V63.parent.name)
        original_loader=quiz.load_verified_questions
        def swapping_loader(path):
            questions=original_loader(path);path.write_bytes(V61.read_bytes());return questions
        quiz.load_verified_questions=swapping_loader
        try:
            try:policy.load_candidate(bank,bank.with_name('review_policy_v1.json'),bank_version=V63.parent.name)
            except ValueError as exc:assert 'changed while loading' in str(exc)
            else:raise AssertionError('bank swap during load was accepted')
        finally:quiz.load_verified_questions=original_loader


def test_semantic_checks_still_refuse_a_repinned_bad_release():
    """Defense in depth: even if a bad release were re-pinned, content checks must refuse it."""
    def sha(path:Path)->str:return 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()
    def edit(path:Path,key:str,value):
        obj=json.loads(path.read_text('utf-8'));obj[key]=value;path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n','utf-8')
    def tampered_bank(b):
        rows=b.read_text('utf-8').splitlines(keepends=True);first=json.loads(rows[0])
        first['correct_option_index']=(first['correct_option_index']+1)%len(first['options_el'])
        rows[0]=json.dumps(first,ensure_ascii=False,separators=(',',':'))+'\n';b.write_text(''.join(rows),'utf-8')
        edit(b.with_name('manifest.json'),'bank_sha256',sha(b));edit(b.with_name('review_policy_v1.json'),'source_bank_sha256',sha(b))
    cases=[
        ('policy-activated',lambda b:edit(b.with_name('review_policy_v1.json'),'status','active'),('manifest','policy')),
        ('policy-version-label',lambda b:edit(b.with_name('review_policy_v1.json'),'policy_version','cy-culture-v6-1-review-v1'),('manifest','policy')),
        ('policy-foreign-bank-sha',lambda b:edit(b.with_name('review_policy_v1.json'),'source_bank_sha256','sha256:'+'0'*64),('manifest','policy')),
        ('manifest-foreign-bank-sha',lambda b:edit(b.with_name('manifest.json'),'bank_sha256','sha256:'+'0'*64),('manifest','policy')),
        ('manifest-mutable',lambda b:edit(b.with_name('manifest.json'),'immutability',False),('manifest','policy')),
        ('bank-tampered-only-manifest-and-policy-repinned',tampered_bank,('manifest','policy')),
    ]
    original=dict(policy.PINNED_REVIEW_BANKS[quiz.V6_3_BANK_VERSION])
    with tempfile.TemporaryDirectory() as td:
        for label,mutate,repin in cases:
            bank=copy_bank(Path(td)/label/V63.parent.name);mutate(bank)
            pins=dict(original)
            if 'manifest' in repin:pins['manifest_sha256']=sha(bank.with_name('manifest.json'))
            if 'policy' in repin:pins['policy_sha256']=sha(bank.with_name('review_policy_v1.json'))
            policy.PINNED_REVIEW_BANKS[quiz.V6_3_BANK_VERSION]=pins
            try:
                try:policy.load_candidate(bank,bank.with_name('review_policy_v1.json'),bank_version=V63.parent.name)
                except ValueError:pass
                else:raise AssertionError(f'{label}: re-pinned bad release accepted')
            finally:policy.PINNED_REVIEW_BANKS[quiz.V6_3_BANK_VERSION]=original
    assert policy.PINNED_REVIEW_BANKS[quiz.V6_3_BANK_VERSION]==original


def test_short_and_malformed_versions_never_reach_legacy_selector():
    """Short (v6, v6-3, v7), unknown full, uppercase, padded and mismatched versions fail closed."""
    for name in ('v6','v6-3','v7','V7','v7 ',' v6-3','v7\t','examcorev7','core_v6','v07','v７','v2027-01-exam-core-v7',
                 'v2026-10-exam-core-v6-9','v2026-10-exam-core-V6-3','v2026-10-exam-core-v6-3 '):
        assert quiz.is_review_policy_bank_name(name),name
    for name in ('v2026-10-exam-core-v5','v2026-10-exam-core-v5 ','v2026-10-exam-core-v2','v2026-10-full','v2026-10-pilot',''):
        assert not quiz.is_review_policy_bank_name(name),name
    U,M='culture_bank_version_unsupported','culture_bank_version_mismatch'
    # A2 regression: allowlisted versions must never read a different legacy bank.
    V4=DATA/'v2026-10-exam-core-v4/questions.jsonl'
    # Explicit malformed identifiers must never reach the unpinned legacy selector.
    malformed=('v 7','v-7','v_7','V 6','core-v 7','v2026-10-exam-core-v 7',
               'v2026-10-exam-core-v-7','v+7','version7','Version 6','v.7','v/7','x')
    cases=[(name,V5,U) for name in malformed]+[('v6',V5,U),('v6-3',V5,U),('v7',V5,U),('V7',V5,U),('v7 ',V5,U),(' v6-3',V5,U),('examcorev7',V5,U),
           ('v2027-01-exam-core-v7',V5,U),('v2026-10-exam-core-v6-9',V5,U),
           ('v7',V63,M),('v6-3',V63,M),('V2026-10-EXAM-CORE-V6-3',V63,M),('v2026-10-exam-core-v6-3 ',V63,M),
           ('v2026-10-exam-core-V6-3',V63,M),('v2026-10-exam-core-v6-3',V61,M),('v6',V61,M)]
    cases.extend((('v2026-10-exam-core-v5',V4,U),('v2026-10-exam-core-v4',V5,U)))
    with tempfile.TemporaryDirectory() as td:
        for i,(version,bank,reason) in enumerate(cases):
            sent=[];legacy=[]
            for slot in ('evening_general','fx_economy'):
                root=Path(td)/f'case{i}'/slot
                result=simulate(slot,bank,version,root,sent=sent,legacy=legacy)
                assert result['result']=='quiz_skipped_invalid_question' and result['reason']==reason,(version,bank.parent.name,slot,result)
                assert not (root/'quiz').exists(),(version,slot)
            assert not sent and not legacy,version
    assert len(cases)==31
    assert quiz.LEGACY_BANK_VERSIONS==frozenset({
        'v2026-10-full','v2026-10-pilot',
        'v2026-10-exam-core-v2','v2026-10-exam-core-v3',
        'v2026-10-exam-core-v4','v2026-10-exam-core-v5',
    })


def test_v5_and_v61_paths_are_unchanged():
    for bank,version,via_policy in ((V5,'v2026-10-exam-core-v5',False),(V61,quiz.V6_1_BANK_VERSION,True)):
        for slot in ('evening_general','fx_economy'):
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);sent=[];legacy=[]
                if via_policy:
                    qs,entries,suspended=policy.load_candidate(bank,bank.with_name('review_policy_v1.json'))
                    expected=policy.candidate_select(qs,entries,suspended,slot=slot,quiz_date=DAY,bank_version=version,receipt_dir=root/'quiz')
                else:
                    expected=quiz.select_question(quiz.load_verified_questions(bank),slot=slot,quiz_date=DAY,bank_version=version,receipt_dir=root/'quiz')
                result=simulate(slot,bank,version,root,sent=sent,legacy=legacy)
                assert result['result']=='quiz_sent' and result['question_id']==expected.question_id
                assert bool(legacy)==(not via_policy)
    assert str(quiz.DEFAULT_BANK_PATH).endswith('v2026-10-exam-core-v5/questions.jsonl')
    workflow=(ROOT/'.github/workflows/daily_post.yml').read_text('utf-8')
    assert 'CY_CULTURE_QUIZ_BANK_VERSION: "v2026-10-exam-core-v6-4"' in workflow and 'v6-2' not in workflow and 'v6-3' not in workflow


def main():
    tests=[test_v63_bank_is_the_pinned_immutable_candidate,test_v63_delivers_both_slots_with_receipts_and_idempotency,
           test_v63_rotation_and_suspensions,test_review_policy_family_never_uses_legacy_selector,
           test_fail_closed_cases_send_nothing_and_reserve_nothing,test_pinned_policy_and_manifest_bytes_fail_closed,
           test_semantic_checks_still_refuse_a_repinned_bad_release,
           test_short_and_malformed_versions_never_reach_legacy_selector,test_v5_and_v61_paths_are_unchanged]
    for t in tests:t();print('PASS',t.__name__)
    print(f'PASS {len(tests)}/{len(tests)} v6.3 runtime suites; v5 and v6.1 unchanged; no production activation')

if __name__=='__main__':main()
