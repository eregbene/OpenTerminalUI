# BSI V2 Rule To Code Map

Source of truth: the 21 documents in `docs/bsi_v2_video_reconstruction/`. Status values: MATCH, PARTIAL, WRONG, MISSING, EXTRA_RULE, AMBIGUOUS.

## Shared Rules

| Rule ID | Rule | Source | Evidence | Type | Current Code | Current Behavior | Required V2 Behavior | Status | Test | Persisted Evidence | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BSI2-VER-001 | Stamp V2 separately from V1 | implementation request + matrix | BENSIM_ENGINEERING | mandatory | `bsi_engine.py:538`, `bsi_canonical_fingerprint.py:133` | `BSI_BASELINE_V1` | Add `BSI_BASELINE_V2_AUDIOVISUAL`, do not reuse V1 truth | MISSING | version isolation test | `bsi_version` | CRITICAL |
| BSI2-STRUCT-001 | MSS reversal accepted | rulebook | SPOKEN_AND_VISUAL | mandatory where used | `_latest_break`, structure snapshot | supports MSS | Preserve BSI wrapper semantics | MATCH | MSS fixture | `break_id`, `structure_direction` | MEDIUM |
| BSI2-STRUCT-002 | MSB continuation accepted | rulebook | SPOKEN_AND_VISUAL | mandatory where used | `_latest_break` | supports MSB | Preserve, but lifecycle must allow new MSB entries | PARTIAL | duplicate/new MSB test | `structure_kind`, `bsi_entry_opportunity_id` | HIGH |
| BSI2-STRUCT-003 | Do not globally redefine CHoCH/MSS severity | primitives | NOT_ESTABLISHED | mandatory safety | `market_structure/structure.py` | shared generic breaks | Add BSI interpretation wrapper only | MATCH | non-BSI regression | evidence source namespace | HIGH |
| BSI2-LIQ-001 | Swing-level liquidity | primitives | SPOKEN_AND_VISUAL | mandatory where used | `liquidity.py`, `models.LiquidityLevel` | horizontal levels | Reuse raw levels through BSI objects | MATCH | swing sweep fixture | liquidity object id/price | MEDIUM |
| BSI2-LIQ-002 | Equal/multi-touch liquidity | primitives | SPOKEN_AND_VISUAL | mandatory where used | `detect_equal_levels`, `_mentor_equal_levels` | available | Preserve; BSI touch thresholds strategy-specific | MATCH | 3-touch fixture | touch count | MEDIUM |
| BSI2-LIQ-003 | Trendline liquidity must stay diagonal | order-flow spec, primitives | SPOKEN_AND_VISUAL | mandatory for Order Flow | `pivot_trendlines.py` raw; not in BSI path | detected elsewhere, not BSI liquidity | Add BSI trendline liquidity object, no flattening | MISSING | diagonal liquidity golden | anchors, slope, projected level | HIGH |
| BSI2-LIQ-004 | Session-box edge liquidity | asian spec | SPOKEN_AND_VISUAL | mandatory for Asian | `_session_level`, sessions snapshot | available | Preserve as Asian-only liquidity | MATCH | Asian box fixture | box id, high/low | MEDIUM |
| BSI2-LIQ-005 | Sweep must preserve original level separate from wick extreme | NY spec, primitives | SPOKEN_AND_VISUAL | mandatory | `LiquiditySweep.level_id`, `swept_price`; NY uses `swept_price` | wick tip can become entry/stop anchor | Resolve `level_id` to original `LiquidityLevel.level` | WRONG | NY wick-tip negative | original level, wick extreme | CRITICAL |
| BSI2-LIQ-006 | Residual wick liquidity must clear before OB Liquidity fakeout | OB Liquidity spec | SPOKEN_AND_VISUAL | mandatory | absent | no residual-liquidity check | Add BSI-only clearance check | MISSING | residual liquidity negative | residual ids cleared | HIGH |
| BSI2-OB-001 | Mentor OB is first candle that creates the FVG | order-flow spec, primitives | SPOKEN_AND_VISUAL | mandatory where arrays used | `_mentor_order_block_for_fvg` | implemented | Preserve BSI-specific OB, do not use generic OB directly | MATCH | FVG-first-candle test | candle index, bounds | HIGH |
| BSI2-OB-002 | Legacy generic last-opposite-candle OB remains for non-BSI | request phase 3 | BENSIM_ENGINEERING | mandatory safety | `market_structure/zones.py` | shared generic OB | Do not globally remove; wrap for BSI | MATCH | generic OB regression | primitive source | HIGH |
| BSI2-FVG-001 | FVG required for mentor OB | order-flow spec | SPOKEN_AND_VISUAL | mandatory where mentor OB used | `_mentor_order_block_for_fvg` | FVG anchored | Preserve and persist FVG relation | MATCH | OB without FVG negative | `fvg_id`, `mentor_ob_id` | HIGH |
| BSI2-ARRAY-001 | Unmitigated arrays only | rulebook | SPOKEN_AND_VISUAL | mandatory | `_unmitigated_fvgs_in_zone` | active/unmitigated only | Preserve with evidence | MATCH | mitigated FVG negative | mitigation status | MEDIUM |
| BSI2-ARRAY-002 | Extreme valid array preferred | rulebook | SPOKEN_EXPLICIT | mandatory where established | `_select_entry_array` | chooses extreme FVG/array | Preserve for Order Flow/9:30; avoid universalizing if subtype differs | MATCH | stacked arrays fixture | selected array rank | MEDIUM |
| BSI2-PD-001 | PD uses break-causing dealing leg midpoint | order-flow spec | SPOKEN_AND_VISUAL | mandatory for Order Flow MSS | `_leg_bounds`, `_zone_favorable` | midpoint gate | Preserve and persist anchors | MATCH | dealing-leg midpoint test | start, end, midpoint | HIGH |
| BSI2-PD-002 | No unsupported 62-79 OTE dependency | request, primitives | VISUAL_EXPLICIT absence | mandatory | `_ote_zone_for_direction` exists shared | shared OTE exists | Do not call OTE from BSI V2 | MATCH | OTE negative | `pd_model=midpoint` | HIGH |
| BSI2-PD-003 | PD is not universal | primitives | VISUAL_EXPLICIT absence | mandatory | `_zone_favorable` only some BSI paths | mostly not universal | Keep per-strategy classification | MATCH | no-PD strategy negatives | `pd_required=false` | HIGH |
| BSI2-LIFE-001 | THESIS/ENTRY_OPPORTUNITY/EXECUTABLE_ENTRY lifecycle | request | BENSIM_ENGINEERING | mandatory | no full model | metadata only | Add BSI lifecycle state model | MISSING | scheduler duplicate tests | thesis/opportunity ids | CRITICAL |
| BSI2-FRESH-001 | Fresh broker quote recheck before execution | request | BENSIM_ENGINEERING | mandatory | generic geometry/risk only | stale valid setup may execute | Expire stale opportunities; no stop widening/RR lowering | MISSING | stale-entry test | quote time, distance, expiration | CRITICAL |
| BSI2-EVID-001 | Persist rule ids and video-derived evidence | request | BENSIM_ENGINEERING | mandatory | `source_rule_ids` exists, sparse | partial | Fill from rule map for V2 | PARTIAL | persistence round-trip | rule IDs, evidence class | HIGH |

## Strategy Rules

| Rule ID | Strategy | Rule | Source | Evidence | Type | Current Location | Status | Required Change |
|---|---|---|---|---|---|---|---|---|
| BSI2-OF-001 | bsi_order_flow | Liquidity precedes structure | order-flow spec | SPOKEN_AND_VISUAL | mandatory | `_liquidity_sweep_precedes` | PARTIAL | Add trendline liquidity; persist liquidity id/type |
| BSI2-OF-002 | bsi_order_flow | MSS uses PD half | order-flow spec | SPOKEN_AND_VISUAL | mandatory | `_zone_favorable` in `evaluate_bsi_order_flow` | MATCH | Preserve; persist leg anchors |
| BSI2-OF-003 | bsi_order_flow | MSB PD status ambiguous | order-flow spec | AMBIGUOUS | ambiguous | same | AMBIGUOUS | Do not harden until source checked |
| BSI2-OF-004 | bsi_order_flow | Spread-aware entry offset | order-flow examples | SPOKEN_EXPLICIT | mandatory | absent | MISSING | Add quote/spread-aware entry geometry |
| BSI2-OF-005 | bsi_order_flow | New MSB + new array is new opportunity | order-flow spec | SPOKEN_AND_VISUAL | mandatory | no lifecycle | MISSING | Implement opportunity ids |
| BSI2-NY-001 | bsi_new_york | Bare retest, no OB/FVG/MSS/PD | NY spec | SPOKEN_AND_VISUAL | mandatory | `evaluate_bsi_new_york` | MATCH | Preserve isolation |
| BSI2-NY-002 | bsi_new_york | Entry at original swept level | NY spec | SPOKEN_AND_VISUAL | mandatory | `evaluate_bsi_new_york:950` | WRONG | Resolve sweep `level_id` to level |
| BSI2-NY-003 | bsi_new_york | No chase if no retest | NY spec | SPOKEN_EXPLICIT | mandatory | absent/implicit | MISSING | Add retest-before-target guard |
| BSI2-NY-004 | bsi_new_york | Small fakeout quality | NY spec | SPOKEN_AND_VISUAL | mandatory qualitative | `_fakeout_quality_score` maybe used | PARTIAL | Use original-level penetration and evidence |
| BSI2-NY-005 | bsi_new_york | Best-pair allowlist | NY spec | VISUAL_EXPLICIT | optional | absent | MISSING | Add shadow evidence or config gate, not hard by default |
| BSI2-ABC-001 | bsi_abc | A/B/C geometry | ABC spec | SPOKEN_AND_VISUAL | mandatory | `_find_abc_legs` | MATCH | Preserve |
| BSI2-ABC-002 | bsi_abc | B must not exceed A start | ABC spec | SPOKEN_EXPLICIT | mandatory | `_find_abc_legs` | MATCH/PARTIAL | Verify explicit negative |
| BSI2-ABC-003 | bsi_abc | Reclaim A and break C structure | ABC spec | SPOKEN_AND_VISUAL | mandatory | `_abc_internal_break`, evaluator | MATCH | Preserve |
| BSI2-ABC-004 | bsi_abc | OB/FVG entry mandatory | ABC spec | SPOKEN_AND_VISUAL | mandatory | `_select_entry_array` | MATCH | Preserve |
| BSI2-ABC-005 | bsi_abc | Target B-leg high/low | ABC spec | SPOKEN_AND_VISUAL | mandatory | evaluator target | MATCH | Preserve |
| BSI2-ASIAN-001 | bsi_asian | Asian box high/low sweep | Asian spec | SPOKEN_AND_VISUAL | mandatory | `_session_level`, `_session_sweep_direction` | MATCH | Preserve |
| BSI2-ASIAN-002 | bsi_asian | No daily bias | Asian spec | SPOKEN_EXPLICIT | mandatory | evaluator | MATCH if no bias | Preserve |
| BSI2-ASIAN-003 | bsi_asian | No PD | Asian spec | VISUAL_EXPLICIT absence | mandatory | evaluator | MATCH | Preserve |
| BSI2-ASIAN-004 | bsi_asian | Target opposite box edge | Asian examples | SPOKEN_AND_VISUAL | mandatory | evaluator target | MATCH | Preserve |
| BSI2-ASIAN-005 | bsi_asian | Exact lunch clock not mentor-certified | Asian spec | AMBIGUOUS | ambiguous | `_in_lunch_window` | EXTRA_RULE/PARTIAL | Mark as engineering evidence |
| BSI2-UO-001 | bsi_under_over | At least three touches | UO spec | SPOKEN_AND_VISUAL | mandatory | `_mentor_equal_levels` | MATCH | Preserve |
| BSI2-UO-002 | bsi_under_over | Close break/reclaim, wicks ignored | UO spec | SPOKEN_EXPLICIT | mandatory | `_close_based_fakeout_reclaim` | MATCH | Preserve |
| BSI2-UO-003 | bsi_under_over | No MSS/PD/FVG entry | UO spec | VISUAL_EXPLICIT absence | mandatory | evaluator | MATCH | Preserve |
| BSI2-UO-004 | bsi_under_over | Partials then full close | UO spec | SPOKEN_AND_VISUAL | mandatory management | metadata only | PARTIAL | Wire management or persist as non-executable |
| BSI2-UO-005 | bsi_under_over | Skip huge fakeout | UO examples | SPOKEN_EXPLICIT | mandatory qualitative | `_fakeout_quality_score` | PARTIAL | Confirm hard ceiling + soft score evidence |
| BSI2-930-001 | bsi_0930 | 09:30-11:59 NY window | 930 spec | SPOKEN_EXPLICIT | mandatory | `_in_930_window` | MATCH | Preserve |
| BSI2-930-002 | bsi_0930 | Indices preferred | 930 spec | SPOKEN_AND_VISUAL | optional/strong | absent | MISSING | Add evidence/config gate |
| BSI2-930-003 | bsi_0930 | Daily bias required | 930 spec | SPOKEN_EXPLICIT | mandatory | `_mentor_htf_direction` | MATCH | Preserve |
| BSI2-930-004 | bsi_0930 | MSS with displacement or strong displacement substitute | 930 spec | SPOKEN_AND_VISUAL | mandatory | evaluator displacement gate | MATCH | Preserve |
| BSI2-930-005 | bsi_0930 | Extreme FVG preferred | 930 spec | SPOKEN_EXPLICIT | mandatory | `_select_entry_array` | MATCH | Preserve |
| BSI2-930-006 | bsi_0930 | 3R-5R target band | 930 spec | SPOKEN_EXPLICIT | mandatory | `_tp_bounded` | MATCH | Preserve |
| BSI2-930-007 | bsi_0930 | Structure-break BE + partials | 930 spec | SPOKEN_EXPLICIT | mandatory management | metadata only | PARTIAL | Wire if V2 includes management |
| BSI2-RB-001 | bsi_reactionary | Two-array mechanic | Reactionary spec | SPOKEN_AND_VISUAL | mandatory | `_reactionary_confirmation` | MATCH | Preserve |
| BSI2-RB-002 | bsi_reactionary | Array 2 no fresh break required | Reactionary spec | SPOKEN_EXPLICIT | mandatory | `_reactionary_confirmation` | MATCH | Preserve |
| BSI2-RB-003 | bsi_reactionary | Entry from array 2 only | Reactionary examples | SPOKEN_AND_VISUAL | mandatory | evaluator branch | MATCH | Preserve |
| BSI2-RB-004 | bsi_reactionary | Array-2 FVG-vs-OB kind ambiguous | Reactionary spec | AMBIGUOUS | ambiguous | hardcoded FVG | AMBIGUOUS | Record evidence, avoid overfitting |
| BSI2-ABCD-001 | bsi_abcd | Complete ABC first | ABCD spec | SPOKEN_AND_VISUAL | mandatory | `_p1_already_broken` | MATCH | Preserve |
| BSI2-ABCD-002 | bsi_abcd | D breaks B-leg endpoint/P2 | ABCD spec | SPOKEN_AND_VISUAL | mandatory | `evaluate_bsi_abcd` | MATCH | Preserve |
| BSI2-ABCD-003 | bsi_abcd | Retest entry, no OB/FVG gate | ABCD spec | SPOKEN_AND_VISUAL | mandatory | evaluator | MATCH | Preserve |
| BSI2-ABCD-004 | bsi_abcd | Default fixed 1:2 | ABCD spec | SPOKEN_EXPLICIT | mandatory | `_tp_fixed_rr(...2.0)` | MATCH | Preserve |
| BSI2-ABCD-005 | bsi_abcd | Optional structure/1:3 target | ABCD spec | SPOKEN_EXPLICIT | optional | absent | MISSING | Add optional V2 branch only if configured |
| BSI2-OBL-001 | bsi_ob_liquidity | Same-array fakeout/reclaim | OB Liquidity spec | SPOKEN_AND_VISUAL | mandatory | `_liquidity_twist_confirmation` | MATCH | Preserve |
| BSI2-OBL-002 | bsi_ob_liquidity | Origin candle local extremum | OB Liquidity spec | SPOKEN_EXPLICIT | mandatory | `_liquidity_twist_confirmation` | MATCH | Preserve |
| BSI2-OBL-003 | bsi_ob_liquidity | Close-based fakeout; wicks ignored | OB Liquidity spec | SPOKEN_EXPLICIT | mandatory | `_liquidity_twist_confirmation` | MATCH | Preserve |
| BSI2-OBL-004 | bsi_ob_liquidity | Heavy reaction = valid OB, not liquidity OB | OB Liquidity spec | SPOKEN_AND_VISUAL | mandatory/reroute | absent | MISSING | Add disqualifier and Order Flow fallback evidence |
| BSI2-OBL-005 | bsi_ob_liquidity | Residual liquidity clears first | OB Liquidity spec | SPOKEN_AND_VISUAL | mandatory | absent | MISSING | Add clearance model |
| BSI2-OBL-006 | bsi_ob_liquidity | Fakeout size fuzzy ceiling | OB Liquidity spec | SPOKEN_EXPLICIT | mandatory qualitative | absent in path | MISSING | Add score/ceiling evidence |
