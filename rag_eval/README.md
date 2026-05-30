# Hebrew Municipal RAG Eval Harness

This harness compares local Ollama Hebrew LLMs on real municipal protocol RAG behavior. Codex builds/runs the harness; it is not used as the judge.

## What Is Compared

Each model is tested against the same indexed documents, same retrieval settings, and same eval rows. The variable is the answer model sent to `/ask` as `model_name`.

Default models:

```text
qwen3.5:122b
hrbrmstr/jamba:latest
dicta-il/DictaLM-3.0-24B-Thinking:bf16
```

The report separates retrieval quality from LLM answer quality. If `retrieval_hit_at_5` is low, chunking/indexing/retrieval is the problem. If retrieval is high but answer/citation scores are low, the answer model is the problem.

## Eval Tasks

Use `task_type` to distinguish what each row tests:

```text
qa                    factual municipal QA from protocol chunks
citation              answer plus exact evidence citation
unanswerable          refusal when the documents do not contain the answer
contradiction         detection of conflicting evidence
```

The current generator produces `qa` and `unanswerable` rows automatically. You can add decision/topic/citation/contradiction rows manually or with a later specialized generator using the same JSONL schema.

## Eval Row Schema

Minimum row:

```json
{
  "id": "eval_0001",
  "task_type": "qa",
  "question_he": "מה החליטה הוועדה לגבי הבקשה ברחוב הרצל 12?",
  "expected_answer_he": "הוועדה החליטה לאשר את הבקשה בתנאים.",
  "source_doc_id": "483",
  "source_chunk_ids": ["fb96c070dcdbc59378f82eccc0167156743f00a1"],
  "required_quote_he": "הוועדה מחליטה לאשר את הבקשה בכפוף לתנאים",
  "answer_type": "decision",
  "difficulty": "medium",
  "is_unanswerable": false
}
```

For topic rows, keep the same fields and put the expected taxonomy in `expected_answer_he`, for example:

```json
{
  "id": "topic_0001",
  "task_type": "topic_classification",
  "question_he": "מה נושא האב ונושא המשנה המתאימים להחלטה על הצבת תמרורים מוארים ליד בית הספר?",
  "expected_answer_he": "נושא אב: תחבורה ובטיחות; נושא משנה: בטיחות בדרכים.",
  "source_doc_id": "483",
  "source_chunk_ids": ["chunk_id"],
  "required_quote_he": "הצבת תמרורים מוארים ליד בית הספר",
  "answer_type": "summary",
  "difficulty": "medium",
  "is_unanswerable": false
}
```

For decision extraction rows, expected answer should list all required decisions and the required quote should be one exact decision phrase from the chunk.

## Build The Eval Set

1. Put real protocol PDFs here:

```bash
rag_eval/data/raw_docs/
```

2. Import and index real protocols into `municipality.db`:

```bash
python rag_eval/scripts/import_protocols.py --city ashdod --input rag_eval/data/raw_docs --limit 20
```

This runs the existing extraction/indexing pipeline. The eval must use indexed chunk IDs, otherwise retrieval/citation metrics cannot be trusted.

3. Export indexed protocol chunks:

```bash
python rag_eval/scripts/extract_chunks.py --source db --source-kind protocol
```

Output:

```text
rag_eval/data/chunks.jsonl
```

4. Generate candidate QA and unanswerable rows with local Ollama:

```bash
python rag_eval/scripts/generate_eval_set.py \
  --generator-model qwen3.5:122b \
  --target-count 300 \
  --final-count 200
```

Output:

```text
rag_eval/data/eval_set.jsonl
```

5. Validate the eval rows before running the benchmark:

```bash
python rag_eval/scripts/validate_eval_set.py
```

This performs deterministic checks only: source chunks exist, `required_quote_he` is found in the source chunk, answerable rows have evidence, and unanswerable rows do not carry a required quote. Quote matching is exact first, then whitespace-normalized. It does not use an LLM to decide whether a quote exists.

For semantic validation of `expected_answer_he`, use a local judge model:

```bash
python rag_eval/scripts/validate_eval_set.py \
  --llm-validate \
  --validator-model qwen3.5:122b
```

Outputs:

```text
rag_eval/data/eval_set.validated.jsonl
rag_eval/data/eval_set.validation_report.jsonl
```

6. Manually review a sample before running the benchmark. JSONL is one object per line, so inspect 30-50 rows, especially decision and unanswerable cases. Treat `auto_generated` rows as development data, `llm_validated` rows as internal comparison data, and `human_verified` rows as gold benchmark data.

## Run The Comparison

1. Start Ollama and make sure the models are available:

```bash
ollama list
```

2. Start the API server:

```bash
scripts/run_ask_server.sh
```

3. Run one model at a time against the same eval set. This is the default workflow when memory cannot hold multiple local models:

```bash
python rag_eval/scripts/run_rag_eval.py \
  --eval-set rag_eval/data/eval_set.validated.jsonl \
  --models 'qwen3.5:122b' \
  --resume
```

Unload or stop that model if needed, then run the next model later:

```bash
python rag_eval/scripts/run_rag_eval.py \
  --eval-set rag_eval/data/eval_set.validated.jsonl \
  --models 'hrbrmstr/jamba:latest' \
  --resume
```

And then:

```bash
python rag_eval/scripts/run_rag_eval.py \
  --eval-set rag_eval/data/eval_set.validated.jsonl \
  --models 'dicta-il/DictaLM-3.0-24B-Thinking:bf16' \
  --resume
```

The report combines all model output files under `rag_eval/runs/`, so models do not need to run at the same time.

If memory is not a constraint, you can still run all models sequentially in one command:

```bash
python rag_eval/scripts/run_rag_eval.py \
  --models 'qwen3.5:122b,hrbrmstr/jamba:latest,dicta-il/DictaLM-3.0-24B-Thinking:bf16'
```

Outputs:

```text
rag_eval/runs/qwen3.5__122b.jsonl
rag_eval/runs/hrbrmstr__jamba__latest.jsonl
rag_eval/runs/dicta-il__DictaLM-3.0-24B-Thinking__bf16.jsonl
```

Each row stores answer, citations, retrieved chunk IDs, latency, and raw API responses.

## Grade And Report

Grade with local Ollama judge:

```bash
python rag_eval/scripts/grade_eval.py --judge-model qwen3.5:122b
```

If you benchmarked `eval_set.validated.jsonl`, grade that same file:

```bash
python rag_eval/scripts/grade_eval.py \
  --eval-set rag_eval/data/eval_set.validated.jsonl \
  --judge-model qwen3.5:122b
```

For a fast deterministic-only smoke test:

```bash
python rag_eval/scripts/grade_eval.py --skip-llm-judge
```

Build CSV and HTML leaderboard:

```bash
python rag_eval/scripts/report.py
```

Outputs:

```text
rag_eval/results/results.csv
rag_eval/results/report.html
rag_eval/results/graded.jsonl
```

Open `rag_eval/results/report.html` to view:

```text
overall model leaderboard
breakdown by task_type
breakdown by answer_type
all individual answers with filters
judge reasons and failure cases
```

## Metrics

Answerable rows:

```text
30% answer correctness
25% groundedness
20% citation correctness
15% retrieval hit@5
10% no hallucination
```

Unanswerable rows:

```text
70% correctly says not enough information
30% does not invent facts
```

Decision extraction rows should be judged for missing decisions and invented decisions. Topic classification rows should be judged for root topic, child topic, and whether the topic is grounded rather than generic or over-specific.

## How To Interpret Results

Use this diagnostic logic:

```text
low retrieval_hit_at_5 across all models
  => indexing/chunking/retrieval problem

high retrieval_hit_at_5 but low answer score for one model
  => model answer quality problem

high answer score but low citation score
  => dangerous citation discipline problem

low unanswerable score
  => model hallucinates when documents are insufficient

low topic_classification score
  => model is not preserving municipal taxonomy
```
