# Dealing Ranges

A dealing range is created from the latest confirmed swing high and swing low.

Stored fields include:

- range high
- range low
- equilibrium
- source swing IDs
- activation/confirmation time
- normalized current position

Premium, equilibrium, discount and OTE zones are derived from the range. The default OTE band is configurable and set to `0.62` to `0.79`.
