from __future__ import annotations

import argparse
import csv
import html
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .common import RAG_EVAL_ROOT, as_bool, as_float, read_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, as_bool, as_float, read_jsonl


SUMMARY_COLUMNS = [
    "scope",
    "model",
    "task_type",
    "answer_type",
    "n",
    "total_score_pct",
    "retrieval_hit_at_5_pct",
    "answer_score_0_5",
    "citation_score_pct",
    "groundedness_0_5",
    "answer_completeness_0_5",
    "hallucination_rate_pct",
    "abstention_unanswerable_pct",
    "avg_latency_seconds",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create CSV and HTML leaderboard for local RAG eval results")
    parser.add_argument("--graded", default=str(RAG_EVAL_ROOT / "results" / "graded.jsonl"))
    parser.add_argument("--results-csv", default=str(RAG_EVAL_ROOT / "results" / "results.csv"))
    parser.add_argument("--report-html", default=str(RAG_EVAL_ROOT / "results" / "report.html"))
    args = parser.parse_args()

    rows = read_jsonl(Path(args.graded))
    if not rows:
        raise SystemExit("no graded rows found; run grade_eval.py first")

    summary_rows = _summary_rows(rows)
    _write_csv(Path(args.results_csv), summary_rows)
    _write_html(Path(args.report_html), rows, summary_rows)
    print(f"wrote {args.results_csv} and {args.report_html}")
    return 0


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_model_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        model = str(row.get("model_name") or "unknown")
        task_type = str(row.get("task_type") or row.get("answer_type") or "unknown")
        answer_type = str(row.get("answer_type") or "unknown")
        by_model[model].append(row)
        by_model_task[(model, task_type)].append(row)
        by_model_type[(model, answer_type)].append(row)

    for model, group in sorted(by_model.items()):
        out.append(_aggregate(scope="overall", model=model, task_type="", answer_type="", rows=group))
    for (model, task_type), group in sorted(by_model_task.items()):
        out.append(_aggregate(scope="task_type", model=model, task_type=task_type, answer_type="", rows=group))
    for (model, answer_type), group in sorted(by_model_type.items()):
        out.append(_aggregate(scope="answer_type", model=model, task_type="", answer_type=answer_type, rows=group))
    return out


def _aggregate(*, scope: str, model: str, task_type: str, answer_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    return {
        "scope": scope,
        "model": model,
        "task_type": task_type,
        "answer_type": answer_type,
        "n": total,
        "total_score_pct": _pct(_avg_metric(rows, "final_score")),
        "retrieval_hit_at_5_pct": _pct(_avg_metric(rows, "retrieval_hit_at_5")),
        "answer_score_0_5": round(_avg_metric(rows, "answer_correctness") * 5.0, 3),
        "citation_score_pct": _pct(_avg_metric(rows, "citation_correctness")),
        "groundedness_0_5": round(_avg_metric(rows, "groundedness") * 5.0, 3),
        "answer_completeness_0_5": round(_avg_metric(rows, "answer_completeness") * 5.0, 3),
        "hallucination_rate_pct": _pct(1.0 - _avg_metric(rows, "no_hallucination")),
        "abstention_unanswerable_pct": _pct(_avg_unanswerable_abstention(rows)),
        "avg_latency_seconds": round(_avg_metric(rows, "latency_seconds"), 3),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


def _write_html(path: Path, rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    models = sorted({str(row.get("model_name") or "unknown") for row in rows})
    answer_types = sorted({str(row.get("answer_type") or "unknown") for row in rows})
    summary_overall = [row for row in summary_rows if row["scope"] == "overall"]
    summary_tasks = [row for row in summary_rows if row["scope"] == "task_type"]
    summary_breakdown = [row for row in summary_rows if row["scope"] == "answer_type"]
    body = f"""
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Hebrew Municipal RAG Eval</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f7f3ea; color: #17202a; }}
    header {{ padding: 28px 34px; background: #17324d; color: white; }}
    main {{ padding: 24px 34px; }}
    h1, h2 {{ margin: 0 0 14px; }}
    section {{ margin: 0 0 28px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #e6e0d3; vertical-align: top; }}
    th {{ background: #efe7d7; text-align: right; position: sticky; top: 0; }}
    .ltr {{ direction: ltr; text-align: left; }}
    .answer {{ max-width: 460px; white-space: pre-wrap; }}
    .question {{ max-width: 360px; white-space: pre-wrap; }}
    .bad {{ background: #fff0f0; }}
    .mid {{ background: #fff9df; }}
    .good {{ background: #f0fff4; }}
    .controls {{ display: flex; gap: 12px; align-items: center; margin: 12px 0; flex-wrap: wrap; }}
    select, input {{ padding: 8px; border: 1px solid #b8ab95; border-radius: 6px; }}
    details {{ max-width: 520px; }}
  </style>
</head>
<body>
  <header>
    <h1>Hebrew Municipal RAG Eval Leaderboard</h1>
    <div>Models: {html.escape(', '.join(models))} | Rows: {len(rows)}</div>
  </header>
  <main>
    <section>
      <h2>Overall</h2>
      {_summary_table(summary_overall)}
    </section>
    <section>
      <h2>Breakdown By Answer Type</h2>
      {_summary_table(summary_breakdown)}
    </section>
    <section>
      <h2>Breakdown By Task Type</h2>
      {_summary_table(summary_tasks)}
    </section>
    <section>
      <h2>All Results</h2>
      <div class="controls">
        <label>Model <select id="model-filter"><option value="">All</option>{''.join(f'<option value="{html.escape(model)}">{html.escape(model)}</option>' for model in models)}</select></label>
        <label>Answer Type <select id="type-filter"><option value="">All</option>{''.join(f'<option value="{html.escape(answer_type)}">{html.escape(answer_type)}</option>' for answer_type in answer_types)}</select></label>
        <label>Search <input id="text-filter" placeholder="question / answer" /></label>
      </div>
      {_results_table(rows)}
    </section>
  </main>
  <script>
    const modelFilter = document.getElementById('model-filter');
    const typeFilter = document.getElementById('type-filter');
    const textFilter = document.getElementById('text-filter');
    function applyFilters() {{
      const model = modelFilter.value;
      const type = typeFilter.value;
      const text = textFilter.value.trim().toLowerCase();
      document.querySelectorAll('#all-results tbody tr').forEach(row => {{
        const okModel = !model || row.dataset.model === model;
        const okType = !type || row.dataset.type === type;
        const okText = !text || row.innerText.toLowerCase().includes(text);
        row.style.display = okModel && okType && okText ? '' : 'none';
      }});
    }}
    [modelFilter, typeFilter, textFilter].forEach(el => el.addEventListener('input', applyFilters));
  </script>
</body>
</html>
"""
    path.write_text(body, encoding="utf-8")


def _summary_table(rows: list[dict[str, Any]]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in SUMMARY_COLUMNS)
    body_rows = []
    for row in sorted(rows, key=lambda item: (-as_float(item.get("total_score_pct")), str(item.get("model")), str(item.get("answer_type")))):
        cells = "".join(f"<td class='{_cell_class(column)}'>{html.escape(str(row.get(column, '')))}</td>" for column in SUMMARY_COLUMNS)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _results_table(rows: list[dict[str, Any]]) -> str:
    headers = ["model", "eval_id", "task_type", "answer_type", "score", "retrieval", "citation", "hallucination", "latency", "question", "expected", "answer", "judge"]
    head = "".join(f"<th>{html.escape(label)}</th>" for label in headers)
    body_rows = []
    for row in sorted(rows, key=lambda item: (str(item.get("model_name")), as_float((item.get("metrics") or {}).get("final_score")))):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        judge = row.get("judge") if isinstance(row.get("judge"), dict) else {}
        score = as_float(metrics.get("final_score"))
        css = "good" if score >= 0.8 else "mid" if score >= 0.5 else "bad"
        model = str(row.get("model_name") or "unknown")
        task_type = str(row.get("task_type") or row.get("answer_type") or "unknown")
        answer_type = str(row.get("answer_type") or "unknown")
        cells = [
            _td(model, "ltr"),
            _td(row.get("eval_id"), "ltr"),
            _td(task_type, "ltr"),
            _td(answer_type, "ltr"),
            _td(f"{score * 100:.1f}%", "ltr"),
            _td(_yes_no(metrics.get("retrieval_hit_at_5")), "ltr"),
            _td(_yes_no(metrics.get("citation_correctness")), "ltr"),
            _td("yes" if not as_bool(metrics.get("no_hallucination")) else "no", "ltr"),
            _td(metrics.get("latency_seconds"), "ltr"),
            _td(row.get("question_he"), "question"),
            _td(row.get("expected_answer_he"), "answer"),
            _td(row.get("model_answer"), "answer"),
            _td(judge.get("reason_he"), "answer"),
        ]
        body_rows.append(
            f"<tr class='{css}' data-model='{html.escape(model)}' data-type='{html.escape(answer_type)}'>{''.join(cells)}</tr>"
        )
    return f"<table id='all-results'><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _avg_metric(rows: list[dict[str, Any]], key: str) -> float:
    values = []
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = metrics.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            values.append(float(value))
        else:
            values.append(as_float(value))
    return sum(values) / len(values) if values else 0.0


def _avg_unanswerable_abstention(rows: list[dict[str, Any]]) -> float:
    values = []
    for row in rows:
        if not as_bool(row.get("is_unanswerable")):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = metrics.get("abstention_when_unanswerable")
        if value is not None:
            values.append(float(as_bool(value)))
    return sum(values) / len(values) if values else 0.0


def _pct(value: float) -> float:
    return round(value * 100.0, 2)


def _td(value: Any, class_name: str = "") -> str:
    class_attr = f" class='{html.escape(class_name)}'" if class_name else ""
    return f"<td{class_attr}>{html.escape(str(value if value is not None else ''))}</td>"


def _cell_class(column: str) -> str:
    return "ltr" if column not in {"model", "answer_type"} else ""


def _yes_no(value: Any) -> str:
    return "yes" if as_bool(value) else "no"


if __name__ == "__main__":
    raise SystemExit(main())
