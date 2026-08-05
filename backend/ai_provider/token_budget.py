from __future__ import annotations

from dataclasses import dataclass


MAX_PROMPT_CHARS = 24_000
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class TokenBudget:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    estimated_cost: float
    budget_remaining: int


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def enforce_prompt_budget(prompt: str, *, max_prompt_tokens: int = 6_000) -> TokenBudget:
    prompt_tokens = estimate_tokens(prompt)
    if len(prompt) > MAX_PROMPT_CHARS or prompt_tokens > max_prompt_tokens:
        raise ValueError("prompt exceeds token budget")
    return TokenBudget(
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        cached_tokens=0,
        estimated_cost=0.0,
        budget_remaining=max(0, max_prompt_tokens - prompt_tokens),
    )
