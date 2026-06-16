# AGENTS.md

All agents working in this repository MUST follow this file.

## Communication
- Print minimal information unless the user explicitly asks for more detail.
- Always show estimated build/execution time before starting implementation or running commands.
- During builds, print only concise progress and summary information unless the user explicitly asks for detailed output.
- Prefer concise status updates, concise findings, and concise verification output.
- When printing file paths in the session, always use full absolute paths instead of paths relative to the project.
- Do not print a "Relevant Files" section in the session unless the current action is committing those files.
- Assume the user is not a software engineer; explain actions, findings, and tradeoffs clearly in plain language.
- When the user writes `GIT` in uppercase, treat it as an instruction to commit and push the current code changes.
- Ask targeted follow-up questions whenever requirements, context, or implementation choices are unclear.

## Project Goal
Build a research-grade MVP for ingesting municipality protocol documents and attachments.
Do not assume fixed document structure, wording, language, schema, or municipality-specific rules.

## Solution Principles
- Prefer generic, reusable solutions over logic tailored to one document, municipality, or text pattern.
- When planning or implementing, challenge the first non-generic solution instinct and prefer a more generic approach, even if it requires more planning, more execution time, additional model runs, or higher cost.
- Do not hardcode assumptions about protocol layout, terminology, metadata, or attachment structure.
- Do not build solutions that depend on semantic-specific keywords or fixed wording; if this seems necessary, clearly notify the user and ask for permission before proceeding.
- Add helpful comments when writing code, especially where intent, assumptions, or non-obvious behavior need clarification.
- If unsure, pause and ask the user. If documentation or best practices are needed, research them before proceeding.

## AI Model Preferences
- Prefer local Ollama models for AI tasks.
- Use `qwen3.5:122b` for general AI tasks.
- Use `dictaLM` for Hebrew-language tasks.
- Use `mistral-small3.1` for vision tasks.

## Verification
- Before implementation, present concise verification options appropriate to the task and ask the user to choose when non-obvious.
- Verify each stage before proceeding to the next.
- Tests alone are not enough.
- UI bug fixes must be accompanied by Playwright verification.
- After UI or database changes, restart any affected local server yourself; do not wait for the user to ask.
- Act as a judge: run or demonstrate the feature with representative input and print concise input/output evidence to the console.
- When running a pipeline on documents, after each document print a table report with topic/subject identifier, real text, model prediction, ground truth from agent judgement, and the reason for success or failure.
- Keep verification output concise unless debugging requires more detail.
