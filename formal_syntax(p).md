# 📘 Formal Syntax & Semantics Summary (Revamping Verilog Semantics for Foundational Verification (OOPSLA'25))

## 1. Formal Syntax 
The syntax targets the **synthesizable, deterministic, synchronous subset** of Verilog. It is formalized as Abstract Syntax Trees (ASTs) with custom Rocq notations.

| Construct | Formal Grammar |
|-----------|----------------|
| **Module** | `m ::= module ⟨id, ⟨id, e⟩, id, id, gb⟩` |
| **Generate Block** | `gb ::= bl \| if(e) then id: gb else id: gb` |
| **Block** | `bl ::= assign ⟨lv, e⟩ \| always_ff ee st \| always_comb st \| ⟨id, id, ⟨id, e⟩, ⟨id, e⟩, ⟨id, lv⟩⟩_mod` |
| **Statement** | `st ::= lv = e \| lv <= e \| if(e) then st else st` |
| **Expression** | `e ::= c \| id \| e.e \| e[e] \| {e} \| op(e) \| op(e, e)` |
| **L-value** | `lv ::= e` |
| **Event Expr** | `ee ::= posedge clk \| negedge clk` |

**Coverage Notes:**
- Supports: wires, registers, blocking/nonblocking assignments, combinational/sequential `always` blocks, continuous assignments, parameters, module instances, generate blocks.
- Excludes: latches, tasks, manual sensitivity lists, `typedef`/`enum`, interfaces/packages, `X`/`Z` values, multi-clock designs.

## 2. Construct Classification for Verification

The four construct types each have a distinct verification path:

| Construct | Formal Grammar | Treatment | Cost | Reliability |
|-----------|---------------|-----------|------|-------------|
| **Assign** | `assign ⟨lv, e⟩` | Parser — deterministic extraction | 0 LLM | 100% |
| **Always** | `always_comb st` / `always_ff ee st` | LLM — NL spec + Formal spec (parallel) | 2 LLM per block | NL: high, Formal: medium |
| **Instance** | `⟨id, id, ⟨id, e⟩, ⟨id, e⟩, ⟨id, lv⟩⟩_mod` | LLM — contract model (caller assumption vs callee guarantee) | 1 LLM per instance | Medium |
| **Generate** | `gb ::= bl \| if(e) then id: gb else id: gb` | **Unfold** — static elaboration, then reclassify as above | 0 LLM | 100% |

### 2.1 Generate Unfolding
A `generate for (genvar i=0; i<N; i++)` block is **static** — the elaborator unrolls it into N copies of the contained construct at compile time. For verification:

```
generate for (i=0; i<4; i++)
  assign X[i] = Y[i] + Z[i];
endgenerate
  → unfold:
    assign X[0] = Y[0] + Z[0];   ← AssignChunk
    assign X[1] = Y[1] + Z[1];   ← AssignChunk
    assign X[2] = Y[2] + Z[2];   ← AssignChunk
    assign X[3] = Y[3] + Z[3];   ← AssignChunk
```

The generated chunks inherit their type from the body. An `always` inside a generate becomes an AlwaysChunk; an instance becomes an InstanceChunk. No special verification treatment needed.

### 2.2 Interface Decomposition
A `interface` in synthesizable SV is a named bundle of port declarations. When a module instances an interface port, the connection decomposes into per-signal connections:

```
interface bus_if;
  logic [31:0] data;
  logic        valid;
  modport slave (input data, output valid);
endinterface
  → per-signal ports on the instance:
    .data_i(data), .valid_o(valid)
```

No special verification treatment — the signal names with structured paths flow through the standard chunking.

---
## 2. Semantic Domain: Hierarchical Maps (HMap)
Verilog states and values are represented using a nested map structure that natively mirrors module hierarchy.

```math
h \in \text{HMap} ::= [\,] \mid b \mid \langle i, h \rangle_{\text{arr}} \mid \langle \text{id}, h \rangle_{\text{str}}
```
- **Paths** `p ∈ P`: Lists of IDs/indices for lookup: `h[p]`
- **Bits** `b`: Tuple `(value: ℤ, size: ℤ, signed: 𝔹)` for proof automation
- **Domains**:
  - `S = V = HMap` (States & Values)
  - `D = HMap` (Declarations; leaf values carry no semantics)
  - `S_u ≜ S`, `D_u  D`, `R_u ≜ R` (Update maps)
- **Map Union**: `s ⊎ s_u` overrides existing paths with new values.

---
## 3. Functional Semantics
Semantics are defined **bottom-up** using a `Fail` monad to handle unresolved dependencies.

### 3.1 Expression & Statement Denotations
| Construct | Signature | Behavior |
|-----------|-----------|----------|
| `⟦e⟧_e` | `P × S → Fail V` | Evaluates expression in scope `p` & state `s`. Fails if operand unevaluated. |
| `⟦lv⟧_lv` | `P × D × S → Fail P` | Resolves L-value to a hierarchical path from declarations. |
| `⟦lv = e⟧_st` | `P × D × S × S_u → Fail S_u` | **Blocking**: uses `s ⊎ s_u` (sequential within block) |
| `⟦lv <= e⟧_st` | `P × D × S × S_u → Fail S_u` | **Nonblocking**: uses original `s` (concurrent) |
| `⟦if(e) st_t else st_f⟧_st` | `... → Fail S_u` | Uses **predicated updates**: `(h_1 ⊎_{\{p\}} h_2)` guards per-variable updates by condition `p`. |

### 3.2 Block & Module Denotations
| Construct | Signature | Behavior |
|-----------|-----------|----------|
| `⟦bl⟧_bl` | `P × D × S → Fail(D_u × S_u × R_u)` | Returns declaration/wire/register updates. `always_comb` → `S_u`, `always_ff` → `R_u`. |
| `⟦m⟧_mod` | `D × S → D × S × R_u` | **Semantic Transfer**: Resolves **one layer** of value-update dependencies. |
| `⟦m⟧^∞_mod` | `D × S → D × S × R_u` | **LFP Wrapper**: `lfp_{(d,s)} ⟦m⟧_mod(d,s)`. Iterates until no new wire updates occur. |
| `T_m` | `S_{in} × R → R' × S_{out}` | **Cycle Function**: Extracts final register updates & outputs from `⟦m⟧^∞_mod`. |

**Key Property:** `T_m` is **modular**. Parent modules compositionally invoke submodule transition functions without flattening hierarchy.

---
## 4. Formalization of IEEE Standard Semantics
The standard is event-driven and logically timed. For synthesizable designs, only two regions matter:
- **Active**: Blocking assignments & combinational logic
- **NBA**: Nonblocking assignments

**Execution Model:**
```math
\text{ExecTimeSlot}(s_0, \text{Acts}, \text{NBAs}, s_1)
```
- Nondeterministically picks events from `Acts`/`NBAs` until regions empty.
- One logical step may contain multiple interleaved updates.
- Top-level events: `EventClk` (clock tick) & `EventUpdate ins` (input change).
- Assumption: Inputs change **between** clock ticks (no simultaneous scheduling).

---
## 5. Equivalence Proof Process
**Goal:** Prove `T_m ≡ \text{IEEE Scheduling Semantics}` for guideline-compliant designs.

### 5.1 State Alignment
Bridge the gap between wire-inclusive standard states and register-only cycle states:
```math
\begin{aligned}
\text{stateOf}(ins, regs) &\triangleq m⟧^∞_{mod}([], ins ⊎ regs)[1] \\
\text{trsF}(ins, regs) &\triangleq T_m(ins, regs)[0] \\
\text{TrsC}(s_0, s_1) &\triangleq \text{ExecTimeSlot}(s_0, [\text{EventClk}], [], s_1) \\
\text{TrsI}(s_0, ins, s_1) &\triangleq \text{ExecTimeSlot}(s_0, [\text{EventUpdate }ins], [], s_1)
\end{aligned}
```

### 5.2 Core Lemmas
| Lemma | Statement | Intuition |
|-------|-----------|-----------|
| **Confluence** | `ExecTimeSlot s0 acts nbas s1 → ExecTimeSlot s0 acts nbas s2 → s1 = s2` | Value-update dependencies form a topological order. Final state is **unique** regardless of nondeterministic scheduling. |
| **Forward (Ours ≤ Standard)** | `trsF ins r0 = r1 → TrsC(stateOf ins r0)(stateOf ins r1)` | Our deterministic LFP order is **one valid path** permitted by the standard scheduler. |
| **Backward (Standard ≤ Ours)** | `TrsC(stateOf ins r0) s1 → s1 = stateOf ins(trsF ins r0)` | Confluence guarantees the standard's nondeterministic execution **converges** to our fixed point. |

### 5.3 Main Equivalence Theorem
```math
\begin{aligned}
\forall ins_1, r_0, r_1,\quad &\text{trsF}(ins_1, r_0) = r_1 \iff \\
&\exists ins_0, s.\ \text{ExecTimeSlot}(\text{stateOf}(ins_0, r_0), [\text{EventUpdate }ins_1], [], s) \\
&\quad\quad\quad \land\ \text{ExecTimeSlot}(s, [\text{EventClk}], [], \text{stateOf}(ins_1, r_1))
\end{aligned}
```
**Interpretation:** One physical cycle in `T_m` corresponds to **two logical steps** in the standard: input update → clock tick. Equivalence holds under the practical assumption that inputs stabilize between clock edges.

---
## 6. Mechanization & Proof Automation
- **Theorem Prover:** Fully mechanized in **Rocq (Coq)**.
- **Parsing:** Verilog → AST via Rocq "custom entries".
- **Termination Proof:** `m⟧^∞_mod` uses a subset type requiring a proof that LFP converges. Discharged automatically for guideline-compliant designs (no combinational loops).
- **Partial Evaluation:** `vm_compute` tactic pre-evaluates pure HMap operations, yielding fast execution traces for simulation/proof checking.
- **Verification Case Study:** Modular proof of a 4-stage RISC-V pipeline (~500 LOC) with functional correctness + progress guarantees. Proof checking time: **148s** (≈5.3× faster than Kami).
