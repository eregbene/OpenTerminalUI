# Instrument Master

Implemented in `backend/market_data/instruments.py`.

The instrument master supports:

- canonical instrument ID
- display symbol
- provider symbol mappings
- asset class
- venue/exchange
- currency and contract metadata fields through canonical `Instrument`

It avoids assuming one ticker uniquely identifies an instrument. Paid identifier dependencies were not added.
