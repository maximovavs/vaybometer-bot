#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, slot-scoped Cyprus Culture Telegram quiz delivery."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

TZ_NAME="Asia/Nicosia"
DEFAULT_BANK_PATH=Path("data/cyprus_culture/v2026-10-exam-core-v3/questions.jsonl")
DEFAULT_QUIZ_RECEIPT_DIR=Path(".cache/cy_quiz_delivery")
DEFAULT_WEATHER_TEXT_RECEIPT_DIR=Path(".cache/cy_text_delivery")
DEFAULT_FX_DELIVERY_DIR=Path(".cache/cy_fx_delivery")
QUIZ_SLOTS={"evening_general","fx_economy"}
EVENING_SCHEDULES={"0 13 * * *","45 13 * * *","15 15 * * *"}
FX_SCHEDULES={"0 7 * * *"}
SUPPORTED_TEMPORAL_POLICIES={"stable","bounded","open_ended","snapshot"}
QUESTION_ID_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
FACT_KEY_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
HASH_RE=re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_QUESTION_CHARS=300;MAX_OPTION_CHARS=100;MAX_EXPLANATION_CHARS=200

@dataclass(frozen=True)
class QuizQuestion:
    question_id:str;rotation_rank:int;fact_key:str;source_tier:str;quiz_slot:str;category:str
    question_el:str;question_ru:str;options_el:tuple[str,...];options_ru:tuple[str,...];correct_option_index:int
    explanation_ru:str;source:str;verified:bool;provenance_tier:str;source_locator:str;answer_source:str
    answer_sources:tuple[str,...];rights_status:str;publication_mode:str;source_text_hash:str;review_status:str
    temporal_policy:str;valid_from:str|None;valid_until:str|None;reference_date:str|None;reference_period:str|None
    verified_as_of:str;revalidate_after:str|None

@dataclass(frozen=True)
class QuizPayload:
    question:str;options:tuple[str,...];correct_option_index:int;explanation:str

def _env_on(name:str,default:bool=False)->bool:
    raw=os.getenv(name);return default if raw is None else raw.strip().lower() in {"1","true","yes","on"}
def _utc_now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def _valid_date(v:object)->bool:
    try:date.fromisoformat(str(v or ""))
    except ValueError:return False
    return True
def _norm(v:str)->str:return " ".join(str(v or "").split()).casefold()
def _atomic(path:Path,payload:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n","utf-8");tmp.replace(path)
def _reserve(path:Path,payload:dict[str,Any])->bool:
    path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:return False
    with os.fdopen(fd,"w",encoding="utf-8") as h:
        json.dump(payload,h,ensure_ascii=False,indent=2,sort_keys=True);h.write("\n");h.flush();os.fsync(h.fileno())
    return True
def _opt_date(v:object)->str|None:
    if v is None:return None
    s=str(v).strip()
    if not _valid_date(s):raise ValueError("invalid date")
    return s

def _parse_record(r:Any)->QuizQuestion|None:
    if not isinstance(r,dict) or r.get("verified") is not True:return None
    required=("question_id","rotation_rank","fact_key","source_tier","quiz_slot","category","question_el","question_ru","options_el","options_ru","correct_option_index","source","provenance_tier","source_locator","answer_source","answer_sources","rights_status","publication_mode","source_text_hash","review_status","temporal_policy","valid_from","valid_until","reference_date","reference_period","verified_as_of","revalidate_after")
    if any(k not in r for k in required):return None
    try:
        qid=str(r["question_id"]).strip();fk=str(r["fact_key"]).strip();rank=r["rotation_rank"];slot=str(r["quiz_slot"]);tier=str(r["source_tier"])
        qel=str(r["question_el"]).strip();qru=str(r["question_ru"]).strip();cat=str(r["category"]).strip()
        els=tuple(str(x).strip() for x in r["options_el"]);rus=tuple(str(x).strip() for x in r["options_ru"]);correct=r["correct_option_index"]
        vf=_opt_date(r["valid_from"]);vu=_opt_date(r["valid_until"]);rd=_opt_date(r["reference_date"]);ra=_opt_date(r["revalidate_after"])
    except Exception:return None
    if not QUESTION_ID_RE.fullmatch(qid) or not FACT_KEY_RE.fullmatch(fk) or not isinstance(rank,int) or isinstance(rank,bool) or rank<0:return None
    if slot not in QUIZ_SLOTS or tier not in {"A","B"} or not cat or not qel or not qru:return None
    if len(els) not in {3,4} or len(els)!=len(rus) or any(not x for x in els+rus):return None
    if len({_norm(x) for x in els})!=len(els) or len({_norm(x) for x in rus})!=len(rus):return None
    if not isinstance(correct,int) or isinstance(correct,bool) or not 0<=correct<len(els):return None
    if r["rights_status"]!="official_derived_rewrite" or r["publication_mode"]!="official_derived_rewrite" or r["review_status"]!="verified_official":return None
    if "libfile_" in str(r["source_locator"]) or not HASH_RE.fullmatch(str(r["source_text_hash"])):return None
    sources=r["answer_sources"];ans=str(r["answer_source"])
    if not ans.startswith("https://") or not isinstance(sources,list) or not sources or any(not str(x).startswith("https://") for x in sources):return None
    if not re.fullmatch(r"P[1-3]",str(r["provenance_tier"])) or not _valid_date(r["verified_as_of"]):return None
    p=str(r["temporal_policy"])
    if p not in SUPPORTED_TEMPORAL_POLICIES:return None
    if p=="stable" and (vf is not None or vu is not None or ra is not None):return None
    if p=="bounded" and (vf is None or vu is None or ra is None or vf>vu):return None
    if p=="open_ended" and (vu is not None or ra is None):return None
    if p=="snapshot" and (vf is not None or vu is not None or ra is None):return None
    rp=None if r["reference_period"] is None else str(r["reference_period"]).strip()
    return QuizQuestion(qid,rank,fk,tier,slot,cat,qel,qru,els,rus,correct,str(r.get("explanation_ru") or ""),str(r["source"]),True,str(r["provenance_tier"]),str(r["source_locator"]),ans,tuple(map(str,sources)),str(r["rights_status"]),str(r["publication_mode"]),str(r["source_text_hash"]),str(r["review_status"]),p,vf,vu,rd,rp,str(r["verified_as_of"]),ra)

def load_verified_questions(path:Path)->list[QuizQuestion]:
    if not path.is_file():return []
    parsed=[]
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():continue
        try:r=json.loads(line)
        except json.JSONDecodeError:continue
        q=_parse_record(r)
        if q:parsed.append(q)
    ids=Counter(q.question_id for q in parsed);ranks=Counter(q.rotation_rank for q in parsed);facts=Counter(q.fact_key for q in parsed)
    return sorted((q for q in parsed if ids[q.question_id]==ranks[q.rotation_rank]==facts[q.fact_key]==1),key=lambda q:q.rotation_rank)

def temporally_eligible(q:QuizQuestion,quiz_date:date)->bool:
    ds=quiz_date.isoformat()
    if q.temporal_policy=="stable":return True
    if q.temporal_policy=="bounded":return bool(q.valid_from<=ds<=q.valid_until and ds<=q.revalidate_after)
    if q.temporal_policy=="open_ended":return (q.valid_from is None or q.valid_from<=ds) and ds<=q.revalidate_after
    if q.temporal_policy=="snapshot":return ds<=q.revalidate_after
    return False
def quiz_receipt_path(slot:str,quiz_date:date,directory:Path)->Path:
    if slot not in QUIZ_SLOTS:raise ValueError("unsupported quiz slot")
    return directory/slot/f"{quiz_date.isoformat()}.json"
def _sent_history(slot:str,directory:Path,before:date,eligible:set[str])->set[str]:
    d=directory/slot
    if not d.is_dir():return set()
    used=[]
    for path in sorted(d.glob("*.json"),reverse=True):
        try:day=date.fromisoformat(path.stem)
        except ValueError:continue
        if day>=before:continue
        try:o=json.loads(path.read_text("utf-8"))
        except Exception:continue
        if o.get("state")!="sent" or o.get("quiz_slot")!=slot:continue
        qid=str(o.get("question_id") or "")
        if qid not in eligible:continue
        if qid in used:break
        used.append(qid)
        if len(used)>=len(eligible):break
    return set() if len(used)>=len(eligible) else set(used)
def select_question(questions:list[QuizQuestion],*,slot:str,quiz_date:date,bank_version:str,receipt_dir:Path)->QuizQuestion|None:
    eligible=[q for q in questions if q.quiz_slot==slot and temporally_eligible(q,quiz_date)]
    if not eligible:return None
    ids={q.question_id for q in eligible};used=_sent_history(slot,receipt_dir,quiz_date,ids);remaining=[q for q in eligible if q.question_id not in used] or eligible
    seed=int(hashlib.sha256(f"{bank_version}|{slot}|{quiz_date.isoformat()}".encode()).hexdigest()[:16],16)
    return remaining[seed%len(remaining)]
def assemble_payload(q:QuizQuestion)->QuizPayload|None:
    question=f"🇨🇾 Ερώτηση για την Κύπρο\n{q.question_el}\n🇷🇺 {q.question_ru}";opts=tuple(f"{a} — {b}" for a,b in zip(q.options_el,q.options_ru))
    if len(question)>MAX_QUESTION_CHARS or any(len(x)>MAX_OPTION_CHARS for x in opts) or len(q.explanation_ru)>MAX_EXPLANATION_CHARS:return None
    return QuizPayload(question,opts,q.correct_option_index,q.explanation_ru)
def _weather_path(target:date,d:Path)->Path:return d/f"{target.isoformat()}-evening.json"
def valid_weather_text_receipt(path:Path,target:date)->bool:
    try:o=json.loads(path.read_text("utf-8"))
    except Exception:return False
    ids=o.get("telegram_message_ids")
    return isinstance(o,dict) and o.get("target_date")==target.isoformat() and o.get("post_type")=="evening" and o.get("chat_type")=="production" and isinstance(o.get("text_chunk_count"),int) and o["text_chunk_count"]>0 and isinstance(ids,list) and len([x for x in ids if isinstance(x,int) and x>0])>=o["text_chunk_count"] and bool(str(o.get("sent_at_utc") or ""))
def _fx_path(day:date,d:Path)->Path:return d/f"{day.isoformat()}.json"
def valid_fx_delivery_receipt(path:Path,day:date)->bool:
    try:o=json.loads(path.read_text("utf-8"))
    except Exception:return False
    return isinstance(o,dict) and o.get("publication_date")==day.isoformat() and o.get("chat_type")=="production" and isinstance(o.get("telegram_message_id"),int) and o["telegram_message_id"]>0 and bool(str(o.get("sent_at_utc") or ""))
def is_natural_production_schedule(slot:str,event_name:str,event_schedule:str)->bool:
    allowed=EVENING_SCHEDULES if slot=="evening_general" else FX_SCHEDULES if slot=="fx_economy" else set()
    return event_name=="schedule" and event_schedule in allowed
async def _default_send_poll(*,token:str,chat_id:str,payload:QuizPayload)->Any:
    from telegram import Bot
    return await Bot(token=token).send_poll(chat_id=chat_id,question=payload.question,options=list(payload.options),type="quiz",correct_option_id=payload.correct_option_index,is_anonymous=True,explanation=payload.explanation or None)

async def deliver_quiz(*,slot:str,enabled:bool,event_name:str,event_schedule:str,chat_id:str,token:str,bank_path:Path,bank_version:str,anchor_date_text:str,weather_receipt_dir:Path,fx_receipt_dir:Path,quiz_receipt_dir:Path,now:datetime|None=None,send_poll:Callable[...,Awaitable[Any]]=_default_send_poll,run_id:str="",run_attempt:str="")->dict[str,Any]:
    if slot not in QUIZ_SLOTS:return {"result":"quiz_failed_non_fatal","reason":"unsupported_slot"}
    if not enabled:return {"result":"quiz_skipped_disabled","quiz_slot":slot}
    if not is_natural_production_schedule(slot,event_name,event_schedule):return {"result":"quiz_skipped_non_production","quiz_slot":slot}
    if not chat_id or not token:return {"result":"quiz_failed_non_fatal","reason":"telegram_config_missing","quiz_slot":slot}
    try:anchor=date.fromisoformat(str(anchor_date_text or ""))
    except ValueError:return {"result":"quiz_skipped_invalid_question","reason":"invalid_anchor_date","quiz_slot":slot}
    local=now or datetime.now(ZoneInfo(TZ_NAME));local=local.replace(tzinfo=ZoneInfo(TZ_NAME)) if local.tzinfo is None else local.astimezone(ZoneInfo(TZ_NAME));qdate=local.date()
    if qdate<anchor:return {"result":"quiz_skipped_invalid_question","reason":"rotation_not_started","quiz_slot":slot}
    if slot=="evening_general":
        target=qdate+timedelta(days=1)
        if not valid_weather_text_receipt(_weather_path(target,weather_receipt_dir),target):return {"result":"quiz_skipped_weather_not_delivered","quiz_slot":slot,"quiz_date":qdate.isoformat()}
    elif not valid_fx_delivery_receipt(_fx_path(qdate,fx_receipt_dir),qdate):
        return {"result":"quiz_skipped_fx_not_delivered","quiz_slot":slot,"quiz_date":qdate.isoformat()}
    qs=load_verified_questions(bank_path);selected=select_question(qs,slot=slot,quiz_date=qdate,bank_version=bank_version,receipt_dir=quiz_receipt_dir)
    if selected is None:return {"result":"quiz_skipped_no_eligible_questions","quiz_slot":slot,"quiz_date":qdate.isoformat()}
    payload=assemble_payload(selected)
    if payload is None:return {"result":"quiz_skipped_invalid_question","reason":"telegram_payload_limits","quiz_slot":slot,"question_id":selected.question_id}
    path=quiz_receipt_path(slot,qdate,quiz_receipt_dir)
    if path.exists():return {"result":"quiz_skipped_receipt_exists","quiz_slot":slot,"question_id":selected.question_id,"receipt_path":str(path)}
    reservation={"quiz_slot":slot,"quiz_date":qdate.isoformat(),"chat_type":"production","question_id":selected.question_id,"bank_version":bank_version,"state":"reserved","run_id":str(run_id or ""),"run_attempt":str(run_attempt or ""),"reserved_at_utc":_utc_now()}
    if slot=="evening_general":reservation["weather_target_date"]=(qdate+timedelta(days=1)).isoformat()
    else:reservation["publication_date"]=qdate.isoformat()
    if not _reserve(path,reservation):return {"result":"quiz_skipped_receipt_exists","quiz_slot":slot,"question_id":selected.question_id,"receipt_path":str(path)}
    try:
        msg=await send_poll(token=token,chat_id=chat_id,payload=payload);mid=getattr(msg,"message_id",None);poll=getattr(msg,"poll",None);pid=str(getattr(poll,"id","") or "").strip()
        if not isinstance(mid,int) or mid<=0 or not pid:raise RuntimeError("Telegram quiz response missing message_id/poll_id")
        sent=dict(reservation);sent.update({"state":"sent","telegram_message_id":mid,"poll_id":pid,"sent_at_utc":_utc_now()});_atomic(path,sent)
        return {"result":"quiz_sent","quiz_slot":slot,"question_id":selected.question_id,"telegram_message_id":mid,"poll_id":pid,"receipt_path":str(path)}
    except Exception as exc:return {"result":"quiz_failed_non_fatal","reason":exc.__class__.__name__,"quiz_slot":slot,"question_id":selected.question_id,"receipt_path":str(path)}

async def run_from_environment(slot:str)->dict[str,Any]:
    flag="CY_CULTURE_QUIZ_EVENING_ENABLED" if slot=="evening_general" else "CY_CULTURE_QUIZ_FX_ENABLED"
    return await deliver_quiz(slot=slot,enabled=_env_on(flag,False),event_name=os.getenv("GITHUB_EVENT_NAME",""),event_schedule=os.getenv("GITHUB_EVENT_SCHEDULE",""),chat_id=(os.getenv("CHANNEL_ID") or "").strip(),token=(os.getenv("TELEGRAM_TOKEN") or "").strip(),bank_path=Path(os.getenv("CY_CULTURE_QUIZ_BANK_PATH",str(DEFAULT_BANK_PATH))),bank_version=os.getenv("CY_CULTURE_QUIZ_BANK_VERSION",""),anchor_date_text=os.getenv("CY_CULTURE_QUIZ_ANCHOR_DATE",""),weather_receipt_dir=Path(os.getenv("CY_TEXT_DELIVERY_DIR",str(DEFAULT_WEATHER_TEXT_RECEIPT_DIR))),fx_receipt_dir=Path(os.getenv("CY_FX_DELIVERY_DIR",str(DEFAULT_FX_DELIVERY_DIR))),quiz_receipt_dir=Path(os.getenv("CY_QUIZ_DELIVERY_DIR",str(DEFAULT_QUIZ_RECEIPT_DIR))),run_id=os.getenv("GITHUB_RUN_ID",""),run_attempt=os.getenv("GITHUB_RUN_ATTEMPT",""))
def main()->int:
    ap=argparse.ArgumentParser(description="Cyprus Culture Telegram quiz");ap.add_argument("--slot",required=True,choices=sorted(QUIZ_SLOTS));args=ap.parse_args()
    try:result=asyncio.run(run_from_environment(args.slot))
    except BaseException as exc:result={"result":"quiz_failed_non_fatal","reason":exc.__class__.__name__,"quiz_slot":args.slot}
    print("CY_CULTURE_QUIZ_RESULT="+json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
