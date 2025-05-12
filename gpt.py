#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, random
from openai import OpenAI
from typing import List, Tuple

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

CULPRITS = {
    "туман": {"emoji":"🌁", "tips":[...]},
    "магнитные бури": {"emoji":"🧲","tips":[...]},
    # допиши остальные (жара, сырость, полная луна) с 3–4 советами
}

def gpt_blurb(culprit:str) -> Tuple[str, List[str]]:
    pool = CULPRITS[culprit]["tips"]
    if not OPENAI_KEY:
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(pool,2)
    prompt = ...
    ans = OpenAI(api_key=OPENAI_KEY).chat.completions.create(...)
    # парсим first line + 3 bullets, fallback на random.sample
    return summary, tips
