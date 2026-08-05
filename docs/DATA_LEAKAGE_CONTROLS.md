# Data Leakage Controls

Implemented safeguards:

- resampling excludes incomplete bars by default
- canonical bars explicitly mark `is_complete`
- provider timestamps and receive timestamps are separate
- snapshot metadata records adjustment mode and validation policy
- candle validation can flag incomplete bars, timestamp drift and possible corporate-action discontinuities

Remaining audit items:

- effective-date tracking for revised fundamentals/economics
- historical index constituent membership
- provider-adjusted data effective dates
- survivorship-bias controls for universe selection
