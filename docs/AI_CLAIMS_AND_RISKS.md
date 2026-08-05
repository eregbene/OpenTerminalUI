# AI Claims And Risks

| Surface | Claim Type | Source Data | Evidence Linkage | Risk |
|---|---|---|---|---|
| `/api/ai/research-brief` | decision-support summary | caller-supplied structured evidence | explicit evidence IDs | Low: deterministic only; still requires users to supply fresh/complete evidence. |
| `/api/ai/query` text answer | market explanation/general answer | LLM-generated classification/explanation | absent | User may treat generated text as fact without deterministic support. |
| `/api/ai/query` screener result | stock recommendation/shortlist | LLM-parsed filters plus screener route | partial, no evidence IDs | Filter interpretation may be wrong; explanation may overstate why names matched. |
| Agent Console standard mode | analysis/recommendation | LLM plus optional tools | tool events visible, durable evidence absent | Final synthesis can include unsupported claims. |
| Agent debate mode | bullish/bearish/PM decision framing | LLM role outputs | absent or weak | May look like investment advice or a decision. |
| Agent strategy mode | strategy research suggestion | LLM plus strategy tools | partial | Must remain draft/research-only; users could infer executable strategy approval. |
| Research autopilot | verdict/signal/confidence | deterministic services plus generated interpretation | mixed | Labels such as verdict/confidence need explicit semantics and freshness. |
| LLM insights cards | conclusion/risk/sentiment | provider/model output | generally absent | Generated insight can be mistaken for validated research. |
| News/sentiment summaries | market fact/sentiment | news/provider payload plus model or deterministic parser | partial | Freshness, source quality and fallback behavior are not consistently visible. |

## Risk Controls Required Before Broad AI

- keep `/api/ai/research-brief` as no-execution-authority decision support
- every answer must carry evidence IDs or explicitly state insufficient evidence
- deterministic comparison must happen before model summarization
- draft actions must never execute
- sensitive action requests must route to normal workflow guidance
- provider unavailability must produce deterministic fallback, not fake AI
- unsupported numeric/entity/causal claims must be detected or qualified
