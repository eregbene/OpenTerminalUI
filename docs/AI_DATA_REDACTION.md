# AI Data Redaction

Central redaction lives in `backend/ai_assistant/security.py`.

Sensitive key patterns: `password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `connection_string`, `private_key`.

Redaction applies to evidence dictionaries, adapter warnings, audit payloads, API responses and safe error payloads. Evidence also uses per-entity field allow-lists so unrelated stored metadata is not serialized.
