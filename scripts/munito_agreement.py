"""
M15 step 2 check: does a coverage check on Munito agree with the sampled spot-check on Groq?

The 40 with-context items of the M14 scorecard (fixtures/m10 seed 1, Volt's policy text as
context: 20 answers with one policy fact changed, 20 unaltered) are judged twice with the same
prompt, schema and model (qwen/qwen3.8-27b): once on Groq (spotcheck.groq_json, free tier, kind
"scorecard" in llm_usage) and once on Munito (munito_coverage.munito_json, feature "scorecard",
inside both Munito budgets). Writes docs/munito_coverage_agreement.md.

  python scripts/munito_agreement.py
"""

import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "docs" / "munito_coverage_agreement.md"


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")                  # first: it may set SHIFTLENS_DEV_TRUSTSTORE
    import dev_truststore
    dev_truststore.maybe_enable()
    import database
    import munito
    import munito_coverage as mc
    import spotcheck
    import m10_scorecard as m10
    import m14_scorecard as m14

    if not munito.api_key():
        raise SystemExit("MUNITO_API_KEY is not set; nothing to compare")
    items = [i for i in m14.spot_items(m10.load(1)) if i[2]]          # with context only
    mc.FEATURE = "scorecard"                                           # book it as scorecard spend
    with database.connection() as conn:
        before = munito.admin_view(conn)["total_spent_usd"]
    rows, g_tokens, g_calls = [], 0, 0
    for n, (prompt, response, ctx, problem, label) in enumerate(items, 1):
        g, used, gwhy = spotcheck.judge(prompt, response, ctx, spotcheck.groq_json, models=(spotcheck.PRIMARY,))
        g_tokens += sum(t for _, t in used); g_calls += len(used)
        m, mwhy = mc.check(prompt, response, ctx)
        rows.append((label, problem, g, m, gwhy, mwhy))
        print(f"{n:2d}/{len(items)} {label:9s} groq={g and g['verdict']} munito={m and m['verdict']}", flush=True)
        if n == 3 and not any(r[3] for r in rows):
            raise SystemExit(f"munito: no verdict on the first 3 items ({mwhy}); nothing written")
        time.sleep(6)                           # Groq qwen free: 8K tokens/min
    with database.connection() as conn:
        database.record_llm_usage(conn, spotcheck.PRIMARY, "scorecard", 0, units=len(items),
                                  requests=g_calls, tokens=g_tokens)
        spend = munito.admin_view(conn)["total_spent_usd"] - before
    write(rows, g_tokens, spend)
    print(f"wrote {OUT}")


def metrics(rows, i):
    tp = sum(1 for r in rows if r[i] and r[1] and r[i]["likely_problem"])
    fn = sum(1 for r in rows if r[i] and r[1] and not r[i]["likely_problem"])
    fp = sum(1 for r in rows if r[i] and not r[1] and r[i]["likely_problem"])
    tn = sum(1 for r in rows if r[i] and not r[1] and not r[i]["likely_problem"])
    none = sum(1 for r in rows if not r[i])
    return tp, fn, fp, tn, none


def write(rows, g_tokens, spend):
    both = [r for r in rows if r[2] and r[3]]
    same_verdict = sum(1 for r in both if r[2]["verdict"] == r[3]["verdict"])
    same_problem = sum(1 for r in both if r[2]["likely_problem"] == r[3]["likely_problem"])
    pairs = Counter((r[2]["verdict"], r[3]["verdict"]) for r in both)
    usd = spend
    lines = [
        "# Coverage checks: Munito vs the Groq sample",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d} by `scripts/munito_agreement.py`. The 40 with-context items "
        "of the M14 scorecard (fixtures/m10 seed 1, Volt's policy text as context: 20 answers with one policy fact "
        "changed, 20 unaltered), each judged by qwen/qwen3.8-27b with the same prompt and schema, once on Groq "
        "(the sampled spot-check) and once on Munito (the full-coverage check: flex, reasoning off). "
        "One run: single examples, not rates. A problem is not_supported; partly_supported is a neutral note.",
        "",
        "| | Groq (sample) | Munito (full coverage) |",
        "|---|---|---|",
    ]
    g, m = metrics(rows, 2), metrics(rows, 3)
    lines += [f"| changed facts caught | {g[0]} of {g[0] + g[1]} | {m[0]} of {m[0] + m[1]} |",
              f"| unaltered answers flagged (false flags) | {g[2]} of {g[2] + g[3]} | {m[2]} of {m[2] + m[3]} |",
              f"| no verdict | {g[4]} | {m[4]} |", "",
              f"Items with a verdict from both: {len(both)}. Same verdict: **{same_verdict} of {len(both)}**. "
              f"Same problem / no-problem call: **{same_problem} of {len(both)}**.", "",
              "| Groq verdict → Munito verdict | items |", "|---|---|"]
    lines += [f"| {a} → {b} | {n} |" for (a, b), n in pairs.most_common()]
    lines += ["", f"Cost: Groq {g_tokens} tokens on the free tier; Munito ${usd:.6f} for these calls "
              "(booked in the Munito ledger, feature `scorecard`)."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
