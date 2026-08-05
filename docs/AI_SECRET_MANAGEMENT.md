# AI Secret Management

Secret abstraction lives in `backend/ai_secrets`.

Adapters:

- `environment.py`: local development environment variables
- `encrypted_file.py`: development-only encrypted local adapter
- `registry.py`: resolver and redacted metadata facade

Production should use a managed cloud secret manager. API responses expose only presence and rotation metadata, never secret values.
