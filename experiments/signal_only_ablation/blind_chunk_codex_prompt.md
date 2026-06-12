# Blind Chunk Codex Ablation Prompt

You are running a blind bug-finding ablation experiment on OpenTitan RTL.

Goal:
Evaluate whether giving a coding agent a small, pre-selected package of RTL chunks improves bug reasoning quality, compared with giving it the full RTL. This is a blind analysis setting: each package may or may not contain a real bug. Your job is to inspect each package independently, reason only from the provided RTL chunks plus official public spec documents when necessary, and return a structured judgment.

Strict scope rules:
1. Treat each package independently. Do not carry conclusions, hypotheses, or patterns from one package to another.
2. Do not inspect the full RTL module or any unrelated RTL files outside the provided package chunks.
3. You may inspect official public spec / documentation files for the same IP block if, and only if, the package suggests a potentially incorrect behavior and you need the spec to confirm or reject it.
4. Do not use benchmark labels, prior known bug IDs, or external memory about previously discussed bugs.
5. Do not assume a bug exists. Some packages may be benign or too underspecified to confirm.

Allowed evidence:
- The RTL chunks inside the current package.
- Official spec documents for the same IP block, such as:
  - `doc/*.md`
  - `data/*.hjson`
  - other clearly official module documentation under the same IP directory
- If a spec statement is too generic to strictly support the claimed bug, mark it as related but weak.

Disallowed evidence:
- Full-module RTL exploration outside the package.
- Looking up prior findings, benchmark answers, or any ground-truth bug list.
- Using unrelated modules as analogies.

Required analysis procedure for each package:
1. Read all chunks in the package first.
2. Form a local understanding of the behavior implemented by those chunks.
3. Identify whether there is a concrete suspicious behavior, not just odd style.
4. Only if needed, consult the official spec for that same IP block to determine whether the behavior is:
   - directly contradicted by spec,
   - indirectly/weakly inconsistent with spec,
   - or not supported by spec at all.
5. Produce one final judgment for the package.

Judgment categories:
- `bug`: The package contains a real design bug or security-relevant logic flaw that can be justified from the RTL and, when needed, supported by spec.
- `suspicious`: The package contains a plausible issue, but the available local RTL/spec evidence is insufficient to confirm it confidently.
- `no_bug`: No meaningful bug is found in the package, or the observed behavior is consistent with the available evidence.

Per-package output cap:
- List at most 3 items in `rtl_evidence`.
- List at most 3 items in `spec_evidence`.
- If more than 3 suspicious points exist, keep only the 3 strongest ones.

Confidence scale:
- `high`
- `medium`
- `low`

For every package, return:
- package id
- IP/module name
- a short summary of what the package implements
- whether you consulted spec files
- which spec files were consulted
- the most relevant RTL evidence
- the most relevant spec evidence, if any
- final judgment
- confidence
- a concise rationale
- if `bug` or `suspicious`, a one-paragraph bug hypothesis describing the failure mode and why it matters

Important quality bar:
- Prefer precise behavioral claims over vague concern language.
- Distinguish implementation oddity from true behavioral defect.
- If the spec evidence is generic and does not directly constrain the behavior, say so.
- Do not overclaim from partial context.
- Do not downgrade a package just because it is small; if the bug is locally evident, say so.

Output format:
Write a single JSON file. The top-level object must be:
{
  "experiment": "blind_bug_chunk_codex_ablation",
  "results": [
    {
      "package_id": "...",
      "ip": "...",
      "summary": "...",
      "spec_consulted": true,
      "spec_files": ["...", "..."],
      "rtl_evidence": [
        {"file": "...", "line_start": 0, "line_end": 0, "reason": "..."}
      ],
      "spec_evidence": [
        {"file": "...", "reason": "...", "strength": "strong|weak"}
      ],
      "judgment": "bug|suspicious|no_bug",
      "confidence": "high|medium|low",
      "rationale": "...",
      "bug_hypothesis": "..."
    }
  ]
}

If `judgment` is `no_bug`, set `bug_hypothesis` to an empty string.
If no spec was consulted, set `spec_consulted` to false and `spec_files` / `spec_evidence` to empty lists.

Evaluation mindset:
This is a blind ablation, so fairness matters more than recall-at-all-costs. Stay inside the package. Use spec only as a confirmation aid, not as a license to expand into a full-design audit.

Input constraints for the concrete run:
- The only input file is `/home/smy/rtl_bug_agent/experiments/signal_only_ablation/out/bug_chunks_blind.json`.
- Do not inspect any other experiment output, finding file, benchmark file, or result file.
- For each package, analyze the chunks provided under that package only.

Module-specific package boundaries:
- `hmac`: PKG-001 .. PKG-007
- `aes`: PKG-008 .. PKG-012
- `keymgr`: PKG-013 .. PKG-016
- `uart`: PKG-017 .. PKG-019
