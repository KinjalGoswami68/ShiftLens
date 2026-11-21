# Scorecard on Munito models (M15)

Generated 2026-11-21 by `scripts/munito_scorecard.py`. The 8 scenarios of [SCORECARD.md](SCORECARD.md) (300 baseline + 100 incident answers each, through the production detection, incident and investigation code) on **3 seeds**, with the investigation note written by Munito models (flex, reasoning off) instead of Groq. SCORECARD.md itself is unchanged.

- Seed 1: the existing pools (bot answers by gpt-oss-20b / gpt-oss-120b on Groq).
- Seeds 2 and 3: generated on Munito with the same prompts and question banks; the bot is deepseek-v4-flash, and qwen3.8-27b for the model-swap pools.
- Every row has its prompt embedding, as `/ingest` stores it in production (the M10 replay left it empty, so no cluster there could be marked a new topic).
- One run per seed and model: single examples, not rates.

| scenario | expected | deepseek-v4.1-flash-1017: right (per seed) | qwen3.8-27b: right (per seed) | causes given |
|---|---|---|---|---|
| prompt_change | prompt_change | 1 of 3 (✓ ✗ ✗) | 1 of 3 (✓ ✗ ✗) | deepseek-v4.1-flash-1017: prompt_change 0.88, unknown 0.30, unknown 0.30<br>qwen3.8-27b: prompt_change 0.91, unknown 0.30, unknown 0.30 |
| model_swap | model_change | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: model_change 0.60, model_change 0.79, model_change 0.60<br>qwen3.8-27b: model_change 0.67 (rules), model_change 0.79, model_change 0.67 |
| topic_shift | topic_shift | 2 of 3 (✓ ✓ ✗) | 2 of 3 (✓ ✓ ✗) | deepseek-v4.1-flash-1017: topic_shift 0.60, topic_shift 0.80, unknown 0.30<br>qwen3.8-27b: topic_shift 0.67, topic_shift 0.80, unknown 0.30 |
| control | no incident | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: no incident, no incident, no incident<br>qwen3.8-27b: no incident, no incident, no incident |
| harmless_bump | no incident | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: no incident, no incident, no incident<br>qwen3.8-27b: no incident, no incident, no incident |
| silent_change | unknown | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: unknown 0.30, unknown 0.30, unknown 0.30<br>qwen3.8-27b: unknown 0.30, unknown 0.30, unknown 0.30 |
| double_change | ambiguous | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: unknown 0.30, ambiguous 0.50, ambiguous 0.50<br>qwen3.8-27b: unknown 0.30, ambiguous 0.50, ambiguous 0.50 |
| slow_drift | unknown | 3 of 3 (✓ ✓ ✓) | 3 of 3 (✓ ✓ ✓) | deepseek-v4.1-flash-1017: unknown 0.30, unknown 0.30, unknown 0.30<br>qwen3.8-27b: unknown 0.30, unknown 0.30, unknown 0.30 |

Totals: deepseek-v4.1-flash-1017 right 21 of 24, rules-only fallback 0; qwen3.8-27b right 21 of 24, rules-only fallback 1.

## Topic naming on the topic-shift scenario

Production names only the clusters the investigation marks as a new topic (questions less similar than 0.70 to every baseline question). The topic injected in this scenario is rooftop solar and EV chargers. "Would have caught it" = a cluster marked new topic got a name about solar or EV charging. Names are shown for every cluster, marked or not.

| seed | cluster | answers | question similarity to baseline | marked new topic | name (Munito) | about the injected topic |
|---|---|---|---|---|---|---|
| 1 | C1 | 20 | 0.687 | yes | Net metering paperwork | yes |
| 1 | C2 | 15 | 0.953 | no | Solar inverter batteries | yes |
| 1 | C3 | 9 | 0.626 | yes | EV charger compatibility | yes |
| 2 | C1 | 16 | 0.624 | yes | EV charger compatibility | yes |
| 2 | C2 | 15 | 0.611 | yes | Solar inverter batteries | yes |
| 2 | C3 | 11 | 0.669 | yes | Solar payback period | yes |
| 3 | C1 | 33 | 0.716 | no | Solar panel installation | yes |
| 3 | C2 | 11 | 0.726 | no | EV charger installation | yes |
| 3 | C3 | 10 | 0.665 | yes | Solar inverter batteries | yes |

Would topic naming have caught the topic shift? **3 of 3 seeds** (1, 2, 3).

## Cost

All Munito scorecard spend in the ledger (feature `scorecard`): **$0.0731**, of which generating the seed 2 and 3 pools $0.0443 (1,800 answers), the coverage agreement check $0.0046, and these notes and topic names $0.0242 (including retries after network drops). Flex prices checked 2026-11-19 (posted prices from 2026-11-17). Full-coverage agreement with the Groq sample: [munito_coverage_agreement.md](munito_coverage_agreement.md).
