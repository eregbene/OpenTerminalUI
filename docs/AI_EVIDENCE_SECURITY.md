# AI Evidence Security

Evidence bundles are read-only, bounded, redacted and owner-scoped.

Persisted ownership metadata:

- `owner_user_id`
- `authorized_account_ids`
- `authorized_research_scope`
- `created_at`
- `expires_at`

Bundle retrieval re-authenticates the caller and verifies owner or admin scope. Bundle ids are not public links.

Limits: 12 evidence items, depth 6, array/object fanout 100, 2,000-character strings, 512 KB bundle size and 256 KB API response size.

Adapters use per-entity allow-lists and centralized redaction before evidence reaches API responses or audits.
