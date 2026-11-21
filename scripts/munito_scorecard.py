"""
M15 Munito scorecard: the M10 scenarios on 3 seeds, notes written by Munito models.
docs/SCORECARD.md is not touched; this writes docs/SCORECARD_MUNITO.md.

  python scripts/munito_scorecard.py --generate   # seed 2 and 3 pools on Munito (resumable)
  python scripts/munito_scorecard.py --run        # replay + notes + topic names, write the doc

Seeds: seed 1 is the existing Groq-generated pool (fixtures/m10/seed1_*). Seeds 2 and 3 are
generated here with the same prompts and question banks (scripts/make_m10_fixtures.py), with
Munito models as the "bot": deepseek-ai/deepseek-v4-flash in place of gpt-oss-20b, and
qwen/qwen3.8-27b in place of gpt-oss-120b for the model-swap pools.

Replay: scripts/m10_scorecard.py's build / replay / correct, unchanged, except that every row
also gets its prompt embedding, as /ingest stores it in production (the M10 replay leaves it
empty, so no cluster there could ever be marked a new topic). Notes: each Munito note model
writes the note from the same evidence pack with the production prompt and schema (one model,
no fallback). Topic names: munito_topics.name() on the topic-shift incident's clusters.

Every Munito call goes through munito.chat(feature "scorecard"): flex, reasoning off, capped
output, both budgets (MUNITO_DAILY_USD, MUNITO_TOTAL_USD_CAP) and the ledger.
"""

import argparse
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "docs" / "SCORECARD_MUNITO.md"
RESULTS = ROOT / "fixtures" / "m10" / "_munito_results.json"     # resume file (gitignored)
SEEDS = (1, 2, 3)
GEN_SEEDS = (2, 3)
BOT = {"openai/gpt-oss-20b": "deepseek-ai/deepseek-v4-flash", "openai/gpt-oss-120b": "qwen/qwen3.8-27b"}
NOTE_MODELS = ("deepseek-ai/deepseek-v4.1-flash-1017", "qwen/qwen3.8-27b")
GEN_MAX_TOKENS = 220            # as the Groq generator
RETRIES = 30                    # per row; with waits up to 60 s this rides out ~28 min of outage
NOTE_MAX_TOKENS = 1200
TOPIC_WORDS = ("solar", "ev ", "ev-", "electric vehicle", "charger", "charging", "net metering",
               "renewable", "green energy", "inverter")   # the injected topic: rooftop solar and EV chargers


def setup():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")                  # first: it may set SHIFTLENS_DEV_TRUSTSTORE
    import dev_truststore
    dev_truststore.maybe_enable()
    import database
    database.CONNECT_ATTEMPTS, database.CONNECT_RETRY_DELAY = 5, 3.0   # laptop -> pooler drops (this script only)
    import munito
    if not munito.api_key():
        raise SystemExit("MUNITO_API_KEY is not set")
    return munito


# ---- generation (seeds 2 and 3) -----------------------------------------------------------

class Persistent:
    """db() for munito.chat that reuses one connection (each use is still its own transaction:
    commit on success, rollback and reconnect on any error). From the laptop a fresh pooler
    connect costs 1-11 s, so this keeps the guard's two short transactions per call cheap.
    One connection per worker; the script uses 3 (the local-scripts share of the budget)."""

    def __init__(self):
        self.conn = None

    def __call__(self):
        import contextlib
        import database

        @contextlib.contextmanager
        def ctx():
            if self.conn is None or self.conn.closed:
                self.conn = database._connect_with_retry(database.CONNECT_TIMEOUT)
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                try:
                    self.conn.rollback()
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                raise
        return ctx()

    def close(self):
        if self.conn is not None and not self.conn.closed:
            self.conn.close()


def _gen_pools(munito, jobs, label):
    """One worker: its pools one after another, on one persistent connection."""
    import psycopg2
    db = Persistent()
    try:
        for seed, pool, model, system, qs, n, path in jobs:
            done = sum(1 for l in open(path, encoding="utf-8") if l.strip()) if path.exists() else 0
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                for i in range(done, n):
                    msgs = [{"role": "system", "content": system}, {"role": "user", "content": qs[i]}]
                    for attempt in range(RETRIES):
                        try:
                            a, info = munito.chat("scorecard", model, msgs, GEN_MAX_TOKENS, temperature=0.8, db=db)
                            break
                        except (munito.Unavailable, psycopg2.Error) as e:
                            # a pooler drop, a network outage or a Munito 503: wait it out (the guard keeps
                            # any booked estimate). 10, 20, 40, then 60 s: ~28 min in all before giving up.
                            wait = min(60, 10 * 2 ** attempt)
                            print(f"  [{label}] {model}: {type(e).__name__} {str(e)[:60]}; retry {attempt + 1} in {wait} s",
                                  flush=True)
                            time.sleep(wait)
                    else:
                        raise SystemExit(f"[{label}] {pool} row {i}: failed {RETRIES} times; rerun to resume")
                    f.write(json.dumps({"seed": seed, "pool": pool, "i": i, "model": model, "prompt": qs[i],
                                        "response": a.strip(), "tokens": info["input_tokens"] + info["output_tokens"],
                                        "usd": round(info["usd"], 8)}, ensure_ascii=False) + "\n")
                    f.flush()
                    time.sleep(2.2 if model.startswith("qwen") else 0.3)     # qwen Tier 1: 30 requests/min
                    if (i + 1) % 25 == 0 or i + 1 == n:
                        print(f"[{label}] seed{seed} {pool:10s} {i + 1:3d}/{n} ({model})", flush=True)
    finally:
        db.close()


def generate(munito):
    import threading
    import make_m10_fixtures as gen
    gen.OUT.mkdir(parents=True, exist_ok=True)
    workers = {"deepseek seed 2": [], "deepseek seed 3": [], "qwen": []}
    for seed in GEN_SEEDS:
        for pool, groq_model, system, kind, n in gen.POOLS:
            model = BOT[groq_model]
            path = gen.OUT / f"seed{seed}_{pool}.jsonl"
            done = sum(1 for l in open(path, encoding="utf-8") if l.strip()) if path.exists() else 0
            if done >= n:
                continue
            w = "qwen" if model.startswith("qwen") else f"deepseek seed {seed}"
            workers[w].append((seed, pool, model, system, gen.questions(seed, kind, n), n, path))
    errors = []

    def run(label, jobs):
        try:
            _gen_pools(munito, jobs, label)
        except BaseException as e:          # SystemExit included: report, let the others finish
            errors.append(f"{label}: {e}")
    threads = [threading.Thread(target=run, args=(k, v), name=k) for k, v in workers.items() if v]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise SystemExit("; ".join(errors) + " (rerun --generate to resume)")
    print("seed 2 and 3 pools complete")


# ---- replay ---------------------------------------------------------------------------------

_packs = []


def note_chat(munito, inv, model, db=None):
    """chat(model, messages) for inv.write_report, on one Munito model, no fallback.
    A database drop in the guard is NOT turned into a rules-only note: it propagates, and the
    whole scenario is retried (run())."""
    def chat(groq_model, messages):
        if groq_model != inv.PRIMARY_MODEL:
            raise inv.LLMUnavailable("no fallback in the Munito scorecard")
        try:
            content, _ = munito.chat("scorecard", model, messages, NOTE_MAX_TOKENS, schema=inv.JSON_SCHEMA,
                                     schema_name="incident_note", temperature=0.2, db=db)
        except (munito.Skipped, munito.Unavailable) as e:
            raise inv.LLMUnavailable(f"munito: {e}")
        return content
    return chat


def with_prompts(rows, cache, embed):
    need = [r["prompt"] for r in rows if r["prompt"] not in cache]
    for i in range(0, len(need), 256):
        part = list(dict.fromkeys(need[i:i + 256]))
        for t, v in zip(part, embed(part)):
            cache[t] = v
    for r in rows:
        r["prompt_embedding"] = cache[r["prompt"]]
    return rows


def topic_names(munito, captured):
    import munito_topics as mt
    pack, prompts = captured
    out = []
    for c in [e for e in pack["evidence"] if e.get("kind") == "cluster"][:3]:
        qs = [prompts[i] for i in c.get("example_ids", []) if i in prompts]
        mt.FEATURE = "scorecard"
        t, why = mt.name(qs)
        out.append({"cluster": c["id"], "size": c["size"], "new_topic": c["new_topic"],
                    "similarity": c.get("question_similarity_to_baseline"), "name": t, "why_not": why,
                    "caught": bool(t) and any(w in f" {t.lower()} " for w in TOPIC_WORDS)})
    return out


def run(munito):
    import embeddings
    import investigation as inv
    import m10_scorecard as m10
    orig = inv.analyse

    def capture(*a, **k):
        p = orig(*a, **k)            # the pack goes to the note model unchanged; rows kept beside it
        _packs.append((p, {r["request_id"]: r["prompt"] for r in a[3]}))
        return p
    inv.analyse = capture
    m10.time = types.SimpleNamespace(sleep=lambda s: time.sleep(min(s, 4)))   # no Groq 65 s pacing here
    import psycopg2
    cache, results, topics = {}, {}, {}
    saved = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"results": {}, "topics": {}}
    for k, v in saved["results"].items():                  # finished scenarios from an earlier run
        s, n, m = k.split("|")
        results[(int(s), n, m)] = v
    topics.update({int(s): v for s, v in saved["topics"].items()})
    db = Persistent()

    def save():
        RESULTS.write_text(json.dumps({"results": {f"{s}|{n}|{m}": v for (s, n, m), v in results.items()},
                                       "topics": {str(s): v for s, v in topics.items()}}, default=str, indent=1),
                           encoding="utf-8")
    for seed in SEEDS:
        if all((seed, n, m) in results for n, _, _ in m10.SCENARIOS for m in NOTE_MODELS) and seed in topics:
            print(f"seed {seed}: all scenarios already done", flush=True)
            continue
        pools = m10.load(seed)
        need = {"normal", "prompt_v2", "topic", "model_swap", "double", "degraded"}
        if not need <= set(pools) or any(len(pools[p]) < (400 if p == "normal" else 100) for p in need):
            raise SystemExit(f"seed {seed}: pools incomplete; run --generate first")
        emb = {p: embeddings.embed([r["response"] for r in pools[p]]) for p in need}
        for name, expected, _ in m10.SCENARIOS:
            for model in NOTE_MODELS:
                need_topics = name == "topic_shift" and model == NOTE_MODELS[0] and seed not in topics
                if (seed, name, model) in results and not need_topics:
                    continue
                for attempt in range(RETRIES):
                    try:
                        base, inc = m10.build(name, pools, emb)
                        with_prompts(base + inc, cache, embeddings.embed)
                        _packs.clear()
                        r = m10.replay(base, inc, note_chat(munito, inv, model, db=db))
                        break
                    except psycopg2.Error as e:          # laptop -> pooler drop: redo this scenario
                        wait = min(60, 10 * 2 ** attempt)
                        print(f"  seed {seed} {name} {model}: {type(e).__name__}; retry {attempt + 1} in {wait} s", flush=True)
                        time.sleep(wait)
                else:
                    raise SystemExit(f"seed {seed} {name}: database unreachable; rerun --run to resume")
                r["right"] = m10.correct(name, expected, r)
                results[(seed, name, model)] = r
                save()
                print(f"seed {seed} {name:14s} {model.split('/')[1]:28s} opened={r['opened']} cause={r.get('cause')} "
                      f"conf={r.get('confidence')} by={r.get('written_by')} right={r['right']} {r.get('fallback') or ''}",
                      flush=True)
                if need_topics and _packs:
                    topics[seed] = topic_names(munito, _packs[-1])
                    save()
                    print(f"seed {seed} topic names: {topics[seed]}", flush=True)
    db.close()
    return results, topics


# ---- the doc --------------------------------------------------------------------------------

def write(results, topics, spent, munito):
    import m10_scorecard as m10
    short = {m: m.split("/")[1] for m in NOTE_MODELS}
    L = ["# Scorecard on Munito models (M15)", "",
         f"Generated {datetime.now(timezone.utc):%Y-%m-%d} by `scripts/munito_scorecard.py`. The 8 scenarios of "
         "[SCORECARD.md](SCORECARD.md) (300 baseline + 100 incident answers each, through the production detection, "
         "incident and investigation code) on **3 seeds**, with the investigation note written by Munito models "
         "(flex, reasoning off) instead of Groq. SCORECARD.md itself is unchanged.", "",
         "- Seed 1: the existing pools (bot answers by gpt-oss-20b / gpt-oss-120b on Groq).",
         "- Seeds 2 and 3: generated on Munito with the same prompts and question banks; the bot is "
         "deepseek-v4-flash, and qwen3.8-27b for the model-swap pools.",
         "- Every row has its prompt embedding, as `/ingest` stores it in production (the M10 replay left it "
         "empty, so no cluster there could be marked a new topic).",
         "- One run per seed and model: single examples, not rates.", "",
         "| scenario | expected | " + " | ".join(f"{short[m]}: right (per seed)" for m in NOTE_MODELS) + " | causes given |",
         "|---|---|" + "---|" * len(NOTE_MODELS) + "---|"]
    for name, expected, desc in m10.SCENARIOS:
        cells, causes = [], []
        for m in NOTE_MODELS:
            rs = [results[(s, name, m)] for s in SEEDS]
            cells.append(f"{sum(r['right'] for r in rs)} of {len(rs)} ({' '.join('✓' if r['right'] else '✗' for r in rs)})")
            causes.append(f"{short[m]}: " + ", ".join(
                (f"{r['cause']} {r['confidence']:.2f}" + ("" if r["written_by"] != "rules" else " (rules)"))
                if r["opened"] else "no incident" for r in rs))
        L.append(f"| {name} | {expected or 'no incident'} | " + " | ".join(cells) + " | " + "<br>".join(causes) + " |")
    tot = {m: sum(results[(s, n, m)]["right"] for s in SEEDS for n, _, _ in m10.SCENARIOS) for m in NOTE_MODELS}
    rules = {m: sum(1 for s in SEEDS for n, _, _ in m10.SCENARIOS
                    if results[(s, n, m)]["opened"] and results[(s, n, m)]["written_by"] == "rules") for m in NOTE_MODELS}
    n_all = len(SEEDS) * len(m10.SCENARIOS)
    L += ["", "Totals: " + "; ".join(f"{short[m]} right {tot[m]} of {n_all}, rules-only fallback {rules[m]}"
                                    for m in NOTE_MODELS) + ".", ""]
    L += ["## Topic naming on the topic-shift scenario", "",
          "Production names only the clusters the investigation marks as a new topic (questions less similar than "
          "0.70 to every baseline question). The topic injected in this scenario is rooftop solar and EV chargers. "
          "\"Would have caught it\" = a cluster marked new topic got a name about solar or EV charging. Names are "
          "shown for every cluster, marked or not.", "",
          "| seed | cluster | answers | question similarity to baseline | marked new topic | name (Munito) | about the injected topic |",
          "|---|---|---|---|---|---|---|"]
    for s in SEEDS:
        for t in topics.get(s, []):
            L.append(f"| {s} | {t['cluster']} | {t['size']} | {t['similarity']} | {'yes' if t['new_topic'] else 'no'} | "
                     f"{t['name'] or '(none: ' + str(t['why_not']) + ')'} | {'yes' if t['caught'] else 'no'} |")
        if not topics.get(s):
            L.append(f"| {s} | - | - | - | - | (no incident or no clusters) | - |")
    caught = [s for s in SEEDS if any(t["new_topic"] and t["caught"] for t in topics.get(s, []))]
    L += ["", f"Would topic naming have caught the topic shift? **{len(caught)} of {len(SEEDS)} seeds** "
          f"({', '.join(map(str, caught)) or 'none'}).", "",
          "## Cost", "",
          f"All Munito scorecard spend in the ledger (feature `scorecard`): **${spent['total']:.4f}**, of which "
          f"generating the seed 2 and 3 pools ${spent['generation']:.4f} (1,800 answers), the coverage agreement "
          f"check ${spent['agreement']:.4f}, and these notes and topic names ${spent['notes']:.4f} (including "
          f"retries after network drops). Flex prices checked {munito.PRICES_CHECKED}. Full-coverage agreement "
          "with the Groq sample: [munito_coverage_agreement.md](munito_coverage_agreement.md)."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    munito = setup()
    import database
    if a.generate:
        generate(munito)
    if a.run:
        results, topics = run(munito)
        with database.connection() as conn:
            total = sum(r["usd"] for r in munito.admin_view(conn)["by_feature_model_total"] if r["feature"] == "scorecard")
        gen = sum(json.loads(l).get("usd", 0) for s in GEN_SEEDS for f in (ROOT / "fixtures" / "m10").glob(f"seed{s}_*.jsonl")
                  for l in open(f, encoding="utf-8") if l.strip())
        agreement = 0.004647                        # docs/munito_coverage_agreement.md
        write(results, topics, {"total": total, "generation": gen, "agreement": agreement,
                                "notes": total - gen - agreement}, munito)
    with database.connection() as conn:
        v = munito.admin_view(conn)
    print(f"Munito total spent ${v['total_spent_usd']:.4f}, today ${v['today_usd']:.4f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
