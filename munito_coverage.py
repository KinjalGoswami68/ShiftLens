"""
Full-coverage checks on Munito (M15 feature 1, MUNITO_FULL_COVERAGE=1, default off).

Answers sent WITH context that the existing sample (spotcheck.sampled) did NOT pick are checked
on Munito with exactly the same prompt, JSON schema and verdict labels as the sampled spot-check
(spotcheck.messages / spotcheck.judge, imported, not changed). The result is stored in
outputs.coverage_check; outputs.spot_check is never written here. The context lives only in
this in-memory queue for the check and is then dropped (never stored, never logged).

Model: qwen/qwen3.8-27b, the same model as the sample, so "sampled" and "full coverage" verdicts
are comparable; flex, reasoning off, at most 300 output tokens. Paced to <= 20 calls a minute
(Tier 1 allows 30). Every call goes through munito.chat(), so both budgets apply.
Without MUNITO_API_KEY (or with the flag off) `wanted()` is False and nothing is queued.
"""

import json
import queue
import threading
import time

import munito
import spotcheck

FEATURE = "coverage"
MODEL = "qwen/qwen3.8-27b"
MAX_TOKENS = 300
QUEUE_MAX = 200
MIN_GAP_S = 3.0


def wanted(company_id, request_key, context):
    """Should this stored answer get a Munito coverage check? Cheap: no DB, no network."""
    if not context or not str(context).strip():
        return False
    if not munito.enabled(FEATURE):
        return False
    return not spotcheck.sampled(company_id, request_key)      # the sample's answers stay Groq-only


def munito_json(model, msgs):
    """chat(model, messages) -> (json text, tokens) for spotcheck.judge, through munito.chat()."""
    import investigation as inv
    try:
        content, info = munito.chat(FEATURE, model, msgs, MAX_TOKENS, schema=spotcheck.SCHEMA,
                                    schema_name="spot_check")
    except (munito.Skipped, munito.Unavailable) as e:
        raise inv.LLMUnavailable(f"munito: {e}")
    return content, info["input_tokens"] + info["output_tokens"]


def check(prompt, response, context, chat=munito_json):
    """One coverage check. Returns (result | None, why_not). Same verdicts as the sample."""
    result, _calls, why = spotcheck.judge(prompt, response, context, chat, models=(MODEL,))
    if result:
        result["source"] = "munito_full_coverage"
    return result, why


def save(conn, output_id, result):
    conn.cursor().execute("UPDATE outputs SET coverage_check = %s WHERE id = %s",
                          (json.dumps(result), int(output_id)))


def counts(conn, company_id, hours=24):
    """Sampled (Groq) vs full coverage (Munito) checks on the bot's answers in the last `hours`."""
    cur = conn.cursor()
    cur.execute("""
        SELECT count(*) FILTER (WHERE spot_check IS NOT NULL),
               count(*) FILTER (WHERE """ + spotcheck.PROBLEM_SQL + """),
               count(*) FILTER (WHERE spot_check->>'verdict' = 'partly_supported'),
               count(*) FILTER (WHERE coverage_check IS NOT NULL),
               count(*) FILTER (WHERE (coverage_check->>'verdict') IN ('not_supported', 'does_not_answer', 'self_contradictory')),
               count(*) FILTER (WHERE coverage_check->>'verdict' = 'partly_supported')
        FROM outputs WHERE company_id = %s AND "timestamp" > now() - make_interval(hours => %s)""",
                (int(company_id), int(hours)))
    s, sp, spp, c, cp, cpp = cur.fetchone()
    return {"hours": hours,
            "sampled": {"checked": s, "likely_problems": sp, "partly_supported": spp},
            "full_coverage": {"on": munito.enabled(FEATURE), "checked": c, "likely_problems": cp,
                              "partly_supported": cpp}}


# ---- background worker ------------------------------------------------------------------

_q = queue.Queue(maxsize=QUEUE_MAX)
_started = False
_lock = threading.Lock()


def submit(output_id, company_id, prompt, response, context):
    """Queue one stored answer. Never blocks; a full queue skips it."""
    global _started
    if not munito.enabled(FEATURE):
        return False
    try:
        _q.put_nowait((output_id, company_id, prompt, response, context))
    except queue.Full:
        return False
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_work, daemon=True, name="munito-coverage").start()
    return True


def _work():
    import database
    last = 0.0
    while True:
        output_id, company_id, prompt, response, context = _q.get()
        try:
            wait = MIN_GAP_S - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            result, why = check(prompt, response, context)
            if result:
                with database.connection() as conn:
                    save(conn, output_id, result)
            else:
                print(f"coverage check skipped for output {output_id}: {why}", flush=True)
        except Exception as e:
            print(f"coverage check error: {type(e).__name__}", flush=True)
        finally:
            context = None          # never kept
            _q.task_done()
