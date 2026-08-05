# Forex Framework Confluence

Framework confluence is deterministic and read-only.

## Comparison

`ForexFrameworkService.compare()` summarizes:

- bullish framework count
- bearish framework count
- neutral framework count
- unknown framework count
- weighted bullish score
- weighted bearish score
- agreement ratio
- conflict ratio
- data quality score
- overlapping evidence

SMC, ICT, market-structure, support/resistance, and supply/demand signals may share objective structure and liquidity evidence. The comparison output calls this out instead of treating every framework as fully independent.

## Thesis

`ForexFrameworkService.thesis()` creates an analytical candidate only. It is not a trade instruction, strategy approval, risk approval, or order request.
