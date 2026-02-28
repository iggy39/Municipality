# Milestone M5 - Geo Step 5 (High-Confidence Mapping)

Goal: classify geo scope, extract mentions, generate candidates without API keys, score links conservatively, and expose map features with strict confidence defaults.

## Exit Gate

- [ ] `geo_scope` classification is implemented (`NONE`, `CITYWIDE`, `AREA`, `SITE`, `MULTI`).
- [ ] Mention extraction for key Hebrew patterns is implemented.
- [ ] No-key candidate generation path is operational.
- [ ] Confidence scoring and thresholds are enforced.
- [ ] `geo_scope=NONE` never renders geometry.
- [ ] Map API/UI defaults to high-confidence features only.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M5-T01 | Geo scope classifier | High | M4-T10 |
| M5-T02 | Hebrew geo mention extractor | High | M5-T01 |
| M5-T03 | No-key candidate generation backend | High | M5-T02 |
| M5-T04 | Confidence scoring and reason codes | High | M5-T03 |
| M5-T05 | Geo persistence schema and services | High | M5-T04 |
| M5-T06 | Map features API with `min_conf` filter | High | M5-T05 |
| M5-T07 | Map view UI with confidence controls | Medium | M5-T06 |
| M5-T08 | Guardrail tests for `NONE` and low confidence | High | M5-T07 |
| M5-T09 | Geo eval dataset and precision report | High | M5-T08 |
| M5-T10 | M5 QA report and signoff | High | M5-T09 |

---

## Tickets

### M5-T01 - Geo scope classifier
Checklist:
- [ ] Implement deterministic scope rules.
- [ ] Support `NONE`, `CITYWIDE`, `AREA`, `SITE`, `MULTI` outputs.
- [ ] Persist scope with decision records.

Done when:
- [ ] Scope classification is available for all eligible decisions.

### M5-T02 - Hebrew geo mention extractor
Checklist:
- [ ] Add regex for cadastre patterns.
- [ ] Add regex for street/address patterns.
- [ ] Add regex for quarter/area references.
- [ ] Persist mention spans with source citations.

Done when:
- [ ] Mention extraction catches expected patterns on labeled samples.

### M5-T03 - No-key candidate generation backend
Checklist:
- [ ] Implement candidate generation without GovMap/MAPI keys.
- [ ] Use available open/local datasets for fallback geocoding where possible.
- [ ] Mark unresolved mentions clearly without forced mapping.

Done when:
- [ ] Candidate generation works without any external API key.

### M5-T04 - Confidence scoring and reason codes
Checklist:
- [ ] Implement scoring rubric by evidence type.
- [ ] Add consistency penalties (municipality bounds, area mismatch).
- [ ] Persist reason codes for audit/debug.
- [ ] Enforce thresholds (`>=0.85` map by default).

Done when:
- [ ] Scoring output is explainable and reproducible.

### M5-T05 - Geo persistence schema and services
Checklist:
- [ ] Persist mentions, links, summaries, and feature cache records.
- [ ] Ensure links keep provenance and confidence details.
- [ ] Add update semantics for reruns.

Done when:
- [ ] Geo data survives reruns without duplicate pollution.

### M5-T06 - Map features API with `min_conf` filter
Checklist:
- [ ] Implement `GET /map/features` with filters.
- [ ] Return only high-confidence features by default.
- [ ] Return source evidence references per feature.

Done when:
- [ ] API supports confidence-aware exploration and auditing.

### M5-T07 - Map view UI with confidence controls
Checklist:
- [ ] Add map layer for high-confidence geometry.
- [ ] Add optional toggle for uncertain features.
- [ ] Add evidence panel for selected feature.

Done when:
- [ ] Map UI clearly separates confident vs uncertain evidence.

### M5-T08 - Guardrail tests for `NONE` and low confidence
Checklist:
- [ ] Add test cases where mapping must not occur.
- [ ] Add tests for confidence threshold boundary conditions.
- [ ] Add regression tests for previously false-mapped items.

Done when:
- [ ] `geo_scope=NONE` false-map rate is near zero in tests.

### M5-T09 - Geo eval dataset and precision report
Checklist:
- [ ] Build labeled geo sample set.
- [ ] Compute precision at confidence threshold.
- [ ] Report coverage and ambiguous case handling.

Done when:
- [ ] Geo metrics pass milestone target.

### M5-T10 - M5 QA report and signoff
Checklist:
- [ ] Create `eval/reports/m5_report.md`.
- [ ] Include precision, coverage, and false-map metrics.
- [ ] Include unresolved no-key limitations and next steps.

Done when:
- [ ] M5 signoff completed and M6 can start.
