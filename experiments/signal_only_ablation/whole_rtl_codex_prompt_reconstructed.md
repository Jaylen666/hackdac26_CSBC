# Whole-RTL Codex Ablation Prompt (Reconstructed)

Note:

- The exact original prompt used in the earlier whole-RTL Codex ablation was not separately persisted at run time.
- This file is a reconstructed version based on the actual protocol that was used for that experiment.
- It is intended for reproducibility of the experimental setup, not as a byte-for-byte log of the historical prompt.

## Reconstructed Prompt

You are running a whole-RTL bug-finding ablation experiment on OpenTitan.

Goal:
Evaluate how well a Codex-style agent can find security-relevant RTL bugs when it is given access to the full RTL of one module and the official public documentation for that same module, without using CSBC-generated AG/U descriptions, without benchmark answers, and without any pre-cut bug chunks.

Scope rules:
1. Analyze exactly one target IP module at a time.
2. You may inspect the RTL under that module's RTL directory.
3. You may inspect official public spec/doc files for that same module only.
4. Do not inspect benchmark files, findings files, previous experiment outputs, or any stored ground truth.
5. Do not use prior knowledge of known bugs for that module.

Allowed evidence:
- RTL files under the target module, for example:
  - `hw/ip/<module>/rtl/`
- Official public module documentation, for example:
  - `hw/ip/<module>/doc/*.md`
  - `hw/ip/<module>/data/*.hjson`
  - other clearly official module documentation under the same IP

Disallowed evidence:
- Any benchmark answer files
- Any prior findings JSON
- Any experiment result files
- Any cross-module analogy as evidence

Task:
1. Read the module RTL and build your own understanding of the control/data behavior.
2. Inspect official public docs when they are needed to confirm intended behavior.
3. Identify the strongest bug candidates in the module.
4. Return at most 6 findings for the module.
5. Prioritize concrete behavioral/security issues over code style concerns.

Finding quality bar:
- Prefer bugs with a clear RTL mechanism.
- If you cite spec support, distinguish strong/direct support from weak/indirect support.
- Do not overclaim from partial context.
- If a point is suspicious but not confirmable, say so explicitly.

Output format:
Return a JSON object of the form:

{
  "module": "<module>",
  "findings": [
    {
      "title": "...",
      "judgment": "bug|suspicious",
      "confidence": "high|medium|low",
      "rtl_evidence": [
        {"file": "...", "line_start": 0, "line_end": 0, "reason": "..."}
      ],
      "spec_evidence": [
        {"file": "...", "reason": "...", "strength": "strong|weak"}
      ],
      "rationale": "...",
      "bug_hypothesis": "..."
    }
  ]
}

Additional constraints:
- Return at most 6 findings total.
- If no meaningful issue is found, return an empty `findings` array.
- Focus on real behavioral defects, especially security-relevant ones.

## Intended Use

This reconstructed prompt corresponds to the earlier baseline referred to in the comparison table as:

- `Codex_agent`
- `Codex whole-RTL`
- `direct-agent matched-top6`

It should be read together with:

- `experiments/signal_only_ablation/bug_comparison_table.xlsx`
- `experiments/signal_only_ablation/generate_chart.py`
