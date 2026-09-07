# Faiz Updated Strategy Version Lineage

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

## Asian

- Base: `Asian Session Trading Strategy`
- Update: `ASIAN SESSION STRATEGY V2.0`
- Status: `REFINES / CURRENT_UPDATED_VERSION`
- Evidence: V2.0 says it uses FX and Asian range "just as the old one," but changes the trigger so it can work during early London, removes daily-bias/HTF-structure requirement, and uses M15 POI to M1 MSS execution.

## Order Flow

- Base: `Order Flow Trading Strategy`
- Update: `ORDERFLOW 101`
- Status: `REFINES`
- Evidence: ORDERFLOW 101 says the older method works, but the mentor now explains his personal A-to-Z method using H4/D1 trend, classic HTF OB, M15 confirmation, and consolidation avoidance.

## ABC

- Base: `ABC Trading Strategy`
- Updates: `Things To Avoid In ABC Strategy`, `ABC 101`, `ABC POI Combination`
- Status: `REFINES`
- Evidence: ABC 101 says the previous method works but the new material improves win rate using HTF trend, PD zone, POI, killzone, and timeframe discipline.

## Order Blocks

- Base: first candle of FVG sequence after MSS.
- Update: `Orderblock 2.0` adds classic last/consecutive opposite candles before structure break.
- Status: `COEXISTS / STRATEGY-SPECIFIC`
- Evidence: mentor says both work. Classic OB is used for Order Flow; first-candle-of-FVG remains used in other strategies such as Asian V2.
