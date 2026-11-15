# ADR 004: Tree-Walking AST Rule Evaluation vs Python Eval / Dynamic Script Engines

## Status
Accepted

## Context
Tenants need to define custom validation rules and workflow transition guards (e.g. `invoice.amount <= 1000`, `lead.score >= 80`, or regex email validations). 

Allowing tenants to supply logic that executes on the server introduces severe security threats:
1. **Remote Code Execution (RCE)**: Using Python's `eval()` or `exec()` exposes the server process to arbitrary code execution, container breakout, and credential theft.
2. **Algorithmic DoS / Infinite Loops**: Unbounded expression depth or malicious recursive constructs can freeze server worker threads.
3. **Regular Expression Denial of Service (ReDoS)**: Python's standard `re` module uses backtracking with exponential worst-case time complexity, allowing polynomial or exponential CPU starvation attacks.

## Decision
We implemented a **Constrained Tree-Walking AST Evaluator (Safe JSONLogic AST)** combined with linear-time regex evaluation via `google-re2`.

1. **Strict Operator Whitelist**: The interpreter only evaluates explicit mathematical and logical operators (`==`, `!=`, `<`, `<=`, `>`, `>=`, `and`, `or`, `!`, `var`, `in`). No built-ins, module imports, or code evaluation primitives exist.
2. **Bounded Depth & Complexity**:
   - Maximum recursion depth: $\le 10$
   - Maximum node count: $\le 50$
   Any construct exceeding these boundaries is rejected with `RuleEvaluationError`.
3. **Zero Python Eval**: Traversal is implemented entirely via an explicit Python recursive function that inspects dict and list structures.
4. **Linear-Time Regex Protection**: All tenant-supplied validation patterns are checked using `google-re2` (compiled in C++), enforcing $O(n)$ matching time, max pattern length of 100 characters, and outright rejecting unsafe backtracking features (such as backreferences or lookarounds).

## Consequences
- **Positive**:
  - Eliminates RCE vulnerabilities by design.
  - Complete immunity to ReDoS CPU exhaustion attacks.
  - Predictable sub-millisecond execution times.
- **Negative**:
  - Expressiveness is limited to propositional logic and arithmetic comparisons. Complex imperative scripts or external network calls inside rule guards are disallowed (which is a desired safety constraint in multi-tenant environments).
