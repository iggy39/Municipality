# AGENTS.md

All agents working in this repository MUST follow this file.

## Communication
- Print minimal information unless the user explicitly asks for more detail.
- Always show estimated build/execution time before starting implementation or running commands.
- During builds, print only concise progress and summary information unless the user explicitly asks for detailed output.
- Prefer concise status updates, concise findings, and concise verification output.
- When presenting cases, examples, predictions, failures, or quality reports in the session, include the raw source text, not only summaries or labels.
- When printing file paths in the session, always use full absolute paths instead of paths relative to the project.
- Do not print a "Relevant Files" section in the session unless the current action is committing those files.
- Assume the user is not a software engineer and is not fully fluent in English; explain actions, findings, and tradeoffs in simple plain English, even if the explanation needs to be longer.
- If the user provides Hebrew text, treat it as context only and always answer in English.
- When the user writes `GIT` in uppercase, treat it as an instruction to commit and push the current code changes.
- Ask targeted follow-up questions whenever requirements, context, or implementation choices are unclear.

## Project Goal
Build a research-grade MVP for ingesting municipality protocol documents and attachments.
Do not assume fixed document structure, wording, language, schema, or municipality-specific rules.

## Solution Principles
- Prefer generic, reusable solutions over logic tailored to one document, municipality, or text pattern.
- When planning or implementing, challenge the first non-generic solution instinct and prefer a more generic approach, even if it requires more planning, more execution time, additional model runs, or higher cost.
- Do not rush when proposing plans, solutions, or fixes; think carefully about each problem, compare plausible approaches, and provide the highest-quality generic recommendation even if it takes longer.
- Do not optimize for saving tokens, tool calls, model runs, or execution time at the expense of solution quality; use the effort needed for high-quality planning and generic, research-based solutions that deliver the highest-quality practical result.
- Do not hardcode assumptions about protocol layout, terminology, metadata, or attachment structure.
- Do not build solutions that depend on semantic-specific keywords or fixed wording; if this seems necessary, clearly notify the user and ask for permission before proceeding.
- When working on UI maps, render selected polygons such as neighborhoods through GovMap `displayGeometries` instead of dashboard HTML/SVG overlays above the map.
- Add helpful comments when writing code, especially where intent, assumptions, or non-obvious behavior need clarification.
- If unsure, pause and ask the user. If documentation or best practices are needed, research them before proceeding.

## AI Model Preferences
- Prefer local Ollama models for AI tasks.
- Ollama can run multiple instances of the same model when parallel model work is useful.
- Use `qwen3.5:122b` for general AI tasks.
- Use `dictaLM` for Hebrew-language tasks.
- Use `mistral-small3.1` for vision tasks.

## Verification
- Before implementation, present concise verification options appropriate to the task and ask the user to choose when non-obvious.
- Verify each stage before proceeding to the next.
- Tests alone are not enough.
- For non-trivial implementation, verification, or commit work, use the Codex agent harness at `scripts/codex_agent_harness.py` to enforce representative testing, sequential example validation, raw-text quality reports, UI verification, server checks, prediction summaries, and commit safety.
- Act as a judge: run or demonstrate the feature with representative input and print concise input/output evidence to the console.
- Keep verification output concise unless debugging requires more detail.

## End-of-Run Quality Reports
- After ingestion, import, extraction, classification, retrieval, or evaluation runs, print a concise quality report with raw source text for every warning, failure, representative sample, and judged row.
- For judged rows, include pipeline/model predictions, assistant judgement, agreement or disagreement reason, and relevant final artifact metadata.
