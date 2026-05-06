# NORMATIVE_RULES

## Purpose

This document defines normative enforcement semantics for repository behavior under SCI governance.
This document is subordinate to SYSTEM_CONTRACT_INDEX.md.
If any conflict exists, SYSTEM_CONTRACT_INDEX.md is authoritative.

## 1. Definitions

### 1.1 Normative Rule

A normative rule is an enforceable system requirement that governs implementation, execution, and conflict handling.

Normative source constraint:

- A rule is normative only if it is explicitly defined in SYSTEM_CONTRACT_INDEX.md.

### 1.2 Non-Normative Content

Non-normative content includes architecture notes, design descriptions, examples, RFC text, and planning text that are not explicitly defined by SCI.

Non-normative source scope:

- All non-SCI documents are descriptive unless explicitly declared SCI-backed.

## 2. Binding Rules

1. Only SCI defines enforceable system behavior.
1. All other documents are descriptive unless explicitly declared SCI-backed.
1. Copilot and automation components MUST NOT infer rules from descriptive text.
1. Ambiguity MUST result in BLOCKED output and fail-closed behavior.

Key enforcement statement:

"If a rule is not explicitly defined in SCI, it does not exist."

## 3. Enforcement Semantics

1. If SCI is missing, execution and generation MUST stop.
1. If SCI is ambiguous, execution and generation MUST stop.
1. If SCI conflict is detected, execution and generation MUST stop.
1. Output under SCI conflict MUST be marked BLOCKED due to SCI conflict.
1. Resolution MUST be requested through SCI clarification or SCI update.

## 4. Interpretation Constraints

1. Copilot MUST treat SCI as the only executable source of truth.
1. Copilot MUST NOT reconcile conflicting subordinate text outside SCI.
1. Copilot MUST NOT invent authority, identity, mode, or conflict-resolution rules.
1. Copilot MUST preserve IP-only identity behavior defined by SCI.

## 5. Compliance Conditions

A change is compliant only when all conditions are true:

1. Every enforced behavior is directly traceable to SCI.
1. No rule is inferred from descriptive text.
1. No subordinate document overrides SCI semantics.
1. Unresolved ambiguity is blocked, not guessed.

## 6. Violation Outcome

Any violation of this document or SCI binding MUST trigger fail-closed handling:

1. Stop generation or runtime execution scope.
1. Mark status as BLOCKED.
1. Record violation event.
1. Require SCI clarification before continuation.
