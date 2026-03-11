# Ops FAQ (MVP)

## How do I run the system?
- Activate venv: `source .venv/bin/activate`
- Run: `PYTHONPATH=. python src/main.py`

## Where are outputs saved?
- `artifacts/` contains stage outputs and final decisions.

## What does "REVIEW_REQUIRED" mean?
- Execution is **fail-closed** until an approval decision is provided.

## I see "approve_for_build" but still have issues. Is this OK?
- Yes. It means **no high severity blockers** for MVP.
- Medium/low items are tracked as backlog for hardening.

## How do I troubleshoot import errors?
- Use `python -m pytest -q`
- Ensure `PYTHONPATH=.` when running from deploy environments.

## How do I verify audit integrity?
- Run integrity verification (if available) or compare hash-chain segments.
