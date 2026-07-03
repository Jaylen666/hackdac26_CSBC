"""
Shared LLM utilities for csbc3.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

API_KEY = "sk-f59e93f159894ca88aa2fcb7e9d2b749"
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-v4-flash"


def call_llm(prompt: str, user_content: str, max_tokens: int = 4000, retries: int = 5) -> str:
    import time
    last_error = ""
    for attempt in range(retries):
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{BASE_URL}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}/{retries}] {e} — waiting {wait}s...", file=__import__('sys').stderr)
                time.sleep(wait)
    return f"ERROR: {last_error}"


def parse_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found: {text[:300]}")
    return json.loads(m.group(0))
