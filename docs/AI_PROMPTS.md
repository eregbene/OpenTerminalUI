# AI Prompts

Prompt construction lives in `backend/ai_provider/prompt_builder.py`.

Each prompt contains:

- fixed Bensim Trading system identity
- grounding rules
- evidence summary
- evidence details
- deterministic explanation, when available
- user request
- expected citation format

Prompts must not include unrestricted application context. They are assembled from bounded, redacted evidence payloads.

