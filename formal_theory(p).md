# Formal Methodology: Chunk-Based Security Verification & Contradiction Resolution

> **Note:** This document describes the theoretical framework. The implementation (`csbc3/`) also adds a parallel natural-language spec track and a structural anomaly detection channel not covered here — see §7.

## 1. Foundations & Notation
Let $\mathcal{M}$ denote the set of guideline-compliant, single-clock Verilog modules. For each $m \in \mathcal{M}$:
- $\mathcal{S}, \mathcal{R}$: Hierarchical Maps (HMaps) for wire states and registers
- $T_m : \mathcal{S}_{in} \times \mathcal{R} \to \mathcal{R}' \times \mathcal{S}_{out}$: Deterministic cycle-level transition function derived via LFP
- $\mathcal{I}_m \in \text{ITree}(\text{Input}, \text{Output})$: Coinductive multi-cycle behavior
- $\sqsubseteq_{beh}$: Behavioral refinement (trace inclusion)
- $\equiv_{IEEE}$: Equivalence to standard scheduling semantics for synthesizable designs

---
## 2. Chunk Decomposition & Security Specification

### 2.1 Hierarchical Chunking
A SoC/CPU $\mathcal{C}$ is decomposed into a directed acyclic composition graph:
$$
\mathcal{C} = \bigoplus_{i=1}^n M_i \quad \text{with interfaces} \quad \partial M_i = (I_i, O_i, \text{Proto}_i)
$$
Parent-child composition is defined by port binding $\beta_{ij}: O_i \leftrightarrow I_j$. The global transition function composes modularly:
$$
T_{\mathcal{C}} = \bigcirc_{\beta} \left( T_{M_1}, T_{M_2}, \dots, T_{M_n} \right)
$$
where $\bigcirc_{\beta}$ denotes hierarchical state projection and interface synchronization.

### 2.2 Per-Chunk Security Predicates
Extend each chunk's specification with a **security invariant bundle** $\Phi_{M_i}$:
$$
\Phi_{M_i} \equiv \Phi_{M_i}^{func} \land \Phi_{M_i}^{conf} \land \Phi_{M_i}^{iso} \land \Phi_{M_i}^{live} \land \Phi_{M_i}^{sc}
$$
Formally, $\Phi_{M_i} \subseteq \mathcal{S} \times \mathcal{R} \times \Sigma$, where $\Sigma$ tracks security context (domains, taint labels, timing windows, observable channels).

| Property | Formal Definition |
|----------|-------------------|
| **Confidentiality** | $\forall (s,r). \Phi_{M_i}^{conf}(s,r) \Rightarrow \text{Leakage}(T_{M_i}(s,r), \mathcal{O}_{unpriv}) = 0$ |
| **Isolation** | $\text{Dom}(s) \cap \text{Dom}(r') = \emptyset \lor \text{Authorized}(\text{Dom}(s), \text{Dom}(r'))$ |
| **Liveness/Progress** | $\Diamond_{\leq k} \text{Commit}(s,r)$ (bounded progress within $k$ cycles) |
| **Side-Channel** | $\forall c \in \mathcal{O}_{phys}. \text{Var}(c, T_{M_i}(s,r)) \perp \text{Secret}(s)$ |

**Preservation Condition:** A chunk is security-correct if:
$$
\forall (s,r). \Phi_{M_i}(s,r) \Rightarrow \Phi_{M_i}(T_{M_i}(s,r))
$$

### 2.3 ITree Lifting of Security Properties
Multi-cycle security properties are lifted to trace semantics:
$$
\text{SecTrace}(\mathcal{I}_{M_i}, \Psi) \equiv \forall \tau \in \text{traces}(\mathcal{I}_{M_i}). \Psi(\tau)
$$
where $\Psi$ is a temporal security predicate (e.g., $\Box (\text{Valid}(o) \Rightarrow \text{Domain}(o) = \text{Domain}(i))$).

---
## 3. Formal Contradiction Detection

### 3.1 Global Specification & Composition Constraints
The composed system specification is:
$$
\Phi_{\mathcal{C}} \equiv \left( \bigwedge_{i=1}^n \Phi_{M_i} \right) \land \left( \bigwedge_{(i,j) \in E} \mathcal{K}_{ij} \right)
$$
where $\mathcal{K}_{ij}$ enforces interface compatibility:
- **Protocol matching**: $\text{Proto}(O_i) \equiv \text{Proto}(I_j)$
- **Timing alignment**: $\text{Latency}(O_i) \leq \text{Setup}(I_j)$
- **Security policy composition**: $\Phi_{M_i}^{iso} \land \Phi_{M_j}^{cross} \not\Rightarrow \bot$

### 3.2 Contradiction as Unsatisfiability
A **security contradiction** exists iff the composed transition relation and invariants are mutually unsatisfiable:
$$
\exists k \in \mathbb{N}.\ \neg \text{SAT}\left( \bigwedge_{t=0}^{k-1} \left[ (s_{t+1}, r_{t+1}) = T_{\mathcal{C}}(s_t, r_t) \right] \land \Phi_{\mathcal{C}}(s_0, r_0) \right)
$$
This is equivalent to a **proof failure** in deductive verification:
$$
\Phi_{\mathcal{C}} \vdash \bot \quad \text{or} \quad \mathcal{I}_{\mathcal{C}} \not\sqsubseteq_{beh} S_{spec}^{sec}
$$

### 3.3 Contradiction Taxonomy
| Type | Formal Condition | Detection Method |
|------|------------------|------------------|
| **Interface Mismatch** | $\exists p \in O_i \cap I_j.\ \text{Type}(p, M_i) \not\equiv \text{Type}(p, M_j)$ | Type/protocol unification failure |
| **Timing Violation** | $\text{Latency}(O_i) > \text{Setup}(I_j) \lor \text{Hold}(I_j) > \text{Skew}$ | Bounded reachability over $T_{\mathcal{C}}$ |
| **Security Policy Conflict** | $\Phi_{M_i}^{iso} \land \Phi_{M_j}^{allow} \Rightarrow \bot$ | SMT entailment check over $\Sigma$ |
| **Liveness Deadlock** | $\neg \Diamond \text{Commit}(s,r)$ under $\Phi_{\mathcal{C}}$ | FreeSim progress obligation failure |
| **Side-Channel Leakage** | $\text{Leakage}(\tau) > \epsilon$ for valid trace $\tau$ | Trace projection + information-theoretic bound |

---
## 4. Suggestion & Decision Framework

### 4.1 Fix Pattern Library $\mathcal{F}$
Each contradiction $\delta$ maps to a structural/security transformation:
$$
\mathcal{F}_\delta : M_i \mapsto M_i' \quad \text{s.t.} \quad \Phi_{\mathcal{C}}' \text{ is satisfiable}
$$

| Contradiction | Fix Pattern $\mathcal{F}_\delta$ | Formal Effect |
|---------------|----------------------------------|---------------|
| Timing mismatch | Insert synchronizer $\text{Sync}(\cdot)$ | $T_{M_i'} = T_{M_i} \circ \text{Delay}_2$ |
| Domain crossing | Add bus filter $\text{Filter}_{dom}(\cdot)$ | $\text{Dom}(o) \leftarrow \text{Dom}(i) \land \text{ACL}(i,o)$ |
| Liveness deadlock | Inject timeout/recovery $\text{Timeout}_k(\cdot)$ | $\Diamond_{\leq k} \text{Reset} \lor \text{Commit}$ |
| Speculative leak | Add flush barrier $\text{Flush}(\cdot)$ | $\text{Leakage} \leftarrow 0$ on domain switch |

### 4.2 Automated Decision Loop
1. **Extract**: Parse $\mathcal{C}$ into $\{M_i, T_{M_i}, \Phi_{M_i}\}$ using Rocq custom entries
2. **Compose**: Build $T_{\mathcal{C}}$ and $\Phi_{\mathcal{C}}$ via hierarchical HMap projection
3. **Check**: Run bounded satisfiability + ITree refinement proof
4. **Diagnose**: Extract counterexample trace $\tau_{ce}$ and failed obligation
5. **Suggest**: Match $\tau_{ce}$ to $\mathcal{F}_\delta$, propose $M_i \mapsto M_i'$
6. **Re-verify**: Prove $\Phi_{M_i'} \land \Phi_{\mathcal{C}\setminus\{M_i\}}$ satisfiable & $\mathcal{I}_{\mathcal{C}'} \sqsubseteq_{beh} S_{spec}^{sec}$

---
## 5. Theoretical Guarantees

### Theorem 1 (Soundness of Security Composition)
If $\Phi_{\mathcal{C}}$ is satisfiable and each $\Phi_{M_i}$ is preserved by $T_{M_i}$, then:
$$
\mathcal{I}_{\mathcal{C}} \sqsubseteq_{beh} S_{spec}^{sec} \implies \mathcal{C} \text{ adheres to } \Phi_{\mathcal{C}} \text{ under IEEE semantics}
$$
*Proof sketch*: Follows equivalence ($T_m \equiv_{IEEE} \text{Standard}$) and ITree adequacy. Modular preservation lifts to global trace inclusion.

### Theorem 2 (Modular Reusability)
If $\Phi_{M_i}$ is proven for $M_i$, then for any parent $M_p$ containing $M_i$:
$$
\Phi_{M_p} \text{ can discharge } \Phi_{M_i} \text{ as a lemma without re-proving internal wires}
$$
*Proof sketch*: Direct consequence of modularity: $T_{M_p}$ calls $T_{M_i}$ as a black-box transition. Invariants compose via HMap projection.

### Corollary 3 (Progress under Security Constraints)
If $\Phi_{M_i}^{live}$ holds for all chunks and interface protocols are deadlock-free, then:
$$
\text{FreeSim}(\mathcal{I}_{\mathcal{C}}, S_{spec}^{sec}) \text{ satisfies progress obligation}
$$
*Proof sketch*: Extends progress proof by adding security-induced $\mathbf{Tau}$ steps that preserve simulation relation.

---
## 6. Example Instantiation: RISC-V Core + Security Extension

### Chunks
- `FD`: Fetch/Decode + BTB
- `EX`: Execute + ALU
- `DC`: Data Cache + MMU

### Security Specs
$$
\begin{aligned}
\Phi_{FD}^{conf} &\equiv \Box (\text{pc\_d2e} \notin \text{Observable}_{unpriv}) \\
\Phi_{DC}^{iso} &\equiv \text{Dom}(req) \Rightarrow \text{CacheTag}[req] = \text{Dom}(req) \\
\mathcal{K}_{FD,DC} &\equiv \text{Latency}(FD.out) \leq 1 \land \text{Proto}(FD.vld) = \text{ValidReady}
\end{aligned}
$$

### Contradiction Detected
Speculative fetch in `FD` leaks `pc_d2e` to `DC` before domain check:
$$
\Phi_{FD}^{conf} \land \Phi_{DC}^{iso} \land T_{\mathcal{C}} \Rightarrow \bot \quad (\text{side-channel trace } \tau_{ce})
$$

### Suggestion
Apply $\mathcal{F}_{leak}$: Insert domain filter between `FD` and `DC`:
$$
\text{DC}_{in}' = \text{Filter}_{dom}(\text{FD}_{out}) \implies \Phi_{\mathcal{C}}' \text{ satisfiable}
$$
Re-verify via ITree refinement: $\mathcal{I}_{\mathcal{C}'} \sqsubseteq_{beh} S_{RISC-V}^{sec}$.

---
## 7. Implementation Roadmap

| Phase | Toolchain Integration | Deliverable |
|-------|----------------------|-------------|
| **1. Parser & LFP Extraction** | Rocq custom entries + `vm_compute` partial evaluation | $\{T_{M_i}\}$ from Verilog AST |
| **2. Security Spec DSL** | Extend HMap with $\Sigma$-context; add temporal operators | $\Phi_{M_i}$ definitions in Rocq |
| **3. Contradiction Engine** | SMT (Z3/CVC5) + bounded reachability over $T_{\mathcal{C}}$ | Counterexample traces $\tau_{ce}$ |
| **4. Suggestion Library** | Pattern matching on proof failures + structural transformations | $\mathcal{F}_\delta$ recommendations |
| **5. End-to-End Flow** | FreeSim + ITree refinement + Rocq proof automation | Certifiable security report |

---
## 7. Hybrid NL + Formal Architecture (CSBC v3)

The theoretical framework above (§1-§6) assumes all specifications are formal. In practice, some behaviors (timing glitches, pulse widths, fault containment windows) resist clean formalization. The hybrid architecture adds two parallel tracks:

### 7.1 Parallel Spec Generation

Each always block produces TWO independent specifications simultaneously:

```
always_comb block
       │
       ├── Prompt A (NL-only): "describe behavior in English"
       │       ↓
       │   NL spec: "hash_done_event is a single-cycle pulse; consumer must
       │   sample it within that cycle because in the next cycle it is
       │   cleared to 0 unless reasserted"
       │
       └── Prompt B (Formal-only): "express as SV clauses"
               ↓
           Formal spec: (done_state_q == DoneAwaitHashDone && reg_hash_done)
                        ? 1'b1 : 8'd0
```

The two specs are cross-checked after generation:
- **Temporal mismatch**: formal says `comb` but NL says "next cycle" → flag
- **Coverage gap**: NL describes a condition missing from formal → flag
- **Semantic contradiction**: formal says `X == 1` but NL says "X defaults to 0" → flag

### 7.2 Structural Anomaly Detection

For `assign` chunks, no LLM is needed. The parser extracts the RHS expression directly. Anomaly detection groups signals by suffix and checks for semantic outliers using Z3 equivalence:

**Formal criterion for anomaly:**

Given a group of signals $\{S_1, ..., S_n\}$ sharing the same suffix (e.g., `_we`) and their normalized antecedent expressions $\{A_1, ..., A_n\}$:

If there exists a minority subset $\{S_m\}$ such that:
$$
\exists \text{inputs}. \bigwedge_{i \in \text{majority}} A_i \neq \bigwedge_{j \in \text{minority}} A_j
$$

then the minority expressions are anomalous. Z3 checks this by solving:
$$
\text{SAT}\left( \bigwedge_{i \in \text{majority}} (A_i(\text{inputs}) \not\equiv A_k(\text{inputs})) \right) \quad \text{for each minority } k
$$

If SAT, the minority expression $A_k$ is **structurally different** — a real anomaly.
If UNSAT, the syntactic difference is **semantically equivalent** — a false positive (e.g., `&` vs `&&` on 1-bit signals produce the same truth table).

### 7.3 Uncertainty Tagging

When the LLM can describe a behavior in NL but cannot express it as a formal `antecedent → consequent` clause, the spec carries an uncertainty tag:

| Tag | Meaning | Example |
|-----|---------|---------|
| `low` | Behavior fully captured by formal clause | "X is asserted when key_clear == 1" |
| `medium` | Formal captures the value but not the timing | "X is a single-cycle pulse" (formal says X == 1, loses pulse width) |
| `high` | Behavior cannot be formalized at all | "The combinational path may glitch if digest_size changes mid-transfer" |

Uncertainty tags flow through the pipeline:
- `low` → Z3 cross-check (deterministic)
- `medium` → LLM Phase 2 residual check
- `high` → Human review recommended

### 7.4 Combining the Two Frameworks

| Aspect | Pure Formal (§1-§6) | Hybrid (§7) |
|--------|---------------------|-------------|
| Spec source | Rocq custom entries | LLM + parser |
| Verification | ITree refinement + SMT | Z3 + structural anomaly + NL residual |
| False positives | Near zero | Acceptable on NL track |
| Coverage | Formalizable constructs only | All constructs (uncertainty tagged) |
| When to use | High-assurance, critical paths | Broad coverage, fast triage |

The hybrid approach trades some formal guarantees for broader coverage. The formal track (§1-§6) remains the long-term goal for critical security properties; the hybrid track (§7) is the practical near-term path that works on real RTL today.

---
## Key Advantages Over Ad-Hoc Methods
- **Minimal TCB**: Only Rocq kernel + LFP semantics trusted 
- **Deterministic Reasoning**: No false positives from nondeterministic scheduling 
- **Compositional Scaling**: Prove chunks independently; reuse lemmas hierarchically 
- **IEEE Equivalence**: Guarantees hold for real silicon, not just abstract models 
- **Actionable Output**: Contradictions map directly to structural/security fixes
