# BSI Faiz Trendline Audit

Sources: complete local transcript search plus visual inspection of target strategy sheets.

## Finding

Faiz explicitly teaches trendline liquidity as a liquidity form inside Order Flow. A standalone named Trendline Trading Strategy was not found in the local course.

## Evidence

Transcript search found trendline language only in `2. Trading Strategies/1. Order Flow Trading Strategy.txt`, where Faiz says liquidity comes in retail trading patterns and identifies a trendline as liquidity.

Visual support: Order Flow material includes diagonal line liquidity. The requested strategy/example videos do not establish a separate trendline strategy.

## Rule Status

- Trendline liquidity: `SPOKEN_AND_VISUAL`.
- Standalone trendline strategy: `NOT_ESTABLISHED`.
- Separate `bsi_trendline` subtype: do not create.

## Anchor Rules

Anchor selection is not fully specified in the local course. Current safe interpretation:

- use obvious swing highs/lows as diagonal liquidity anchors,
- require at least two respected points,
- treat additional touches as stronger but not proven mandatory,
- trendline liquidity is taken when price breaks/sweeps the diagonal liquidity line,
- post-sweep entry still belongs to the consuming strategy, usually Order Flow after MSS/MSB with FVG/OB entry.

## Consuming BSI Strategies

Confirmed consumer: Order Flow.

Possible consumers: ABC, Reactionary, OB Liquidity when their own full sequence also exists. This is `VISUAL_INFERENCE`, not a standalone rule.
