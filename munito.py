"""
Munito AI (2026-11-19): optional extra features on the owner's prepaid credits. NEW features
only: full-coverage checks, topic names, weekly reports and the Munito scorecard. Nothing that
exists today (investigations, questions and sampled spot-checks on Groq) calls this module.

Every Munito call in ShiftLens goes through `chat()` here:
  - off unless MUNITO_API_KEY is set AND the feature's own env var is "1" (default off);
  - flex service tier, reasoning off, a max output-token limit on every call;
  - the cost is estimated BEFORE the call (prompt size + max output, at the posted price) and
    the call is skipped if it would cross the daily budget (MUNITO_DAILY_USD, default 1.00) or
    the total cap (MUNITO_TOTAL_USD_CAP, default 90; only the owner raises it, in Render);
  - the estimate is booked in the database first and replaced by the real cost from the
    response's token counts, so a restart or a crash can never reset or undercount the total;
  - 402 (payment review), 401 (bad key), an inactive account or the free tier (credits used up)
    stop every Munito feature until the owner clears it on the admin page; a Munito-side spend
    limit pauses them until the next UTC day. Nothing else in ShiftLens is affected.

Prices and limits: docs.munito.ai/models and /rate-limits, checked 2026-11-19 (prices from
2026-11-17, global host, USD per 1M tokens). The pure parts (estimate, cost, budget decision,
error handling) are tested offline in tests/test_munito.py.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.munito.ai/inference/v1"
TIER = "flex"

# (input, output) USD per 1M tokens, global host, per service tier.
PRICES = {
    "qwen/qwen3.8-27b": {"flex": (0.09, 1.20), "standard": (0.18, 2.40), "priority": (0.32, 4.20)},
    "deepseek-ai/deepseek-v4.1-flash-1017": {"flex": (0.14, 0.50), "standard": (0.27, 1.00), "priority": (0.47, 1.80)},
    "deepseek-ai/deepseek-v4-flash": {"flex": (0.05, 0.13), "standard": (0.099, 0.25), "priority": (0.17, 0.44)},
}
PRICES_CHECKED = "2026-11-19 (posted prices from 2026-11-17)"

# Reasoning off: reasoning tokens are billed as output (docs: features/reasoning).
REASONING_OFF = {
    "qwen/qwen3.8-27b": {"chat_template_kwargs": {"enable_thinking": False}},
    "deepseek-ai/deepseek-v4.1-flash-1017": {"reasoning_effort": "none"},
    "deepseek-ai/deepseek-v4-flash": {"reasoning_effort": "none"},
}

# Base requests/minute per usage tier (docs: rate-limits), to name the account's tier from the
# x-ratelimit-limit-requests header. Paid limits can rise with usage ("above Tier N").
TIER_BY_RPM = {
    "qwen/qwen3.8-27b": [(15, "Free"), (30, "Tier 1"), (60, "Tier 2"), (120, "Tier 3"), (240, "Tier 4")],
    "deepseek-ai/deepseek-v4.1-flash-1017": [(200, "Free"), (400, "Tier 1"), (800, "Tier 2"), (1600, "Tier 3"), (3200, "Tier 4")],
    "deepseek-ai/deepseek-v4-flash": [(10, "Free"), (100, "Tier 1"), (200, "Tier 2"), (400, "Tier 3"), (800, "Tier 4")],
}

# Feature -> its on/off env var. "scorecard" is run by hand (scripts/munito_scorecard.py) and
# "smoke" records the step-0 test calls; both need only the key.
FEATURES = {
    "coverage": "MUNITO_FULL_COVERAGE",
    "topics": "MUNITO_TOPIC_NAMES",
    "weekly": "MUNITO_WEEKLY_REPORT",
    "scorecard": None,
    "smoke": None,
}

CHARS_PER_TOKEN = 3.0      # English is ~4 characters per token; 3 over-estimates on purpose
TOKENS_PER_MESSAGE = 8     # chat template overhead per message, also over-estimated
MAX_OUTPUT_CAP = 1200      # no call may ask for more output than this
NOT_SENT_ERRORS = ("SSLError", "ConnectTimeout")    # requests exceptions raised before anything is sent


class Skipped(Exception):
    """The call was not made (feature off, no key, budget, stopped). Costs nothing."""


class Unavailable(Exception):
    """The call was made (or attempted) and failed. The feature degrades; nothing else changes."""


def api_key():
    return os.environ.get("MUNITO_API_KEY", "").strip()


def flag_on(feature):
    env = FEATURES.get(feature, "missing")
    if env == "missing":
        return False
    return env is None or os.environ.get(env, "").strip() == "1"


def enabled(feature):
    """True when this feature may try a call: the key is set and its env var is on."""
    return bool(api_key()) and flag_on(feature)


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def daily_cap():
    return _f("MUNITO_DAILY_USD", "1.00")


def total_cap():
    return _f("MUNITO_TOTAL_USD_CAP", "90")


def credit():
    return _f("MUNITO_CREDIT_USD", "100")


def utc_day(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


# ---- pure: estimate, cost, decision ---------------------------------------------------------

def estimate_input_tokens(messages):
    return sum(math.ceil(len(str(m.get("content") or "")) / CHARS_PER_TOKEN) + TOKENS_PER_MESSAGE
               for m in messages) + 16


def price(model, tier=TIER):
    tiers = PRICES[model]
    return tiers.get(tier) or tiers["priority"]      # an unknown billed tier: assume the dearest


def cost(model, input_tokens, output_tokens, tier=TIER):
    pin, pout = price(model, tier)
    return (input_tokens * pin + output_tokens * pout) / 1e6


def estimate(model, messages, max_tokens):
    """Upper-bound cost of one call: over-estimated prompt + the full output allowance."""
    return cost(model, estimate_input_tokens(messages), max_tokens, TIER)


def decide(state, today_usd, est, now=None):
    """May a call of estimated cost `est` start? (ok, reason). Pure.
    state: {"total_usd", "stopped_reason", "paused_day", "paused_reason"}."""
    if state.get("stopped_reason"):
        return False, f"Munito is off: {state['stopped_reason']}"
    total = float(state.get("total_usd") or 0)
    if total + est > total_cap():
        return False, (f"total cap reached: ${total:.4f} spent of the ${total_cap():.2f} cap "
                       "(MUNITO_TOTAL_USD_CAP); Munito features stay off until the owner raises it")
    if state.get("paused_day") and str(state["paused_day"]) == str(utc_day(now)):
        return False, f"paused until 05:30 IST: {state.get('paused_reason') or 'Munito spend limit'}"
    if today_usd + est > daily_cap():
        return False, (f"daily budget used: ${today_usd:.4f} of ${daily_cap():.2f} today "
                       "(MUNITO_DAILY_USD); Munito features resume at 05:30 IST")
    return True, ""


def error_action(status, body_text):
    """What a failed response means for every Munito feature: ("stop" | "pause" | None, reason)."""
    try:
        err = (json.loads(body_text or "{}").get("error") or {})
    except (ValueError, AttributeError):
        err = {}
    code = str(err.get("code") or "")
    etype = str(err.get("type") or "")
    msg = str(err.get("message") or "")[:200]
    if status == 402:
        return "stop", f"Munito answered 402 (payment required / paid access paused): {msg or 'no message'}"
    if status == 401:
        return "stop", "Munito rejected the API key (401). Check MUNITO_API_KEY."
    if status == 403 and ("not active" in msg.lower() or "verification" in msg.lower() or "permission" in msg.lower()):
        return "stop", f"Munito account problem (403): {msg}"
    if status == 429 and code == "free_daily_limit_exceeded":
        return "stop", ("Munito put the account on the free tier (credits used up or not applied). "
                        "Munito features are off; ShiftLens never adds a card or buys credits.")
    if status == 429 and (code == "organization_spend_limit_exceeded" or etype == "insufficient_quota"
                          or "spend limit" in msg.lower()):
        return "pause", f"Munito spend limit reached: {msg or code or etype}"
    return None, ""


def tier_name(model, rpm):
    try:
        rpm = int(rpm)
    except (TypeError, ValueError):
        return None
    table = TIER_BY_RPM.get(model, [])
    for limit, name in table:
        if rpm == limit:
            return name
    if table and rpm > table[-1][0]:
        return f"above {table[-1][1]}"
    if table and rpm > table[0][0]:
        return "between tiers (dynamic limits)"
    return None


# ---- database (one short connection before and one after each call; none during it) ---------

def _state(conn, lock=False):
    cur = conn.cursor()
    cur.execute("INSERT INTO munito_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    cur.execute("""SELECT total_usd, stopped_reason, stopped_at, paused_day, paused_reason, last_error,
                          last_error_at, usage_tier, updated_at
                   FROM munito_state WHERE id = 1""" + (" FOR UPDATE" if lock else ""))
    r = cur.fetchone()
    keys = ("total_usd", "stopped_reason", "stopped_at", "paused_day", "paused_reason", "last_error",
            "last_error_at", "usage_tier", "updated_at")
    st = dict(zip(keys, r))
    st["total_usd"] = float(st["total_usd"] or 0)
    return st


def _today_usd(conn, now=None):
    cur = conn.cursor()
    cur.execute("SELECT coalesce(sum(usd), 0) FROM munito_usage WHERE day = %s", (utc_day(now),))
    return float(cur.fetchone()[0])


def reserve(conn, feature, model, est, now=None):
    """Book `est` before the call, under a row lock so concurrent calls cannot both slip under a
    cap. Returns (ok, reason)."""
    st = _state(conn, lock=True)
    ok, why = decide(st, _today_usd(conn, now), est, now)
    if not ok:
        return False, why
    cur = conn.cursor()
    cur.execute("UPDATE munito_state SET total_usd = total_usd + %s, updated_at = now() WHERE id = 1", (est,))
    cur.execute("""INSERT INTO munito_usage (day, feature, model, calls, usd) VALUES (%s, %s, %s, 1, %s)
                   ON CONFLICT (day, feature, model) DO UPDATE SET calls = munito_usage.calls + 1,
                   usd = munito_usage.usd + EXCLUDED.usd""", (utc_day(now), feature, model, est))
    return True, ""


def settle(conn, feature, model, est, actual, input_tokens=0, output_tokens=0, now=None):
    """Replace the booked estimate with the real cost."""
    delta = actual - est
    cur = conn.cursor()
    cur.execute("UPDATE munito_state SET total_usd = greatest(0, total_usd + %s), updated_at = now() WHERE id = 1", (delta,))
    cur.execute("""UPDATE munito_usage SET usd = greatest(0, usd + %s), input_tokens = input_tokens + %s,
                   output_tokens = output_tokens + %s WHERE day = %s AND feature = %s AND model = %s""",
                (delta, int(input_tokens), int(output_tokens), utc_day(now), feature, model))


def record_error(conn, action, reason, now=None):
    cur = conn.cursor()
    cur.execute("INSERT INTO munito_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    if action == "stop":
        cur.execute("""UPDATE munito_state SET stopped_reason = %s, stopped_at = now(), last_error = %s,
                       last_error_at = now(), updated_at = now() WHERE id = 1""", (reason, reason))
    elif action == "pause":
        cur.execute("""UPDATE munito_state SET paused_day = %s, paused_reason = %s, last_error = %s,
                       last_error_at = now(), updated_at = now() WHERE id = 1""", (utc_day(now), reason, reason))
    else:
        cur.execute("UPDATE munito_state SET last_error = %s, last_error_at = now(), updated_at = now() WHERE id = 1",
                    (reason[:300],))


def record_tier(conn, tier):
    conn.cursor().execute("UPDATE munito_state SET usage_tier = %s WHERE id = 1", (tier,))


def clear_stop(conn):
    """Owner only (admin page): turn Munito back on after an error stop. The total cap is not
    affected: it is checked on every call against MUNITO_TOTAL_USD_CAP."""
    conn.cursor().execute("""UPDATE munito_state SET stopped_reason = NULL, stopped_at = NULL, paused_day = NULL,
                             paused_reason = NULL, updated_at = now() WHERE id = 1""")


def record_external(conn, feature, model, input_tokens, output_tokens, now=None):
    """Book calls made outside chat() (the step-0 smoke test) so the total matches Munito's."""
    usd = cost(model, input_tokens, output_tokens)
    cur = conn.cursor()
    _state(conn, lock=True)
    cur.execute("UPDATE munito_state SET total_usd = total_usd + %s, updated_at = now() WHERE id = 1", (usd,))
    cur.execute("""INSERT INTO munito_usage (day, feature, model, calls, input_tokens, output_tokens, usd)
                   VALUES (%s, %s, %s, 1, %s, %s, %s)
                   ON CONFLICT (day, feature, model) DO UPDATE SET calls = munito_usage.calls + 1,
                   input_tokens = munito_usage.input_tokens + EXCLUDED.input_tokens,
                   output_tokens = munito_usage.output_tokens + EXCLUDED.output_tokens,
                   usd = munito_usage.usd + EXCLUDED.usd""",
                (utc_day(now), feature, model, int(input_tokens), int(output_tokens), usd))
    return usd


# ---- the one call --------------------------------------------------------------------------

def chat(feature, model, messages, max_tokens, schema=None, schema_name="out", timeout=90,
         temperature=0, post=None, db=None):
    """One Munito chat completion with every guard. Returns (content, info) where info =
    {"usd", "input_tokens", "output_tokens", "tier"}. Raises Skipped (no call made, nothing
    spent) or Unavailable (call failed). `post` and `db` are injectable for tests."""
    if not api_key():
        raise Skipped("MUNITO_API_KEY not set")
    if not flag_on(feature):
        raise Skipped(f"{FEATURES.get(feature) or feature} is off")
    if model not in PRICES:
        raise Skipped(f"no price for {model}")
    max_tokens = int(min(max_tokens, MAX_OUTPUT_CAP))
    if db is None:
        import database
        db = database.connection
    est = estimate(model, messages, max_tokens)
    with db() as conn:
        ok, why = reserve(conn, feature, model, est)
    if not ok:
        raise Skipped(why)

    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            **REASONING_OFF.get(model, {})}
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json",
               "munito-service-tier": TIER}
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(f"{BASE_URL}/chat/completions", json=body, headers=headers, timeout=timeout)
    except Exception as e:
        with db() as conn:
            # TLS or connect failures happen before the request is sent: nothing was billed.
            # Anything later (a read timeout, a dropped answer) may have run and been billed:
            # keep the booked estimate.
            if type(e).__name__ in NOT_SENT_ERRORS:
                settle(conn, feature, model, est, 0.0)
            record_error(conn, None, f"{feature}: {type(e).__name__}")
        raise Unavailable(type(e).__name__)

    if r.status_code != 200:
        action, reason = error_action(r.status_code, getattr(r, "text", ""))
        with db() as conn:
            settle(conn, feature, model, est, 0.0)       # an error answer is not billed
            record_error(conn, action, reason or f"{feature}: HTTP {r.status_code}")
        raise Unavailable(f"HTTP {r.status_code}" + (f" ({action})" if action else ""))

    j = r.json()
    usage = j.get("usage") or {}
    tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    billed = (r.headers.get("munito-service-tier") or j.get("service_tier") or TIER).lower()
    billed = "standard" if billed == "default" else billed
    actual = cost(model, tin, tout, billed) if (tin or tout) else est    # no usage: keep the estimate
    tier = tier_name(model, r.headers.get("x-ratelimit-limit-requests"))
    with db() as conn:
        settle(conn, feature, model, est, actual, tin, tout)
        if tier:
            record_tier(conn, tier)
    choice = (j.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    if choice.get("finish_reason") == "length" or not content:
        raise Unavailable("output cut at max_tokens" if choice.get("finish_reason") == "length" else "empty answer")
    return content, {"usd": actual, "input_tokens": tin, "output_tokens": tout, "tier": billed}


# ---- admin page ----------------------------------------------------------------------------

ADMIN_NOTE = ("Spend counts ShiftLens's own Munito calls only (each estimated before the call and "
              "corrected from the token counts after it). Other API keys in the same Munito organization "
              "draw on the same credit balance, so the Munito console shows the real balance.")


def admin_view(conn, now=None):
    st = _state(conn)
    today = _today_usd(conn, now)
    cur = conn.cursor()
    cur.execute("""SELECT feature, model, sum(calls), sum(input_tokens), sum(output_tokens), sum(usd)
                   FROM munito_usage GROUP BY feature, model ORDER BY feature, model""")
    totals = [{"feature": f, "model": m, "calls": int(c), "input_tokens": int(i), "output_tokens": int(o),
               "usd": round(float(u), 6)} for f, m, c, i, o, u in cur.fetchall()]
    cur.execute("""SELECT feature, sum(calls), sum(usd) FROM munito_usage WHERE day = %s GROUP BY feature
                   ORDER BY feature""", (utc_day(now),))
    today_rows = [{"feature": f, "calls": int(c), "usd": round(float(u), 6)} for f, c, u in cur.fetchall()]
    ok, why = decide(st, today, 0.0, now)
    status = "active" if ok and api_key() else ("off" if not api_key() else "paused" if "paused" in why or "daily" in why else "stopped")
    return {
        "key_set": bool(api_key()),
        "status": status,
        "status_reason": "" if ok else why,
        "features": [{"feature": f, "env": env, "on": enabled(f)} for f, env in FEATURES.items() if env],
        "total_spent_usd": round(st["total_usd"], 6),
        "credit_usd": credit(),
        "credit_left_usd": round(credit() - st["total_usd"], 6),
        "today_usd": round(today, 6),
        "daily_cap_usd": daily_cap(),
        "total_cap_usd": total_cap(),
        "usage_tier": st["usage_tier"],
        "tier": TIER,
        "last_error": st["last_error"],
        "last_error_at": st["last_error_at"],
        "stopped_reason": st["stopped_reason"],
        "by_feature_today": today_rows,
        "by_feature_model_total": totals,
        "prices_checked": PRICES_CHECKED,
        "note": ADMIN_NOTE,
    }
