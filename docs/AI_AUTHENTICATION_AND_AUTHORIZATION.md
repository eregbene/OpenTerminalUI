# AI Authentication And Authorization

AI routes use `get_current_user`.

Trusted fields: authenticated user id, authenticated role and server request metadata.

Ignored request-body fields: `user_id`, `role`, `account_ids`, `is_admin`, `authorization_scope`.

Admins can read all AI evidence. Non-admin users can read records they own by `owner_user_id` or `user_id`. Research records are scoped by owner where present. Unauthorized entity reads return generic not-found behavior where existence must not be disclosed.
## Phase 10 Addendum

Phase 10 adds provider-administration and autonomous-research surfaces without relaxing the existing authorization boundary.

- Provider configuration, provider health checks, usage aggregation, and budget inspection are authenticated API operations.
- Non-admin users receive user-scoped usage and budget views; admin users can inspect aggregate provider health and non-secret configuration status.
- Research Agent operations are tenant-scoped by `owner_user_id` and require active research policy context before execution.
- Research plan execution requires human approval unless the active policy explicitly marks approval as not required.
- The assistant and Research Agent remain prohibited from broker access, paper-account mutation, deployment approval, candidate promotion, risk approval, shell commands, filesystem mutation, and unrestricted HTTP.
