# Sessions And Kill Zones

Session windows are configurable with:

- timezone
- start time
- end time
- weekdays
- applicable asset classes

Implemented defaults:

- Asian
- London
- New York morning
- New York afternoon

The implementation uses timezone-aware `zoneinfo`, so DST is handled by the configured timezone. Kill zones are treated as named time filters and reference windows, not signal generators.
