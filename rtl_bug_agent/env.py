from __future__ import annotations

import os
from pathlib import Path

from rtl_bug_agent.llm.client import LlmConfig, OpenAICompatibleClient


def load_dotenv(path: str | Path = "/home/smy/.env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_provider_config(prefix: str) -> tuple[str, str, str]:
    prefix = prefix.upper()
    names = [f"{prefix}_API_KEY", f"{prefix}_BASE_URL", f"{prefix}_MODEL"]
    values = [os.environ.get(n) for n in names]
    missing = [n for n, v in zip(names, values) if not v]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    return values[0] or "", values[1] or "", values[2] or ""


def make_client(prefix: str, thinking: str | None = None,
                timeout_s: int = 90) -> OpenAICompatibleClient:
    """Create a client from ``<PREFIX>_API_KEY/BASE_URL/MODEL`` env vars.

    ``thinking``: ``"low"`` | ``"medium"`` | ``"high"`` | ``None``.
    The format is auto-selected per provider (DeepSeek expects a struct,
    OpenAI-compatible providers accept a string value).
    """
    api_key, base_url, model = get_provider_config(prefix)

    # Map thinking strength to the provider's format.
    # DeepSeek models have built-in reasoning — do NOT pass an explicit
    # thinking parameter or the model may put all output into
    # reasoning_content and leave content empty.
    thinking_val = None
    if thinking and "deepseek" not in base_url.lower():
        thinking_val = thinking

    return OpenAICompatibleClient(
        LlmConfig(api_key=api_key, base_url=base_url, model=model,
                   thinking=thinking_val, timeout_s=timeout_s)
    )
