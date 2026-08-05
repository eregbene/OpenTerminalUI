# Strategy Testing

Phase 6 focused tests:

- backend strategy registry
- reference strategy proposal generation
- data-quality policy blocking
- unsafe/unknown feature namespace rejection
- feature-to-feature condition comparison
- frontend strategy inspector rendering

Commands:

```powershell
python -m pytest backend/tests/test_strategy_engine_phase6.py -q
npm.cmd test -- StrategyDecisionPanel.test.tsx --run
```
