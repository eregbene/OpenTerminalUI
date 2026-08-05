from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class ModelPricing:
    provider: str
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal = Decimal("0")
    reasoning_per_million: Decimal = Decimal("0")
    currency: str = "USD"
    version: str = "2026-07-24.local"


class PricingTable:
    def __init__(self) -> None:
        self.version = os.getenv("AI_PRICING_VERSION", "2026-07-24.local")
        self._rows: dict[tuple[str, str], ModelPricing] = {}
        self.register(
            ModelPricing(
                provider="openai",
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                input_per_million=Decimal(os.getenv("OPENAI_INPUT_USD_PER_MTOK", "0.40")),
                output_per_million=Decimal(os.getenv("OPENAI_OUTPUT_USD_PER_MTOK", "1.60")),
                cached_input_per_million=Decimal(os.getenv("OPENAI_CACHED_INPUT_USD_PER_MTOK", "0.10")),
                reasoning_per_million=Decimal(os.getenv("OPENAI_REASONING_USD_PER_MTOK", "0")),
                version=self.version,
            )
        )
        self.register(ModelPricing(provider="local", model="local-grounded", input_per_million=Decimal("0"), output_per_million=Decimal("0"), version=self.version))

    def register(self, row: ModelPricing) -> None:
        self._rows[(row.provider.lower(), row.model.lower())] = row

    def get(self, provider: str, model: str) -> ModelPricing:
        key = (provider.lower(), model.lower())
        if key in self._rows:
            return self._rows[key]
        provider_default = next((row for (p, _), row in self._rows.items() if p == provider.lower()), None)
        return provider_default or ModelPricing(provider=provider, model=model, input_per_million=Decimal("0"), output_per_million=Decimal("0"), version=self.version)

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> tuple[str, str, str]:
        if min(input_tokens, output_tokens, cached_input_tokens, reasoning_tokens) < 0:
            raise ValueError("provider usage cannot be negative")
        row = self.get(provider, model)
        million = Decimal("1000000")
        uncached_input = max(0, input_tokens - cached_input_tokens)
        total = (
            Decimal(uncached_input) * row.input_per_million
            + Decimal(cached_input_tokens) * row.cached_input_per_million
            + Decimal(output_tokens) * row.output_per_million
            + Decimal(reasoning_tokens) * row.reasoning_per_million
        ) / million
        return str(total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)), row.currency, row.version


pricing_table = PricingTable()
