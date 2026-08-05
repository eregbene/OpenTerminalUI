from __future__ import annotations


def calculate_exposure(position_rows: list[dict]) -> dict:
  gross = 0.0
  net = 0.0
  by_strategy: dict[str, float] = {}
  by_instrument: dict[str, float] = {}
  by_asset_type: dict[str, float] = {}
  by_currency: dict[str, float] = {}
  for row in position_rows:
    value = float(row.get("market_value") or 0.0)
    gross += abs(value)
    net += value
    strategy = str(row.get("strategy_id") or "UNASSIGNED_STRATEGY")
    symbol = str(row.get("symbol") or "UNKNOWN")
    asset = str(row.get("asset_type") or "UNKNOWN")
    currency = str(row.get("currency") or "UNKNOWN")
    by_strategy[strategy] = by_strategy.get(strategy, 0.0) + value
    by_instrument[symbol] = by_instrument.get(symbol, 0.0) + value
    by_asset_type[asset] = by_asset_type.get(asset, 0.0) + value
    by_currency[currency] = by_currency.get(currency, 0.0) + value
  return {
    "gross_exposure": round(gross, 8),
    "net_exposure": round(net, 8),
    "long_exposure": round(sum(max(0.0, float(r.get("market_value") or 0.0)) for r in position_rows), 8),
    "short_exposure": round(sum(min(0.0, float(r.get("market_value") or 0.0)) for r in position_rows), 8),
    "by_strategy": by_strategy,
    "by_instrument": by_instrument,
    "by_asset_type": by_asset_type,
    "by_currency": by_currency,
  }
