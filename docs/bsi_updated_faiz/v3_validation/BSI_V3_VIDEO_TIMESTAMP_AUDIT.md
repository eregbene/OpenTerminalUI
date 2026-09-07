# BSI V3 Video Timestamp Audit

Selected symbols: `EURUSD, XAUUSD`

Status counts: `{'VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE': 15, 'NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS': 8, 'OUT_OF_SCOPE_FOR_SELECTED_SYMBOLS': 3}`

This audit reads the actual local video manifest, SRT transcripts, contact sheets, and extracts source-video still frames at rule timestamps. It proves source timestamp evidence is available; exact visual overlap with the exported August OHLCV bars remains a separate detector-accuracy check.

| Strategy                        | Status                                 | Video                                      | First Timestamp | Frames |
| ------------------------------- | -------------------------------------- | ------------------------------------------ | --------------- | ------ |
| bsi_v3_order_flow               | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 1. Order Flow Trading Strategy.mp4         | 00:00:26,360    | 2      |
| bsi_v3_smt_divergence           | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_abc                      | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 10. ABC Trading Strategy.mp4               | 00:00:00,000    | 2      |
| bsi_v3_abcd                     | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 16. ABCD 101.mp4                           | 00:00:03,200    | 2      |
| bsi_v3_asian_v2                 | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_0930                     | OUT_OF_SCOPE_FOR_SELECTED_SYMBOLS      |                                            |                 | 0      |
| bsi_v3_reactionary_block        | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 17. Reactionary Block Trading Strategy.mp4 | 00:00:00,000    | 2      |
| bsi_v3_ict_silver_bullet        | OUT_OF_SCOPE_FOR_SELECTED_SYMBOLS      |                                            |                 | 0      |
| bsi_v3_silver_bullet_with_bias  | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_4h_order_block           | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 34. 4 Hour OB Trading Strategy.mp4         | 00:00:00,000    | 2      |
| bsi_v3_mmxm                     | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_mmxm_second_distribution | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 13. MMXM 2ND DISTRIBUTION ENTRY.mp4        | 00:00:06,960    | 2      |
| bsi_v3_holy_grail               | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 17. The Holy Grail.mp4                     | 00:00:00,000    | 2      |
| bsi_v3_juggernaut               | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_spectre                  | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 25. The Spectre Model.mp4                  | 00:00:34,400    | 2      |
| bsi_v3_monday_range             | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 37. Utilizing Monday Range.mp4             | 00:00:13,760    | 2      |
| bsi_v3_weaver                   | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 38. The Weaver Model.mp4                   | 00:02:20,000    | 2      |
| bsi_v3_standard_deviation_po3   | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 29. Standard Deviations.mp4                | 00:00:29,720    | 2      |
| bsi_v3_ar50                     | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 34. AR50 Trading Model.mp4                 | 00:00:10,400    | 2      |
| bsi_v3_ifvg_po3                 | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_turtle_soups_ranges      | OUT_OF_SCOPE_FOR_SELECTED_SYMBOLS      |                                            |                 | 0      |
| bsi_v3_yin_yang                 | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 41. The Yin Yang Model.mp4                 | 00:00:03,280    | 2      |
| bsi_v3_4h_candle_ranges         | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 42. 4 Hour Candle Ranges.mp4               | 00:00:05,080    | 2      |
| bsi_v3_smt_session_hl           | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_1h_candle_ranges         | NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS |                                            |                 | 0      |
| bsi_v3_enigma_range             | VIDEO_TIMESTAMP_EVIDENCE_AVAILABLE     | 45. The Enigma.mp4                         | 00:00:00,000    | 2      |
