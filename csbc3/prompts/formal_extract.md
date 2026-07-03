You are extracting formal SystemVerilog clauses from an always block.

For EACH signal driven by this block, write ONE formal clause:

  antecedent → consequent

Where:
- antecedent: the condition under which this behavior applies (SV expression)
- consequent: the resulting value or behavior (SV expression)
- Both use real signal names and SV operators (==, !=, &&, ||, !, ?:, {})

Rules:
1. antecedent must be a boolean SV expression (e.g., "state_q == IDLE && en_i == 1")
2. consequent must be an SV expression for the driven signal (e.g., "next_state == ACTIVE")
3. Use "1" for antecedent if the assignment is unconditional
4. If you CANNOT express the behavior as formal SV, set formalizable=false
   and explain why in comment. Do NOT write prose in antecedent/consequent.
5. Signal names must match the RTL EXACTLY.

Output JSON format:
{
  "signals": [
    {
      "name": "driven_signal_name",
      "antecedent": "SV condition expression or '1'",
      "consequent": "SV result expression",
      "temporal": "comb | next_cycle",
      "formalizable": true,
      "comment": "optional explanation"
    }
  ]
}

Output JSON only.
