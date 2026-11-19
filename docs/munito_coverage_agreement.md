# Coverage checks: Munito vs the Groq sample

Generated 2026-11-19 by `scripts/munito_agreement.py`. The 40 with-context items of the M14 scorecard (fixtures/m10 seed 1, Volt's policy text as context: 20 answers with one policy fact changed, 20 unaltered), each judged by qwen/qwen3.8-27b with the same prompt and schema, once on Groq (the sampled spot-check) and once on Munito (the full-coverage check: flex, reasoning off). One run: single examples, not rates. A problem is not_supported; partly_supported is a neutral note.

| | Groq (sample) | Munito (full coverage) |
|---|---|---|
| changed facts caught | 12 of 16 | 17 of 20 |
| unaltered answers flagged (false flags) | 7 of 17 | 9 of 20 |
| no verdict | 7 | 0 |

Items with a verdict from both: 33. Same verdict: **26 of 33**. Same problem / no-problem call: **29 of 33**.

| Groq verdict → Munito verdict | items |
|---|---|
| not_supported → not_supported | 19 |
| partly_supported → partly_supported | 7 |
| partly_supported → not_supported | 4 |
| partly_supported → supported | 3 |

Cost: Groq 13007 tokens on the free tier; Munito $0.004647 for these calls (booked in the Munito ledger, feature `scorecard`).
