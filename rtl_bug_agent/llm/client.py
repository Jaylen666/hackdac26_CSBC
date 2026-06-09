from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    base_url: str
    model: str
    timeout_s: int = 90
    thinking: str | None = None  # "low" | "medium" | "high" | None


class OpenAICompatibleClient:
    def __init__(self, config: LlmConfig):
        self.config = config
        self.call_count: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_tokens: int = 0
        self._error_count: int = 0
        self._total_wall_s: float = 0.0

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> str:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Pass thinking strength if configured.
        # DeepSeek expects an object: {"type": "enabled"}
        # OpenAI-compatible providers accept a string: "low" | "medium" | "high"
        if self.config.thinking:
            tv = self.config.thinking
            try:
                payload["thinking"] = json.loads(tv)  # parse JSON object
            except (json.JSONDecodeError, TypeError):
                payload["thinking"] = tv  # plain string

        t0 = time.monotonic()
        body = self._post_json_with_retries(self._chat_url(), payload)
        elapsed = time.monotonic() - t0

        parsed = json.loads(body)
        try:
            msg = parsed["choices"][0]["message"]
            content = msg.get("content", "")
            # DeepSeek sometimes puts the answer in reasoning_content
            # and leaves content as an empty string.
            if not content:
                content = msg.get("reasoning_content", "")
        except (KeyError, IndexError, TypeError) as exc:
            self._error_count += 1
            raise RuntimeError(
                f"Unexpected LLM response shape: {body[:1200]}"
            ) from exc

        self.call_count += 1
        self._total_wall_s += elapsed
        usage = parsed.get("usage", {})
        if usage:
            inp = usage.get("prompt_tokens", 0)
            out = usage.get("completion_tokens", 0)
            self.total_input_tokens += inp
            self.total_output_tokens += out
            self.total_tokens += usage.get("total_tokens", inp + out)
        else:
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            self.total_input_tokens += prompt_chars // 3
            self.total_output_tokens += len(content) // 3
            self.total_tokens += prompt_chars // 3 + len(content) // 3

        return content

    def stats(self) -> dict:
        return {
            "call_count": self.call_count,
            "error_count": self._error_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_wall_seconds": round(self._total_wall_s, 1),
        }

    def print_stats(self) -> None:
        s = self.stats()
        print(f"  LLM calls:  {s['call_count']} ({s['error_count']} errors)")
        print(f"  Tokens:     {s['total_tokens']:,} total "
              f"({s['total_input_tokens']:,} in / {s['total_output_tokens']:,} out)")
        print(f"  Wall time:  {s['total_wall_seconds']:.0f}s "
              f"({s['total_wall_seconds'] / 60:.1f} min)")

    def _chat_url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _post_json_with_retries(
        self, url: str, payload: dict, attempts: int = 3
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "rtl-bug-agent/0.1",
        }
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._post_json_requests(url, payload, headers)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code < 500 or attempt == attempts:
                    self._error_count += 1
                    raise RuntimeError(
                        f"LLM HTTP {exc.code}: {detail[:1200]}"
                    ) from exc
                last_error = exc
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt == attempts:
                    self._error_count += 1
                    raise RuntimeError(f"LLM request failed: {exc}") from exc
            except RuntimeError as exc:
                last_error = exc
                if attempt == attempts:
                    self._error_count += 1
                    raise
            time.sleep(2 * attempt)
        self._error_count += 1
        raise RuntimeError(f"LLM request failed after retries: {last_error}")

    def _post_json_requests(
        self, url: str, payload: dict, headers: dict[str, str]
    ) -> str:
        try:
            import requests
        except ImportError:
            return self._post_json_urllib(url, payload, headers)
        try:
            resp = requests.post(
                url, headers=headers, json=payload,
                timeout=self.config.timeout_s,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:1200]}")
        return resp.text

    def _post_json_urllib(
        self, url: str, payload: dict, headers: dict[str, str]
    ) -> str:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            raise
