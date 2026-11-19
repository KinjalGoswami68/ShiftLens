"""
Weekly report on Munito (M15 feature 3, MUNITO_WEEKLY_REPORT=1, default off).

One short plain-language summary per bot per week, written from the numbers ShiftLens already
has (the daily digest's numbers over 7 days, the week before, and the week's incidents). Every
number in the summary must appear in those numbers; otherwise the plain-numbers version is used
instead (no invented figures). Stored in weekly_reports (one per bot per week, even across
restarts), shown on bot health and sent to the bot's OWN Slack webhook only, if it set one.

The week: Monday 00:00 IST to the next Monday 00:00 IST. Sent on Mondays at WEEKLY_AT_IST
(20:40, inside the 09:00-21:00 awake window); if the service was asleep, the next awake check
in the same week catches up (any day, 09:00-21:00 IST only).

Model: deepseek-ai/deepseek-v4.1-flash-1017, flex, reasoning off, at most 400 output tokens,
through munito.chat() (both budgets apply). Without MUNITO_API_KEY or with the flag off,
`start()` does nothing. `build_facts`, `check_numbers` and `plain` are pure (tests).
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import munito

FEATURE = "weekly"
MODEL = "deepseek-ai/deepseek-v4.1-flash-1017"
MAX_TOKENS = 400
IST = timedelta(minutes=330)
WEEKLY_AT_IST = os.environ.get("WEEKLY_AT_IST", "20:40")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary"],
          "properties": {"summary": {"type": "string"}}}
SYSTEM = ("You write a short weekly report for the owner of a support chatbot, from monitoring numbers. "
          "3 to 5 plain sentences, no headings, no lists, no markdown. Say how the week went, what changed "
          "since the week before, and anything to look at. Use ONLY the numbers given, exactly as given; do not "
          "compute new ones. If nothing looked wrong, say so plainly.")


def week_bounds(now):
    """(start, end, week_key) of the last complete IST week (Monday 00:00 IST to Monday 00:00 IST)."""
    ist = now.astimezone(timezone.utc) + IST
    monday = (ist - timedelta(days=ist.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (monday - IST).replace(tzinfo=timezone.utc)
    return end - timedelta(days=7), end, monday.date()


def _pct(a, b):
    return round(100 * a / b) if b else 0


def build_facts(name, week, prev, incidents, state, start, end):
    """The numbers the summary may use. Pure."""
    n, n0 = week["answers"], prev["answers"]
    facts = {
        "bot": name, "week": f"{(start + IST):%d %b} to {(end + IST - timedelta(days=1)):%d %b}",
        "state": state, "answers": n, "answers_week_before": n0,
        "flagged_pct": _pct(week["flagged"], n), "flagged_pct_week_before": _pct(prev["flagged"], n0),
        "refusals_or_empty_pct": _pct(week["nonanswers"], n),
        "avg_answer_length_chars": round(week["avg_len"]),
        "incidents": len(incidents),
        "incident_causes": sorted({(i.get("cause") or "not investigated").replace("_", " ") for i in incidents}),
        "quality_flags": {k.replace("_", " "): v for k, v in sorted(week["quality"].items(), key=lambda kv: -kv[1])[:3]},
        "spot_checks": week["spot_checked"], "spot_check_problems": week["spot_problems"],
    }
    return facts


NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check_numbers(summary, facts):
    """Numbers in the summary that are not in the facts (empty = fine)."""
    allowed = set(NUM.findall(json.dumps(facts)))
    return [x for x in NUM.findall(summary) if x not in allowed]


def plain(facts):
    """The plain-numbers report (no LLM), also the fallback."""
    f = facts
    s = [f"{f['bot']}, {f['week']}: {f['answers']} answers (week before: {f['answers_week_before']}), "
         f"{f['flagged_pct']}% flagged (week before: {f['flagged_pct_week_before']}%), "
         f"{f['refusals_or_empty_pct']}% refusals or empty answers."]
    if f["incidents"]:
        s.append(f"{f['incidents']} incident(s): {', '.join(f['incident_causes'])}.")
    if f["spot_check_problems"]:
        s.append(f"Spot-checks: {f['spot_check_problems']} of {f['spot_checks']} likely have a problem.")
    if not f["incidents"] and not f["spot_check_problems"] and not f["quality_flags"]:
        s.append("Nothing looked wrong.")
    return " ".join(s)


def write(facts, chat=None):
    """(summary, written_by). chat(messages) -> raw json is injectable for tests."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Numbers (JSON):\n" + json.dumps(facts, ensure_ascii=False)}]
    try:
        if chat is None:
            raw, _ = munito.chat(FEATURE, MODEL, msgs, MAX_TOKENS, schema=SCHEMA, schema_name="weekly",
                                 temperature=0.3)
        else:
            raw = chat(msgs)
        summary = re.sub(r"\s+", " ", str(json.loads(raw).get("summary") or "")).strip()
    except (munito.Skipped, munito.Unavailable, ValueError, AttributeError) as e:
        return plain(facts), f"numbers only ({type(e).__name__})"
    bad = check_numbers(summary, facts)
    if not summary or len(summary) > 1200 or bad:
        return plain(facts), "numbers only (" + (f"numbers not in the data: {', '.join(bad[:3])}" if bad else "unusable") + ")"
    return summary, MODEL


def run_once(notify, now=None, chat=None):
    """Build, store and send last week's report for every bot active that week. Returns how many
    were new. One per bot per week (primary key), so repeats and restarts send nothing twice."""
    import database
    if not munito.enabled(FEATURE):
        return 0
    now = now or datetime.now(timezone.utc)
    start, end, week = week_bounds(now)
    with database.connection() as conn:
        bots = database.active_bots(conn, start)
        cur = conn.cursor()
        cur.execute("SELECT company_id FROM weekly_reports WHERE week = %s", (week,))
        done = {r[0] for r in cur.fetchall()}
    new = 0
    for cid, name, state in bots:
        if cid in done:
            continue
        try:
            with database.connection() as conn:
                w = database.digest_stats(conn, cid, start, end)
                p = database.digest_stats(conn, cid, start - timedelta(days=7), start)
                incs = database.incidents_between(conn, cid, start, end)
            if not w["answers"]:
                continue
            facts = build_facts(name, w, p, incs, state, start, end)
            summary, by = write(facts, chat=chat)
            body = {"summary": summary, "written_by": by, "facts": facts}
            with database.connection() as conn:
                cur = conn.cursor()
                cur.execute("""INSERT INTO weekly_reports (company_id, week, body) VALUES (%s, %s, %s)
                               ON CONFLICT (company_id, week) DO NOTHING RETURNING week""", (cid, week, json.dumps(body)))
                fresh = cur.fetchone() is not None
            if not fresh:
                continue
            new += 1
            if notify(cid, f"*Weekly report: {name}, {facts['week']}*\n{summary}"):
                with database.connection() as conn:
                    conn.cursor().execute("UPDATE weekly_reports SET slack_sent = true WHERE company_id = %s AND week = %s",
                                          (cid, week))
        except Exception as e:
            print(f"weekly report for company {cid} failed: {type(e).__name__}", flush=True)
    print(f"weekly report: {new} new for the week of {week}", flush=True)
    return new


def latest(conn, company_id):
    """The bot's newest weekly report, or None."""
    cur = conn.cursor()
    cur.execute("""SELECT week, body, slack_sent FROM weekly_reports WHERE company_id = %s
                   ORDER BY week DESC LIMIT 1""", (int(company_id),))
    r = cur.fetchone()
    if not r:
        return None
    return {"week": r[0].isoformat(), "summary": r[1].get("summary"), "written_by": r[1].get("written_by"),
            "period": (r[1].get("facts") or {}).get("week"), "slack_sent": r[2]}


def due(now):
    """True from Monday WEEKLY_AT_IST until Sunday, inside the 09:00-21:00 IST awake window."""
    ist = now.astimezone(timezone.utc) + IST
    if not 9 <= ist.hour < 21:
        return False
    h, m = map(int, WEEKLY_AT_IST.split(":"))
    return ist.weekday() > 0 or (ist.hour, ist.minute) >= (h, m)


def start(notify):
    """Background check every 30 min; does nothing when the feature is off."""
    if not munito.enabled(FEATURE):
        return False

    def loop():
        finished = None                           # the week whose reports all exist: no more DB reads
        while True:
            try:
                now = datetime.now(timezone.utc)
                week = week_bounds(now)[2]
                if due(now) and week != finished:
                    run_once(notify, now=now)
                    finished = week               # every active bot now has this week's report
                    # (a bot that failed is retried next week; the error is in the log)
            except Exception as e:
                print(f"weekly report loop: {type(e).__name__}", flush=True)
            time.sleep(1800)
    threading.Thread(target=loop, daemon=True, name="munito-weekly").start()
    return True
