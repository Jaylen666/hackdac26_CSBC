You are extracting formal behavioral clauses from SystemVerilog RTL chunks.

Your output must be JSON ONLY. No markdown, no prose, no explanation.

For the given chunk of RTL code, extract two lists:

## 1. guarantees — what signals does this chunk DRIVE and how?

For each driven signal, write ONE guarantee with:
- `signal`: the driven signal name (exactly as in the RTL)
- `kind`: "assignment" | "mux_case" | "state_transition" | "reg_update" | "default_fallback" | "error_detection"
- `antecedent`: the condition triggering this behavior (SV expression, NO prose)
- `consequent`: the effect (SV expression, NO prose)  
- `temporal`: "comb" | "next_cycle" | "always"
- `formalizable`: true | false (true only if BOTH antecedent and consequent are valid SV expressions)
- `claim`: one-line English summary (optional, only for human readability)

## 2. assumptions — what signals does this chunk READ and what does it assume?

For each input/read signal, write ONE assumption about the protocol/value range expected:
- `signal`: the assumed signal name
- `kind`: "value_domain" | "handshake" | "producer_contract" | "mode_config" | "security_precondition"
- `antecedent`: when this assumption applies (SV expression)
- `consequent`: the required condition (SV expression)
- `temporal`: "comb" | "next_cycle" | "always"
- `formalizable`: true | false
- `risk`: what goes wrong if violated (short)
- `claim`: one-line English summary

## RULES (CRITICAL):

1. antecedent and consequent MUST be SystemVerilog expressions using real signal names,
   operators (==, !=, &&, ||, !, >=, <=, >, <), and real constants.
   Example GOOD: antecedent="key_clear == 1" consequent="wipe_val == 1'b1"
   Example BAD:  antecedent="when key_clear is active" consequent="wipe_val is set high"

2. If you CANNOT express the behavior as formal SV, set formalizable=false and
   explain in claim. Do NOT write prose in antecedent/consequent.

3. For always_comb chunks: temporal is "comb"
4. For always_ff chunks: temporal is "next_cycle"
5. For assignments: check the assigned expression directly

6. Each chunk drives roughly 1-5 signals. Be precise, not exhaustive.
   Merge similar assignments into one guarantee with a broader consequent.

7. Signal names must match the RTL EXACTLY. Do not rename, simplify, or invent signals.

8. Be COMPLETE. Extract EVERY guarantee for every driven signal. Do NOT skip signals
   or merge them. A chunk with 30 `_we` signals must produce 30 guarantee entries.
   Repetitive patterns are expected — output them all.

## Output format:

```json
{
  "guarantees": [
    {
      "signal": "signal_name",
      "kind": "assignment",
      "antecedent": "condition_expression or '1' if unconditioned",
      "consequent": "result_expression",
      "temporal": "comb",
      "formalizable": true,
      "claim": "short English"
    }
  ],
  "assumptions": [
    {
      "signal": "input_signal_name",
      "kind": "value_domain",
      "antecedent": "when_this_applies or '1'",
      "consequent": "assumed_property",
      "temporal": "comb",
      "formalizable": true,
      "risk": "what fails",
      "claim": "short English"
    }
  ]
}
```

Now analyze the chunk below. Output JSON only.
