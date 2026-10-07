#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authoritative production FX delivery receipts."""
from __future__ import annotations
from datetime import date,datetime,timezone
import json
from pathlib import Path
from typing import Any,Awaitable,Callable
DEFAULT_FX_DELIVERY_DIR=Path(".cache/cy_fx_delivery")
def _utc_now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def fx_delivery_receipt_path(publication_date:date,directory:Path=DEFAULT_FX_DELIVERY_DIR)->Path:return directory/f"{publication_date.isoformat()}.json"
def _write_atomic(path:Path,payload:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n","utf-8");tmp.replace(path)
def valid_fx_delivery_receipt(path:Path,publication_date:date)->bool:
    try:o=json.loads(path.read_text("utf-8"))
    except Exception:return False
    return isinstance(o,dict) and o.get("publication_date")==publication_date.isoformat() and o.get("chat_type")=="production" and isinstance(o.get("telegram_message_id"),int) and o["telegram_message_id"]>0 and bool(str(o.get("sent_at_utc") or ""))
async def send_production_fx_with_receipt(*,send_message:Callable[...,Awaitable[Any]],chat_id:str,production_chat_id:str,to_test:bool,publication_date:date,receipt_dir:Path,text:str,parse_mode:Any,disable_web_page_preview:bool=True,run_id:str="",run_attempt:str="")->Any:
    message=await send_message(chat_id=chat_id,text=text,parse_mode=parse_mode,disable_web_page_preview=disable_web_page_preview)
    mid=getattr(message,"message_id",None)
    if not isinstance(mid,int) or mid<=0:raise RuntimeError("Telegram FX response missing message_id")
    if (not to_test) and str(chat_id).strip() and str(chat_id).strip()==str(production_chat_id).strip():
        _write_atomic(fx_delivery_receipt_path(publication_date,receipt_dir),{"publication_date":publication_date.isoformat(),"chat_type":"production","telegram_message_id":mid,"sent_at_utc":_utc_now(),"run_id":str(run_id or ""),"run_attempt":str(run_attempt or "")})
    return message
