# AI Grounding Rules

The AI assistant can explain only retrieved deterministic evidence.

Rules:

- Use adapter evidence before caller-supplied fallback evidence.
- Never invent entity fields or missing relationships.
- Include freshness and quality limitations in the response.
- Treat `MISSING` and `INVALID` evidence as unsupported.
- Keep all AI assistant operations read-only.
- Do not call shell commands, broker services, state-changing workflows, or unrestricted HTTP from tools.
- Use stable content hashes when a business version is absent.

Current explanations are deterministic summaries. LLM summarization is intentionally not implemented in this phase.
## Security Grounding

Request/response bounds, redaction, field allow-lists and owner-scoped evidence bundle retrieval are required before any future LLM summarization layer can consume evidence.

## Phase 9D Output Validation

LLM output is accepted only after validation against the evidence bundle. Unsupported claims or action language are replaced with `I don't have sufficient verified evidence to answer.`
