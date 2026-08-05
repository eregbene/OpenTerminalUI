# AI Citations

Citations are generated from evidence bundles in `backend/ai_provider/citations.py`.

Citation labels map entity types to stable markers, for example:

- `paper_order` -> `[Order]`
- `risk_evaluation` -> `[Risk Evaluation]`
- `scorecard` -> `[Scorecard]`
- `candidate` -> `[Candidate]`
- `snapshot` -> `[Market Structure]`

If provider output omits a citation and evidence exists, the validator appends the first evidence citation. If no evidence exists, the answer is refused.

