# AGENTS.md

All agents working in this repository MUST follow this file.

## Most Important Rule
- No pipeline step is accepted until it is validated against an independent ground-truth-like check.
- A model, parser, OCR engine, extractor, or pipeline output is never its own ground truth. Comparing two outputs is useful only as a diagnostic signal.
- For OCR and document-image tasks, the closest practical ground truth is visual judgement of the same rendered page image or bbox crop image.
- If visual judgement or other independent evidence does not support the output, mark the step `needs_review`, `partial`, or `failed`, print the reason and raw evidence in the session, and suggest a generic next step before continuing.

## Communication
- During builds and long commands, print concise progress and summary information unless detailed output is needed for debugging or judgement.
- When printing file paths in the session, or saving in a variable, always use full absolute paths instead of paths relative to the project.
- Do not print a "Relevant Files" section in the session unless the current action is committing those files.
- Assume the user is not a software engineer and is not fully fluent in English; explain actions, findings, and tradeoffs in simple plain English, even if the explanation needs to be longer.
- If the user provides Hebrew text, treat it as context only and always answer in English.
- When the user writes `GIT` in uppercase, treat it as an instruction to commit and push the current code changes.
- Ask targeted follow-up questions whenever requirements, context, or implementation choices are unclear.

## Concurrent Agent Work
- Multiple agents or sessions may work in this same project at the same time.
- At the beginning of any substantial pipeline, experiment, report, data-processing, or code-change task, ask the user whether to continue an existing pipeline/run or start a new separate pipeline/run so the work does not interfere with other agents.
- Prefer separate run directories, output directories, logs, temporary files, databases, and report paths for new work unless the user explicitly asks to continue an existing pipeline.
- Before editing code, check the worktree and inspect the specific files that will be changed. Do not assume local changes were made by the current agent.
- Never overwrite, revert, reformat, delete, or clean files changed by another agent or the user unless the user explicitly asks.
- If another session changes a file currently being edited, reread the file before patching. If the changes conflict with the task, stop and ask the user how to proceed.
- Commit only the files intentionally changed for the current task. Do not include unrelated modified or untracked files.

## Other Project Boundaries
- Different projects may be solving the same problem with different agents or approaches.
- Agents may read files in other projects when needed for comparison or research, but must treat other projects as read-only by default.
- Never modify, create, delete, format, commit, push, or run write operations in another project unless the user specifically asks for that project to be changed.
- If modifying another project is requested, first confirm the exact absolute project path, the intended files or scope, and why the change belongs there instead of this project.

## Project Goal
Build a research-grade MVP for ingesting municipality protocol documents and attachments.
Do not assume fixed document structure, wording, language, schema, or municipality-specific rules.

## Solution Principles
- Prefer generic, reusable solutions over logic tailored to one document, municipality, wording pattern, or fixed layout.
- Before planning or implementing, challenge the first narrow or non-generic idea and prefer a more generic approach, even if it requires more planning, more model runs, more execution time, or higher cost.
- Do not rush when proposing plans, solutions, or fixes; think carefully about each problem, compare plausible approaches, and provide the highest-quality generic recommendation even if it takes longer.
- Do not optimize for saving tokens, tool calls, model runs, or execution time at the expense of solution quality.
- Do not make correctness depend on semantic-specific keywords, fixed wording, or municipality-specific labels; if this seems necessary, clearly notify the user and ask for permission before proceeding.
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
- After classification, retrieval, or evaluation runs, print a concise quality summary and, when the result is meant for user review, save the full quality report as HTML.
- Always explain input and output and show raw input text for every run.
- Always judge each result as if you were a human judge; if it is not acceptable, explain the mistake source and suggest a generic fix.
- For every processing step, validate the output against the best available independent evidence before accepting it. For PDF/OCR/layout work, check the rendered page image or bbox crop visually; do not accept a result only because two text outputs agree.
- For document or file processing, do not run multiple files silently; process one file at a time and show raw evidence and judgement after each file unless the user explicitly approved a full batch.
- When testing new logic or fixes, choose a broad representative set across municipalities, document types, and edge cases; unless the user approves a full batch, run examples one at a time from hardest to easiest and show the result before continuing.
- For any non-success state/status such as `failed`, `blocked`, `skipped`, `warning`, `needs_review`, `partial`, or `not_accepted`, print the reason, raw evidence or report path, and suggested generic next step directly in the session. Do not require `reason` or `suggested_solution` fields inside JSON report files unless the user explicitly asks for machine-readable reporting.
- Prefer putting the important quality judgement in the session immediately: status, reason, raw evidence snippets, paths to key artifacts, human judgement, and next step. Keep internal machine-readable report files concise; make user-review HTML reports complete enough to judge the result without hunting for separate visual evidence.
- For UI work, restart any affected local server, verify the correct port, and check the desired result with globally installed Playwright.
- After database changes, restart any affected local server yourself and verify the correct port.
- Run experiments and long commands visibly in the active session, monitor them until they finish, and report concise progress with the raw inputs, outputs, warnings, and failures needed to judge the result.
- For long runs expected to take more than one hour, save incremental results to durable per-file or per-example output files as the run progresses, not only at the end.
- If required verification cannot be completed, stop and report the reason, raw evidence, and generic next step.

## User-Review Report Outputs
- Any report meant for the user to review must use HTML as the primary report format.
- User-review HTML reports must include the visual details needed to judge the result conveniently, such as rendered page images, bbox crop images, screenshots, visual overlays, tables, status colors, raw input text, pipeline/model outputs, assistant judgement, failure reasons, and links to key artifacts using full absolute paths.
- JSON, JSONL, CSV, Markdown, or plain-text reports may still be generated for internal processing, automated tests, or companion machine-readable artifacts, but they must not be the only user-review report unless the user explicitly asks for that format.
- In the session, print a concise summary and the full absolute path to the user-review HTML report.
