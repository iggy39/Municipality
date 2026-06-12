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
- Ask targeted follow-up questions whenever requirements, context, or implementation choices are unclear.

## Project Goal
Build a research-grade MVP for ingesting municipality protocol documents and attachments.
Do not assume fixed document structure, wording, language, schema, or municipality-specific rules.

## Solution Principles
- Prefer generic, reusable solutions over logic tailored to one document, municipality, or text pattern.
- Do not hardcode assumptions about protocol layout, terminology, metadata, or attachment structure.
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
- Act as a judge: run or demonstrate the feature with representative input and print concise input/output evidence to the console.
- Keep verification output concise unless debugging requires more detail.
