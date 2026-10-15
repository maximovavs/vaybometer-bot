"""Inactive v6.x candidates: separate editorial review dates from claim validity.

An explicitly selected v6.1 or v6.3 bank uses its exactly pinned review policy.
Active v5 selection remains unchanged; switching production bank requires separate approval.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

import cyprus_culture_quiz as quiz

VERSION = "cy-culture-v6-1-review-v1"
V6_3_VERSION = "cy-culture-v6-3-review-v1"
# Exact immutable byte pins (bank, review policy, manifest) for every review-policy release.
# All three are verified before any policy or manifest content is used.
PINNED_REVIEW_BANKS = {
    quiz.V6_1_BANK_VERSION: {
        "policy_version": VERSION,
        "bank_sha256": "sha256:2574297b91f10341f9e173661913867aeeb8b12d33aa1fa2591774340f28ef99",
        "policy_sha256": "sha256:d96caf7f6179d19c5135597a0b634afb6185b58d028eb947d38fdab5c2f2ea5f",
        "manifest_sha256": "sha256:e35c92a1df7adb02523eecf5042e3610fa3946a0f492532057ff0b825cd7bcc5",
    },
    quiz.V6_3_BANK_VERSION: {
        "policy_version": V6_3_VERSION,
        "bank_sha256": "sha256:67f931b910e77fb4fdd631538a71f3bed2670ccb69718193c4f336f43edc3346",
        "policy_sha256": "sha256:0b20d4b617f8f5a6797eb7e6ed18bfaa748400a43fb250ec234b40a403e887fa",
        "manifest_sha256": "sha256:59c013a5046f47555fe6d9ffe319bca78939703e6c4b50ba9a2f98055927a8a5",
    },
}
EXPECTED_SLOT_COUNTS = {"evening_general": 56, "fx_economy": 16}
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


def _sha256(raw:bytes)->str:
    return "sha256:"+hashlib.sha256(raw).hexdigest()


def _check_manifest(manifest_raw:bytes, bank_version:str, source_sha:str, raw:bytes)->None:
    manifest=json.loads(manifest_raw.decode("utf-8"))
    if not isinstance(manifest,dict) or manifest.get("bank_version")!=bank_version or manifest.get("immutability") is not True:
        raise ValueError("bank manifest mismatch")
    if manifest.get("bank_sha256")!=source_sha or manifest.get("question_count")!=72 or manifest.get("slot_counts")!=EXPECTED_SLOT_COUNTS:
        raise ValueError("bank manifest pin mismatch")
    if manifest.get("ordered_question_ids")!=[json.loads(line)["question_id"] for line in raw.decode("utf-8").splitlines() if line.strip()]:
        raise ValueError("bank manifest order mismatch")


def load_candidate(bank_path:Path, policy_path:Path, *, bank_version:str|None=None)->tuple[list[quiz.QuizQuestion],dict[str,dict],set[str]]:
    """Require a complete pinned policy; never silently use the old TTL on error."""
    expected=bank_path.parent.name if bank_version is None else bank_version
    pin=PINNED_REVIEW_BANKS.get(expected)
    if pin is None or bank_path.parent.name!=expected or bank_path.name!="questions.jsonl":
        raise ValueError("unsupported review-policy bank")
    raw=bank_path.read_bytes()
    manifest_raw=bank_path.with_name("manifest.json").read_bytes()
    policy_raw=policy_path.read_bytes()
    source_sha=_sha256(raw)
    for label,actual,pinned in (("bank",source_sha,pin["bank_sha256"]),
                                ("manifest",_sha256(manifest_raw),pin["manifest_sha256"]),
                                ("review policy",_sha256(policy_raw),pin["policy_sha256"])):
        if actual!=pinned:
            raise ValueError(f"{label} differs from its pinned immutable SHA-256")
    _check_manifest(manifest_raw,expected,source_sha,raw)
    policy=json.loads(policy_raw.decode("utf-8"))
    if not isinstance(policy,dict) or policy.get("schema_version")!=1 or policy.get("policy_version")!=pin["policy_version"]:
        raise ValueError("unsupported review policy")
    if policy.get("source_bank_version")!=expected or policy.get("source_bank_sha256")!=source_sha:
        raise ValueError("review policy source bank mismatch")
    if policy.get("status")!="candidate_inactive":
        raise ValueError("unapproved policy activation")
    questions=quiz.load_verified_questions(bank_path)
    if _sha256(bank_path.read_bytes())!=source_sha:
        raise ValueError("bank changed while loading")
    if len(questions)!=72 or len({q.question_id for q in questions})!=72:
        raise ValueError("incomplete verified bank")
    if Counter(q.quiz_slot for q in questions)!=EXPECTED_SLOT_COUNTS:
        raise ValueError("unexpected slot partition")
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
