# Forex Framework Testing

FX-3 adds focused backend and frontend coverage:

- framework registry contract
- signal schema contract
- unsupported symbol behavior
- deterministic execution order
- confluence and thesis generation
- persistence round trip
- XAU/USD metadata and proxy behavior
- frontend forex page framework panel and XAU/USD selection

Useful commands:

```bash
python -m compileall -q backend/forex_intelligence backend/forex_frameworks backend/market_structure backend/api/routes
python -m pytest backend/tests/test_forex.py backend/tests/test_forex_intelligence.py backend/tests/test_forex_frameworks.py backend/tests/test_forex_framework_contract.py backend/tests/test_forex_framework_confluence.py backend/tests/test_xauusd.py -q
npm.cmd test -- ForexPage.test.tsx --run
npm.cmd run build
```
