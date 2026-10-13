#!/usr/bin/env python3
"""Offline, no-network verification for inactive Culture Quiz v6.1 candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cyprus_culture_quiz as quiz

V5 = ROOT / "data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl"
V6 = ROOT / "data/cyprus_culture/v2026-10-exam-core-v6-1/questions.jsonl"
MANIFEST = V6.with_name("manifest.json")
WORKFLOW = ROOT / ".github/workflows/daily_post.yml"
REFRESH_IDS = frozenset({
    "full-a-005",
    "full-a-014",
    "full-a-024",
    "full-a-035",
    "full-a-038",
    "full-a-107",
    "full-b-cur-001",
    "full-b-cur-005",
    "full-b-cur-007",
    "full-b-cur-012",
})



def test_inactive_explanation_only_contract() -> None:
    original = [json.loads(s) for s in V5.read_text("utf-8").splitlines() if s.strip()]
    new_bytes = V6.read_bytes()
    proposed = [json.loads(s) for s in new_bytes.decode("utf-8").splitlines() if s.strip()]
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    assert len(original) == len(proposed) == manifest["question_count"] == 72
    assert manifest["bank_version"] == "v2026-10-exam-core-v6-1"
    assert manifest["immutability"] is True
    assert manifest["bank_sha256"] == "sha256:" + hashlib.sha256(new_bytes).hexdigest()
    assert manifest["source_contract"]["parent_bank_version"] == "v2026-10-exam-core-v5"
    assert [q["question_id"] for q in proposed] == manifest["ordered_question_ids"]
    assert [q["fact_key"] for q in proposed] == manifest["ordered_fact_keys"]
    assert Counter(q["quiz_slot"] for q in proposed) == {"evening_general": 56, "fx_economy": 16}
    explanations = []
    greek_cues = 0
    refreshed = set()
    for before, after in zip(original, proposed):
        assert before["question_id"] == after["question_id"]
        assert before["explanation_ru"] != after["explanation_ru"]
        assert 20 <= len(after["explanation_ru"]) <= quiz.MAX_EXPLANATION_CHARS
        if after["question_id"] in REFRESH_IDS:
            assert (before["verified_as_of"], before["revalidate_after"]) == ("2026-10-06", "2026-10-13")
            assert (after["verified_as_of"], after["revalidate_after"]) == ("2026-10-13", "2026-10-20")
            refreshed.add(after["question_id"])
            excluded = {"explanation_ru", "verified_as_of", "revalidate_after"}
        else:
            excluded = {"explanation_ru"}
        assert {k: v for k, v in before.items() if k not in excluded} == {
            k: v for k, v in after.items() if k not in excluded
        }, f"Unauthorized drift: {before['question_id']}"
        greek_cues += any("\u0370" <= c <= "\u03ff" or "\u1f00" <= c <= "\u1fff" for c in after["explanation_ru"])
        explanations.append(after["explanation_ru"])
    assert refreshed == REFRESH_IDS
    iban = next(q for q in proposed if q["question_id"] == "full-a-024")
    assert iban["question_ru"] == "Что из перечисленного не выдают в Центрах обслуживания граждан?"
    assert iban["options_ru"][iban["correct_option_index"]] == "Справку IBAN"
    assert (iban["verified_as_of"], iban["revalidate_after"]) == ("2026-10-13", "2026-10-20")
    assert len(set(explanations)) == 72
    assert greek_cues == manifest["learning_contract"]["greek_root_cues"] == 30
    questions = quiz.load_verified_questions(V6)
    assert len(questions) == 72
    for q in questions:
        payload = quiz.assemble_payload(q)
        assert payload is not None, q.question_id
        assert payload.correct_option_index == q.correct_option_index
        assert payload.question == f"🇨🇾 {q.question_el}\n🇷🇺 {q.question_ru}"
    assert str(quiz.DEFAULT_BANK_PATH).endswith("v2026-10-exam-core-v5/questions.jsonl")
    workflow = WORKFLOW.read_text("utf-8")
    assert 'CY_CULTURE_QUIZ_BANK_PATH: "data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl"' in workflow
    assert 'CY_CULTURE_QUIZ_BANK_VERSION: "v2026-10-exam-core-v5"' in workflow


if __name__ == "__main__":
    test_inactive_explanation_only_contract()
    print("PASS: 72 v6.1 explanation-only rows, 30 Greek-root cues, immutable v5 and inactive deployment")
