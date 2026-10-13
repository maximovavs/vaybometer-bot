"""Inactive v6.1 candidate: separate editorial review dates from claim validity.

The explicitly selected v6.1 bank uses this pinned review policy.
Active v5 selection remains unchanged; switching production bank requires separate approval.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import cyprus_culture_quiz as quiz

VERSION = "cy-culture-v6-1-review-v1"
ALLOWED_GROUPS = {
    "historical_stable", "stable_monitor", "elected_office", "party_leadership", "civic_service",
    "public_safety_rules", "religious_office", "administrative_rules",
    "appointed_office", "regulatory_threshold", "economic_snapshot",
    "annual_finance", "bounded_statutory_measure",
}


def _parse_day(value:object, *, allow_null:bool=False)->str|None:
    if value is None and allow_null:
        return None
    if not isinstance(value,str):
        raise ValueError("invalid policy date")
    parsed=date.fromisoformat(value)
    if parsed.isoformat()!=value:
        raise ValueError("non-canonical policy date")
    return value


def load_candidate(bank_path:Path, policy_path:Path)->tuple[list[quiz.QuizQuestion],dict[str,dict],set[str]]:
    """Require a complete pinned policy; never silently use the old TTL on error."""
    raw=bank_path.read_bytes()
    source_sha="sha256:"+hashlib.sha256(raw).hexdigest()
    policy=json.loads(policy_path.read_text("utf-8"))
    if not isinstance(policy,dict) or policy.get("schema_version")!=1 or policy.get("policy_version")!=VERSION:
        raise ValueError("unsupported review policy")
    if policy.get("source_bank_version")!="v2026-10-exam-core-v6-1" or policy.get("source_bank_sha256")!=source_sha:
        raise ValueError("review policy source bank mismatch")
    if policy.get("status")!="candidate_inactive":
        raise ValueError("unapproved policy activation")
    questions=quiz.load_verified_questions(bank_path)
    if len(questions)!=72 or len({q.question_id for q in questions})!=72:
        raise ValueError("incomplete verified bank")
    raw_entries=policy.get("entries")
    if not isinstance(raw_entries,list) or len(raw_entries)!=len(questions):
        raise ValueError("incomplete review policy")
    by_id={}
    for entry in raw_entries:
        if not isinstance(entry,dict) or set(entry)!={"question_id","review_group","review_after","max_unverified_until"}:
            raise ValueError("invalid review policy entry")
        key=entry["question_id"]
        if not isinstance(key,str) or key in by_id or entry["review_group"] not in ALLOWED_GROUPS:
            raise ValueError("duplicate/invalid review policy entry")
        _parse_day(entry["review_after"],allow_null=True)
        _parse_day(entry["max_unverified_until"],allow_null=True)
        by_id[key]=entry
    if set(by_id)!={q.question_id for q in questions}:
        raise ValueError("uncovered verified bank question")
    for q in questions:
        entry=by_id[q.question_id]
        until=entry["max_unverified_until"]
        review=entry["review_after"]
        if q.temporal_policy=="stable":
            if until is not None:
                raise ValueError("stable record cannot receive arbitrary expiry")
            if entry["review_group"]=="historical_stable" and review is None:
                pass
            elif entry["review_group"]=="stable_monitor" and review is not None and q.verified_as_of<=review:
                pass
            else:
                raise ValueError("invalid stable review policy")
        else:
            if entry["review_group"] in {"historical_stable", "stable_monitor"} or until is None or review is None:
                raise ValueError("dynamic record missing conservative limits")
            if q.verified_as_of>review or review>until:
                raise ValueError("invalid verification timeline")
            if q.temporal_policy=="bounded":
                if until!=q.valid_until or q.valid_from is None or until<q.valid_from:
                    raise ValueError("bounded legal interval overridden")
            elif q.valid_until is not None:
                raise ValueError("unexpected official expiry")
    suspended=policy.get("suspended_ids")
    if not isinstance(suspended,list) or len(suspended)!=len(set(suspended)) or any(x not in by_id for x in suspended):
        raise ValueError("invalid suspensions")
    return questions, by_id, set(suspended)


def candidate_eligibility(q:quiz.QuizQuestion, *, on:date, entry:dict, suspended:bool=False)->bool:
    if suspended:
        return False
    # Keep all existing *valid_from* and *valid_until* guards by delegating.
    new_upper=entry["max_unverified_until"]
    if q.temporal_policy=="stable":
        return quiz.temporally_eligible(q,on)
    if new_upper is None:
        return False
    return quiz.temporally_eligible(replace(q,revalidate_after=new_upper),on)


def review_due(q:quiz.QuizQuestion,*,on:date,entry:dict)->bool:
    value=entry["review_after"]
    return value is not None and on.isoformat()>value


def candidate_select(questions:list[quiz.QuizQuestion],entries:dict[str,dict],suspended:set[str],*,slot:str,quiz_date:date,bank_version:str,receipt_dir:Path)->quiz.QuizQuestion|None:
    """Use existing deterministic receipt-aware rotation; never bypass its guards."""
    if slot not in quiz.QUIZ_SLOTS or len(entries)!=72 or len(questions)!=72:
        return None
    if set(entries)!={q.question_id for q in questions}:
        return None
    mapped=[]
    for q in questions:
        entry=entries.get(q.question_id)
        if entry is None:
            return None
        if q.question_id in suspended:
            continue
        if not candidate_eligibility(q,on=quiz_date,entry=entry):
            continue
        if q.temporal_policy=="stable":
            mapped.append(q)
        else:
            mapped.append(replace(q,revalidate_after=entry["max_unverified_until"]))
    return quiz.select_question(mapped,slot=slot,quiz_date=quiz_date,bank_version=bank_version,receipt_dir=receipt_dir)
