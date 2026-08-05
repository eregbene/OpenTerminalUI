# Strategy Rule Language

Rules are JSON-compatible trees made from `all`, `any`, `not`, and leaf conditions.

Allowed condition operators:

- `eq`, `neq`
- `gt`, `gte`, `lt`, `lte`
- `between`
- `in`, `not_in`
- `exists`
- `is_true`, `is_false`

Feature-to-feature comparisons are supported by using another feature name as the condition value.

Limits are enforced by `StrategyEngineLimits`:

- maximum rule nesting
- maximum conditions
- maximum lookback
- maximum feature/expression reference length

Unsupported expression execution is intentionally excluded.
