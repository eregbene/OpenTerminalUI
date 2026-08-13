"""Provider-neutral historical data sources. HistoricalDataProvider is the contract every
concrete provider (MT5HistoricalProvider, YahooHistoricalProvider, ...) implements; nothing
downstream of ingestion.py is allowed to assume "MT5" or "Yahoo" specifically."""
