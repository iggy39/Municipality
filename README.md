# Municipality

Implementation scaffold for Milestone M1 (discovery and versioned raw archive).

## Development

- Install dev dependencies: `pip install -e ".[dev]"`
- Enable git hooks once per clone: `python -m pre_commit install`
- Run all hooks manually: `python -m pre_commit run --all-files`

The pre-commit hook enforces the Hebrew direction guard (`tests/unit/test_hebrew_direction_guard.py`) to prevent reversed Hebrew text regressions.
