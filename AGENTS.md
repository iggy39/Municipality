# AGENTS.md

All agents working in this repository MUST follow this file.

## Goal
Build a research-grade MVP that ingests municipality protocol documents and attachments,

## Code Standards
- Keep imports clean: no missing, unresolved, or unused imports.

## Operating Rules

1. Build Mode Protocol
   - Assume the initial plan/architecture may be imperfect.
   - If context is ambiguous or missing, pause and ask the user targeted follow-up questions.
   - Do not make silent assumptions.

2. Handling Implementation Uncertainty
   - If unsure about the best implementation, state the uncertainty to the user first.
   - Only then (if needed) research best practices and proceed.

3. Plan Mode Clarification Depth
   - In Plan Mode, ask as many targeted follow-up questions as needed to remove ambiguity.
   - Do not proceed while any requirement remains vague.
