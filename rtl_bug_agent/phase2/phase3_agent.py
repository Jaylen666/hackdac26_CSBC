"""
Phase 3 Agent: Source-Level Bug Verification (Agentic)
========================================================

Uses GPT function-calling to let the LLM actively read RTL source files
and trace signals, rather than receiving a fixed pre-extracted window.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtl_bug_agent.llm.client import OpenAICompatibleClient
from rtl_bug_agent.phase2.signal_graph import SignalGraph

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

MAX_TOOL_ROUNDS = 8

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_rtl",
        "description": "Read a range of lines from an RTL source file. "
                       "Use this to trace signals and understand the implementation.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the .sv file"
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read (1-indexed)"
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to read (1-indexed)"
                },
            },
            "required": ["file_path", "start_line", "end_line"],
        },
    },
}

AGENT_PROMPT = """You are a senior RTL design verification engineer.  You are investigating a suspected hardware bug in an OpenTitan IP module.

You have access to a tool:
- **read_rtl(file_path, start_line, end_line)**: read lines from an RTL source file.

Your task:
1. Start by reading the code around the signals and specs mentioned in the finding.
2. Trace the signal chain — where does each signal come from, what drives it, what conditions gate it.
3. Determine: is the finding a real hardware defect, or a false alarm?
4. If you need to see more code, use read_rtl.  Do not guess.

When you are done, output a JSON verdict.  Do NOT include the JSON in a markdown fence.

{
  "verdict": "CONFIRMED | FALSE_ALARM | UNCERTAIN",
  "confidence": 0.0-1.0,
  "summary": "One-paragraph conclusion",
  "root_cause": "If CONFIRMED: exact defect mechanism with line numbers.  If FALSE_ALARM: why the finding is wrong.",
  "trigger_condition": "If CONFIRMED: what inputs/states trigger the bug.  Otherwise empty.",
  "security_impact": "If CONFIRMED: security consequence.  Otherwise empty.",
  "software_visible": true or false,
  "reasoning": "Step-by-step analysis referencing specific line numbers"
}"""


def verify_finding_agent(
    finding: dict[str, Any],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> dict[str, Any]:
    """Verify one Phase 2 finding using an agent with read_rtl tool access."""

    # ── Build initial context ────────────────────────────────────
    involved_signals = finding.get("involved_signals", [])
    involved_specs = finding.get("involved_specs", [])

    # Build a navigation map: signal → (file, line_start, line_end)
    signal_map: dict[str, list[dict]] = {}
    for sig in involved_signals:
        info = graph.signals.get(sig)
        if not info:
            continue
        entries: list[dict] = []
        for spec_id in list(dict.fromkeys(info.drivers + info.consumers)):
            meta = graph.spec_meta.get(spec_id, {})
            src = meta.get("source_file", "")
            if src:
                entries.append({
                    "file": src,
                    "line_start": meta.get("line_start", 1),
                    "line_end": meta.get("line_end", 1),
                    "role": "driver" if spec_id in info.drivers else "consumer",
                })
        signal_map[sig] = entries

    nav_text = "Signals to investigate:\n"
    for sig, entries in signal_map.items():
        nav_text += f"  {sig}:\n"
        for e in entries[:3]:
            fn = Path(e["file"]).name
            nav_text += f"    [{e['role']}] {fn}:{e['line_start']}-{e['line_end']}\n"

    user_prompt = f"""Suspected bug finding:
  Title: {finding.get("title", "")}
  Phase2 verdict: {finding.get("verdict", "")}
  Severity: {finding.get("severity", "")}
  Contradiction: {finding.get("contradiction", "")[:500]}

{nav_text}

All RTL files are under /home/smy/opentitan/hw/ip/. Use the exact paths from the navigation map above.
Start by reading the driver/consumer code for the key signals, then trace to determine if this is a real bug."""

    messages: list[dict] = [
        {"role": "system", "content": AGENT_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # ── Agent loop ───────────────────────────────────────────────
    for round_num in range(max_rounds):
        payload: dict = {
            "model": client.config.model,
            "messages": messages,
            "max_tokens": 2000,
            "tools": [READ_FILE_TOOL],
            "tool_choice": "auto",
        }

        body = client._post_json_with_retries(client._chat_url(), payload)
        parsed = json.loads(body)
        choice = parsed["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason", "")

        # Track token usage
        client.call_count += 1
        usage = parsed.get("usage", {})
        if usage:
            client.total_input_tokens += usage.get("prompt_tokens", 0)
            client.total_output_tokens += usage.get("completion_tokens", 0)
            client.total_tokens += usage.get("total_tokens", 0)

        messages.append(msg)

        # Tool call?
        tool_calls = msg.get("tool_calls", [])
        if tool_calls and finish == "tool_calls":
            for tc in tool_calls:
                func_name = tc["function"]["name"]
                func_args = json.loads(tc["function"]["arguments"])

                if func_name == "read_rtl":
                    file_path = func_args.get("file_path", "")
                    start = func_args.get("start_line", 1)
                    end = func_args.get("end_line", start + 50)
                    result = _read_file(file_path, start, end)
                else:
                    result = f"Unknown tool: {func_name}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            continue

        # Final answer — extract JSON
        content = msg.get("content", "")
        if content:
            verdict = _parse_llm_response(content)
            verdict["_rounds"] = round_num + 1
            return verdict

        # Empty content — try again
        messages.append({"role": "user", "content": "Please continue your analysis."})

    return {"verdict": "UNCERTAIN", "confidence": 0.5,
            "summary": f"Agent did not reach a conclusion within {max_rounds} rounds.",
            "_rounds": max_rounds}


def verify_top_findings_agent(
    findings: list[dict[str, Any]],
    graph: SignalGraph,
    client: OpenAICompatibleClient,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Agentic verification of top-N findings."""
    sorted_findings = sorted(
        findings, key=lambda f: f.get("score", 0), reverse=True
    )[:top_n]

    results = []
    confirmed = 0
    false_alarms = 0

    for idx, f in enumerate(sorted_findings):
        fid = f.get("finding_id", f"F-{idx}")
        print(
            f"  Phase3A [{idx + 1}/{len(sorted_findings)}] {fid} ... ",
            end="", flush=True,
        )
        try:
            verdict = verify_finding_agent(f, graph, client)
        except Exception as exc:
            print(f"ERROR ({exc})")
            results.append({**f, "phase3": {"verdict": "ERROR", "error": str(exc)}})
            continue

        v = verdict.get("verdict", "UNCERTAIN")
        if v == "CONFIRMED":
            confirmed += 1
        elif v == "FALSE_ALARM":
            false_alarms += 1
        rounds = verdict.get("_rounds", "?")
        print(f"{v} ({rounds} rounds, conf={verdict.get('confidence','?')})")
        results.append({**f, "phase3": verdict})

    print(
        f"  Phase3A done: {len(results)} verified "
        f"({confirmed} confirmed, {false_alarms} false alarms)"
    )
    return results


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _read_file(file_path: str, start: int, end: int) -> str:
    """Read a range of lines from a file, with line numbers.

    Tries the exact path first, then common OpenTitan prefixes."""
    p = Path(file_path)
    if not p.exists():
        # Try common alternative prefixes (LLM may guess wrong)
        for prefix in ["/home/smy/opentitan", "/root/opentitan"]:
            # Replace whatever prefix the LLM used with the known one
            alt = Path(str(p).replace(
                str(Path(prefix).parent / Path(prefix).name), prefix
            ))
            # More robust: try name-only match
            attempts = [
                p,
                Path("/home/smy/opentitan") / "/".join(p.parts[-6:]),
                Path("/home/smy/opentitan") / "/".join(p.parts[-4:]),
            ]
            for a in attempts:
                if a.exists():
                    p = a
                    break
            else:
                continue
            break
        else:
            if not p.exists():
                # Last resort: search by filename under opentitan
                import subprocess
                try:
                    result = subprocess.run(
                        ["find", "/home/smy/opentitan", "-name", p.name,
                         "-not", "-path", "*/.git/*", "-not", "-path", "*/dv/*"],
                        capture_output=True, text=True, timeout=5
                    )
                    found = result.stdout.strip().split('\n')
                    if found and found[0]:
                        p = Path(found[0])
                except Exception:
                    pass
    if not p.exists():
        return f"ERROR: file not found: {file_path}"
    lines = p.read_text(encoding="utf-8").splitlines()
    if start < 1:
        start = 1
    if end > len(lines):
        end = len(lines)
    result = []
    for i in range(start - 1, end):
        result.append(f"{i + 1:5d}: {lines[i]}")
    return "\n".join(result)


def _parse_llm_response(content: str) -> dict[str, Any]:
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
