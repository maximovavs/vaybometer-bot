#!/usr/bin/env python3
from __future__ import annotations
import asyncio,hashlib,json,re,tempfile,sys
from collections import Counter
from datetime import date,datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import cyprus_culture_quiz as quiz
import cyprus_fx_delivery as fxdel
BANK=ROOT/"data/cyprus_culture/v2026-10-exam-core-v4/questions.jsonl";MAN=ROOT/"data/cyprus_culture/v2026-10-exam-core-v4/manifest.json";V3_BANK=ROOT/"data/cyprus_culture/v2026-10-exam-core-v3/questions.jsonl";V3_MAN=ROOT/"data/cyprus_culture/v2026-10-exam-core-v3/manifest.json"
def check(c,m):
    if not c:raise AssertionError(m)
def write_json(p,o):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,ensure_ascii=False)+"\n","utf-8")
def weather(p,d):write_json(p,{"target_date":d.isoformat(),"post_type":"evening","chat_type":"production","text_chunk_count":1,"telegram_message_ids":[1],"sent_at_utc":"2026-10-07T13:01:00Z"})
def fx(p,d):write_json(p,{"publication_date":d.isoformat(),"chat_type":"production","telegram_message_id":2,"sent_at_utc":"2026-10-07T07:01:00Z"})
def test_bank_contract():
    raw=BANK.read_bytes();m=json.loads(MAN.read_text("utf-8"));qs=quiz.load_verified_questions(BANK)
    check(len(qs)==72 and m["question_count"]==72,"count");check(m["bank_sha256"]=="sha256:"+hashlib.sha256(raw).hexdigest(),"sha")
    check(Counter(q.source_tier for q in qs)=={"A":62,"B":10},"tiers");check(len({q.fact_key for q in qs})==72 and len({q.rotation_rank for q in qs})==72,"unique")
    check({q.rights_status for q in qs}=={"official_derived_rewrite"} and {q.review_status for q in qs}=={"verified_official"},"rights/review")
    check(all("libfile_" not in q.source_locator for q in qs) and m["source_contract"]["official_answer_sources_required"] is True,"private/source")
    check(Counter(q.temporal_policy for q in qs)=={"stable":50,"open_ended":17,"bounded":2,"snapshot":3},"temporal")
    a21=next(q for q in qs if q.question_id=="full-a-021")
    check(a21.temporal_policy=="stable" and a21.reference_date=="2026-05-24","A21")
    cyr=re.compile(r"[А-Яа-яЁё]");greek=re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
    check(all("VayboMeter" not in q.question_el and "VayboMeter" not in q.question_ru for q in qs),"no VayboMeter prefix")
    check(all(not cyr.search(q.question_el) and all(not cyr.search(x) for x in q.options_el) for q in qs),"Greek purity")
    check(all(all(not greek.search(x) for x in q.options_ru) for q in qs),"Russian option purity")
    check(all(all("…" not in x for x in q.options_el+q.options_ru) for q in qs),"no truncated options")
    check(all(q.quiz_slot=="fx_economy" for q in qs if q.category=="economy"),"economy only FX")
    check(m["exam_reference_counts"]=={"hallouminati":47,"official_core":25},"exam refs")
    new_ids={"exam-v3-hall-a05","exam-v3-hall-a17","exam-v3-hall-a18","exam-v3-hall-b01","exam-v3-hall-b15","exam-v3-hall-b17","exam-v3-hall-b21","exam-v3-hall-b22"}
    check(new_ids<={x.question_id for x in qs},"v3 Hallouminati expansion")
def test_v4_editorial_identity_and_payload():
    v3=[json.loads(x) for x in V3_BANK.read_text("utf-8").splitlines() if x.strip()];v4=[json.loads(x) for x in BANK.read_text("utf-8").splitlines() if x.strip()]
    check([x["question_id"] for x in v4]==[x["question_id"] for x in v3],"rotation order preserved")
    changed={"full-a-002","full-a-020","full-a-026","full-a-034","exam-v3-hall-a17","full-b-eco-001","full-b-ins-020"}
    by3={x["question_id"]:x for x in v3};by4={x["question_id"]:x for x in v4};actual=set()
    for qid in by4:
        a=dict(by3[qid]);b=dict(by4[qid]);ae=a.pop("question_el");ar=a.pop("question_ru");be=b.pop("question_el");br=b.pop("question_ru")
        check(a==b,f"non-editorial drift: {qid}")
        if (ae,ar)!=(be,br):actual.add(qid)
    check(actual==changed,"exact editorial question scope")
    m=json.loads(MAN.read_text("utf-8"));check(m["bank_version"]=="v2026-10-exam-core-v4","v4 version");check(m["source_contract"]["parent_bank_version"]=="v2026-10-exam-core-v3","v3 parent")
    qs=quiz.load_verified_questions(BANK);check(all(quiz.assemble_payload(q) is not None for q in qs),"payload limits")
    q=next(x for x in qs if x.question_id=="full-a-020");p=quiz.assemble_payload(q);check(p.question.splitlines()[0]==q.question_el,"question starts directly");check("Ερώτηση για την Κύπρο" not in p.question,"generic Greek header removed")
def test_temporal():
    qs=quiz.load_verified_questions(BANK);b=next(q for q in qs if q.question_id=="full-b-eco-010")
    check(not quiz.temporally_eligible(b,date(2026,4,30)) and quiz.temporally_eligible(b,date(2026,5,1)),"bounded start")
    check(not quiz.temporally_eligible(b,date(2026,11,6)),"bounded freshness")
    o=next(q for q in qs if q.temporal_policy=="open_ended" and q.revalidate_after=="2026-10-13")
    check(quiz.temporally_eligible(o,date(2026,10,13)) and not quiz.temporally_eligible(o,date(2026,10,14)),"open ttl")
    s=next(q for q in qs if q.temporal_policy=="snapshot");check(s.valid_from is None and s.valid_until is None and not quiz.temporally_eligible(s,date(2026,10,14)),"snapshot")
def test_rotation_full_cycle_and_slots():
    qs=quiz.load_verified_questions(BANK);g={q.question_id for q in qs if q.quiz_slot=="evening_general"};f={q.question_id for q in qs if q.quiz_slot=="fx_economy"}
    check(len(g)==56 and len(f)==16 and not(g&f),"slots")
    with tempfile.TemporaryDirectory() as td:
        d=Path(td);day=date(2026,10,7);eligible=[q for q in qs if q.quiz_slot=="fx_economy" and quiz.temporally_eligible(q,day)]
        seen=[]
        for i in range(len(eligible)):
            q=quiz.select_question(qs,slot="fx_economy",quiz_date=day,bank_version="v2026-10-exam-core-v4",receipt_dir=d);check(q and q.question_id not in seen,"repeat before pool exhausted");seen.append(q.question_id)
            pd=day-timedelta(days=i+1);write_json(quiz.quiz_receipt_path("fx_economy",pd,d),{"state":"sent","quiz_slot":"fx_economy","question_id":q.question_id})
        check(len(seen)==len(eligible),"full cycle")
        e=Path(td)/"empty";q1=quiz.select_question(qs,slot="evening_general",quiz_date=day,bank_version="v2026-10-exam-core-v4",receipt_dir=e);q2=quiz.select_question(qs,slot="evening_general",quiz_date=day,bank_version="v2026-10-exam-core-v4",receipt_dir=e);check(q1.question_id==q2.question_id,"deterministic")
def test_receipts_dependencies_and_nonfatal():
    async def go():
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);qd=root/"q";wd=root/"w";fd=root/"fx";now=datetime(2026,10,7,16,0,tzinfo=ZoneInfo("Asia/Nicosia"));weather(wd/"2026-10-08-evening.json",date(2026,10,8));fx(fd/"2026-10-07.json",date(2026,10,7))
            async def send(**kw):return SimpleNamespace(message_id=77,poll=SimpleNamespace(id="p77"))
            common=dict(chat_id="1",token="x",bank_path=BANK,bank_version="v2026-10-exam-core-v4",anchor_date_text="2026-10-07",weather_receipt_dir=wd,fx_receipt_dir=fd,now=now,send_poll=send)
            r1=await quiz.deliver_quiz(slot="evening_general",enabled=True,event_name="schedule",event_schedule="0 13 * * *",quiz_receipt_dir=qd,**common);check(r1["result"]=="quiz_sent","evening")
            r2=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="schedule",event_schedule="0 7 * * *",quiz_receipt_dir=qd,**common);check(r2["result"]=="quiz_sent","fx")
            check(quiz.quiz_receipt_path("evening_general",date(2026,10,7),qd).exists() and quiz.quiz_receipt_path("fx_economy",date(2026,10,7),qd).exists(),"coexist")
            r3=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="schedule",event_schedule="0 7 * * *",quiz_receipt_dir=qd,**common);check(r3["result"]=="quiz_skipped_receipt_exists","dup")
            r4=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="workflow_dispatch",event_schedule="0 7 * * *",quiz_receipt_dir=root/"manual",**common);check(r4["result"]=="quiz_skipped_non_production","manual")
            bad=dict(common);bad["fx_receipt_dir"]=root/"missing";r5=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="schedule",event_schedule="0 7 * * *",quiz_receipt_dir=root/"miss",**bad);check(r5["result"]=="quiz_skipped_fx_not_delivered","fx prereq")
            bad2=dict(common);bad2["weather_receipt_dir"]=root/"missingw";r6=await quiz.deliver_quiz(slot="evening_general",enabled=True,event_name="schedule",event_schedule="0 13 * * *",quiz_receipt_dir=root/"missw",**bad2);check(r6["result"]=="quiz_skipped_weather_not_delivered","weather prereq")
            async def fail(**kw):raise RuntimeError("boom")
            bad3=dict(common);bad3["send_poll"]=fail;q5=root/"amb";r7=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="schedule",event_schedule="0 7 * * *",quiz_receipt_dir=q5,**bad3);check(r7["result"]=="quiz_failed_non_fatal","nonfatal")
            r8=await quiz.deliver_quiz(slot="fx_economy",enabled=True,event_name="schedule",event_schedule="0 7 * * *",quiz_receipt_dir=q5,**common);check(r8["result"]=="quiz_skipped_receipt_exists","reservation failclosed")
    asyncio.run(go())
def test_fx_authoritative_receipt():
    async def go():
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            async def send(**kw):return SimpleNamespace(message_id=91)
            await fxdel.send_production_fx_with_receipt(send_message=send,chat_id="prod",production_chat_id="prod",to_test=False,publication_date=date(2026,10,7),receipt_dir=d,text="x",parse_mode="HTML")
            p=fxdel.fx_delivery_receipt_path(date(2026,10,7),d);check(fxdel.valid_fx_delivery_receipt(p,date(2026,10,7)),"prod receipt")
            d2=d/"test";await fxdel.send_production_fx_with_receipt(send_message=send,chat_id="test",production_chat_id="prod",to_test=True,publication_date=date(2026,10,7),receipt_dir=d2,text="x",parse_mode="HTML");check(not fxdel.fx_delivery_receipt_path(date(2026,10,7),d2).exists(),"test receipt")
            d3=d/"fail"
            async def fail(**kw):raise RuntimeError("send failed")
            try:await fxdel.send_production_fx_with_receipt(send_message=fail,chat_id="prod",production_chat_id="prod",to_test=False,publication_date=date(2026,10,7),receipt_dir=d3,text="x",parse_mode="HTML")
            except RuntimeError:pass
            check(not fxdel.fx_delivery_receipt_path(date(2026,10,7),d3).exists(),"failed receipt")
    asyncio.run(go())
def main():
    for f in [test_bank_contract,test_v4_editorial_identity_and_payload,test_temporal,test_rotation_full_cycle_and_slots,test_receipts_dependencies_and_nonfatal,test_fx_authoritative_receipt]:f()
    print("OK: 6 Cyprus Culture/FX dual-slot offline checks passed")
if __name__=="__main__":main()
