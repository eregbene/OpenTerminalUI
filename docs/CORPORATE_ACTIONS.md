# Corporate Actions

Canonical model support was added through `CorporateAction`.

Historical data requests can declare adjustment mode in the capability contract. Supported labels are documented as:

- `raw`
- `split_adjusted`
- `total_return_adjusted`
- `provider_default`

No guessed split/dividend adjustments were implemented. Candle validation can flag possible unexplained discontinuities.
