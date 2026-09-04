"""The LLM coach — the "LLM Based Adaptive Support" proposed in the report (§3.7).

After a run it reads the session log the game already writes, folds it into a
compact performance summary, and asks an LLM for personalized, encouraging
feedback: what went well, what to improve, one concrete tip. It runs on a
background thread so the game loop never blocks, and it always produces
*something*: it tries the chosen model, then a few free OpenRouter models (which
work even on a key that is out of paid credit), then a deterministic local
coach — so a grading session with a spent key or no internet still shows
feedback.

The model is reached through OpenRouter's OpenAI-compatible endpoint (stdlib
HTTP, no SDK to install). The key is read from the OPENROUTER_API_KEY
environment variable and is never stored in the repo — the public GitHub repo
must never carry a secret. Set the model with OPENROUTER_MODEL; the default is
anthropic/claude-sonnet-5 (cheap and plenty for short feedback). Use
anthropic/claude-haiku-4.5 to spend even less, or anthropic/claude-opus-4.8 for
the strongest write-up.

Design follows current LLM-app guidance and recent work: keep the control flow
in code and call the model at exactly one place; use a JSON-schema structured
output so the fields come back parseable; reason first in a private field before
the structured fields; ground the prompt in pedagogical principles; and carry a
small player profile across runs so feedback can note progress. Two extra inputs
may ride in the summary, a heart_rate effort summary and a history of past runs,
and the model is told to treat heart rate as effort and progress only, never as
health or medical advice, per the report's boundary on the physiological data.

Nothing here imports pygame, so it is testable without a window; draw.py renders
whatever `Coach.feedback` holds.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

# OpenRouter, OpenAI-compatible. Model id is an OpenRouter route, not a bare
# Anthropic id — override with OPENROUTER_MODEL.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-5")
MAX_TOKENS = 900
TIMEOUT_S = 10                     # per model attempt, so the chain can't hang

# Best-effort $0 models to try when the primary model fails — e.g. an
# over-its-limit key, which OpenRouter blocks for paid models but not for free
# ones. They are flaky (rate limits, missing JSON support), so the coach only
# leans on them, then falls through to the offline coach. Set OPENROUTER_MODEL
# to a free id to make one of these the primary and skip the paid attempt.
FREE_FALLBACKS = [
    "z-ai/glm-5.2:free",
    "google/gemma-4-31b-it:free",
]

# What the model must return. Structured outputs validate this for us, so the
# renderer can trust every key exists and has the right type. "analysis" is a
# private reasoning field placed first: the model reasons there before it fills
# the player-facing fields, which recovers the reasoning that a strict format
# can otherwise suppress (Tam et al., "Let Me Speak Freely?", 2024). It is never
# shown. "effort" is one sentence about physical effort, drawn from heart rate.
FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},          # private reasoning, not displayed
        "headline": {"type": "string"},          # one line, the overall read
        "did_well": {"type": "array", "items": {"type": "string"}},
        "improve": {"type": "array", "items": {"type": "string"}},
        "tip": {"type": "string"},                # one concrete thing to try next
        "effort": {"type": "string"},             # one sentence on physical effort
        "encouragement": {"type": "string"},
    },
    "required": ["analysis", "headline", "did_well", "improve", "tip",
                 "effort", "encouragement"],
    "additionalProperties": False,
}

# Pedagogically grounded prompt: be specific, actionable, growth minded, and pick
# one focus (Chi et al. field study on AI feedback in a serious game, 2025). The
# heart rate is an effort and progress signal only, never health advice, which
# keeps it inside the report's boundary on the physiological data.
SYSTEM = (
    "You are a coach for MagicDodge, an exercise game played with body movement "
    "and drawn gestures. The player leans to change lane and draws a shape to "
    "cast the spell that beats an incoming monster: triangle beats circle, "
    "circle beats square, square beats triangle. Drawing the monster's own shape "
    "is blocked; drawing the shape it beats empowers it and speeds it up.\n"
    "Coaching principles: refer to what actually happened in this run; make every "
    "point something the player can act on next time; keep a warm, growth minded "
    "tone; choose one main thing to improve rather than a long list.\n"
    "You are given a JSON summary of one run. It may also include a heart_rate "
    "summary and a history of past runs. Treat heart rate ONLY as a sign of "
    "physical effort and of progress across runs, and describe it in plain, "
    "motivating words in the effort field. If a history is present, note real "
    "progress such as a higher score or better accuracy than before.\n"
    "Never give health, fitness, or medical advice, never diagnose anything, and "
    "never comment on the player's body or any health condition.\n"
    "First reason in the analysis field, then fill the player-facing fields. Keep "
    "every player-facing string to one sentence, with two or three items per list."
)


# =============================================================================
# Reading the log and folding it into a summary
# =============================================================================


def read_records(path) -> list[dict]:
    """The session JSONL, one dict per line. Missing file -> no records."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass                # a half-written final line, mid-crash
    return records


def summarize(records: list[dict], score: int, wave: int) -> dict:
    """Fold the raw log into the compact shape the model (and fallback) read.

    Two record kinds share the file: per-cast lines (no "type") and per-wave
    "wave_summary" lines. Cast lines carry the outcome of each spell, which is
    where the interesting coaching signal lives.
    """
    casts = [r for r in records if r.get("type") != "wave_summary"]
    waves = [r for r in records if r.get("type") == "wave_summary"]

    outcomes = Counter(r.get("outcome") for r in casts)
    # Per shape drawn: how often it landed a kill, so we can name a strength and
    # a weak spot by shape.
    per_shape: dict[str, dict[str, int]] = {}
    for r in casts:
        shape = r.get("shape_cast")
        if not shape:
            continue
        stat = per_shape.setdefault(shape, {"cast": 0, "kill": 0})
        stat["cast"] += 1
        if r.get("outcome") == "kill":
            stat["kill"] += 1

    durations = [r["duration_ms"] for r in casts
                 if isinstance(r.get("duration_ms"), (int, float)) and r["duration_ms"] > 0]

    total = {k: sum(w.get(k, 0) for w in waves)
             for k in ("casts", "kills", "misfires", "blocks", "empowers", "damage_taken")}
    total_casts = total["casts"] or len(casts)

    return {
        "score": score,
        "waves_reached": wave,
        "total_casts": total_casts,
        "kills": total["kills"],
        "misfires": total["misfires"],
        "blocks": total["blocks"],
        "empowers": total["empowers"],
        "damage_taken": total["damage_taken"],
        "accuracy_pct": round(100 * total["kills"] / total_casts) if total_casts else 0,
        "max_combo": max((w.get("max_combo", 1.0) for w in waves), default=1.0),
        "avg_cast_ms": round(sum(durations) / len(durations)) if durations else None,
        "outcome_counts": {k: v for k, v in outcomes.items() if k},
        "per_shape": per_shape,
    }


# =============================================================================
# Persistent player profile — longitudinal coaching across runs
# =============================================================================


def load_profile(path) -> dict:
    """The player's history before this run, or an empty dict on the first run."""
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def update_profile(path, summary: dict) -> dict:
    """Fold this run into the profile and save it, so the next run can see it.

    Kept small on purpose: a run count, a best score, and a short recent history
    are enough for the coach to speak to progress without storing personal data.
    """
    prof = load_profile(path)
    run = {
        "score": summary.get("score", 0),
        "wave": summary.get("waves_reached", 0),
        "accuracy_pct": summary.get("accuracy_pct", 0),
    }
    hr = summary.get("heart_rate") or {}
    if hr.get("mean_bpm"):
        run["mean_bpm"] = hr["mean_bpm"]
    updated = {
        "runs": prof.get("runs", 0) + 1,
        "best_score": max(prof.get("best_score", 0), run["score"]),
        "recent": (prof.get("recent", []) + [run])[-5:],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    return updated


# =============================================================================
# The coach — a background request with a local fallback
# =============================================================================


class Coach:
    """One coach per run. Call request() once, poll feedback/status from draw.py."""

    def __init__(self, model: str = MODEL):
        self.model = model
        self.status = "idle"        # idle -> pending -> ready
        self.feedback: dict | None = None
        self.source = ""            # "llm" or "offline", for a small UI note
        self.model_used = ""        # which model actually produced the feedback
        self._thread: threading.Thread | None = None

    def request(self, summary: dict) -> None:
        """Kick off the feedback on a daemon thread. Safe to call once per run."""
        if self.status != "idle":
            return
        self.status = "pending"
        self._thread = threading.Thread(target=self._run, args=(summary,), daemon=True)
        self._thread.start()

    def _run(self, summary: dict) -> None:
        # Try the chosen model, then the free fallbacks, then the offline coach.
        # The first model that returns valid feedback wins.
        tried = []
        for model in [self.model] + [m for m in FREE_FALLBACKS if m != self.model]:
            tried.append(model)
            try:
                self.feedback = _call_llm(summary, model)
                self.source = "llm"
                self.model_used = model
                self.status = "ready"
                return
            except Exception as error:
                print(f"Coach: {model} unavailable ({str(error)[:120]})")
        print(f"Coach: falling back to the offline coach after {tried}")
        self.feedback = _fallback(summary)
        self.source = "offline"
        self.status = "ready"


def _call_llm(summary: dict, model: str) -> dict:
    """One structured-output call to the model via OpenRouter. Raises on any
    failure so the caller can fall back; never returns malformed feedback.

    Uses stdlib HTTP so there is nothing to install. The key comes from the
    OPENROUTER_API_KEY environment variable and is never written to disk."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    body = json.dumps({
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user",
             "content": "Here is the run summary:\n" + json.dumps(summary, indent=2)},
        ],
        # OpenRouter's structured-output shape: a named strict JSON schema. Falls
        # back to prose on providers that ignore it, which _parse_json tolerates.
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "coach_feedback", "strict": True, "schema": FEEDBACK_SCHEMA},
        },
    }).encode("utf-8")

    request = urllib.request.Request(
        OPENROUTER_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Optional attribution headers OpenRouter uses for its dashboard.
            "HTTP-Referer": "https://github.com/HungPhamGia/Magic_Dodge_Game",
            "X-Title": "MagicDodge",
        },
    )
    # Single, time-boxed attempt: on a spent key the paid model 403s instantly
    # and free models mostly 429 fast, so the chain reaches the offline coach in
    # a few seconds rather than hanging the game-over screen.
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"OpenRouter HTTP {error.code}: {detail}") from error

    # A provider error can come back as a 200 with an "error" field and no choices.
    if not payload or "choices" not in payload:
        raise RuntimeError(f"OpenRouter returned no choices: {json.dumps(payload)[:200]}")
    text = payload["choices"][0]["message"]["content"]
    data = _parse_json(text)
    # Normalize so the renderer can trust the shape even if a lax free model
    # dropped a field or returned a list as a string.
    for name in ("did_well", "improve"):
        data[name] = list(data.get(name) or [])
    for name in ("headline", "tip", "effort", "encouragement"):
        data[name] = str(data.get(name) or "").strip()
    return data


def _parse_json(text: str) -> dict:
    """Tolerant JSON parse: strips a ```json fence and, failing that, pulls the
    first {...} block out — some models wrap structured output in prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise


def _fallback(summary: dict) -> dict:
    """A deterministic coach for when the model is unreachable. Reads the same
    summary and applies simple rules, so a run always ends with real feedback."""
    did_well, improve = [], []

    acc = summary["accuracy_pct"]
    if acc >= 70:
        did_well.append(f"Sharp casting — {acc}% of your spells landed a kill.")
    elif acc <= 40 and summary["total_casts"] >= 4:
        improve.append(f"Only {acc}% of casts killed; take an extra beat to pick the right shape.")

    if summary["max_combo"] >= 3:
        did_well.append(f"You built a {summary['max_combo']:.0f}x combo — great streaks.")
    if summary["empowers"] >= 2:
        improve.append("You empowered monsters a few times; that means the losing shape — recall the cycle.")
    if summary["blocks"] >= 2:
        improve.append("Some casts were blocked; that's the monster's own shape, not the counter.")
    if summary["damage_taken"] >= 2:
        improve.append("You took several hits — lean out of a lane earlier when you can't answer it.")

    # Name the best and weakest shape, if there's enough signal.
    rates = {s: v["kill"] / v["cast"] for s, v in summary["per_shape"].items() if v["cast"] >= 2}
    if rates:
        best = max(rates, key=rates.get)
        did_well.append(f"Your {best} spell is your most reliable.")
        worst = min(rates, key=rates.get)
        if worst != best and rates[worst] < 0.5:
            improve.append(f"Practice the {worst} spell — it missed more than it hit.")

    # Progress against past runs, if a history is present.
    history = summary.get("history") or {}
    prev_best = history.get("best_score")
    if prev_best is not None and summary["score"] > prev_best:
        did_well.insert(0, f"New personal best, up from {prev_best} points.")
    elif history.get("recent"):
        last = history["recent"][-1]
        if summary["accuracy_pct"] > last.get("accuracy_pct", 0):
            did_well.append("Your accuracy improved on your last run.")

    if not did_well:
        did_well.append("You kept casting and pushing through the waves.")
    if not improve:
        improve.append("Push for a longer kill streak next run to lift the multiplier.")

    # Effort, from the heart rate summary, phrased as effort only.
    hr = summary.get("heart_rate") or {}
    rise = hr.get("rise_bpm")
    if rise is None:
        effort = "No heart rate was recorded for this run."
    elif rise >= 30:
        effort = f"Your heart rate rose about {rise} beats above rest, so you were working hard."
    elif rise >= 12:
        effort = f"Your heart rate rose about {rise} beats above rest, a solid moderate effort."
    else:
        effort = "Your heart rate stayed close to rest; there is room to push harder next time."

    return {
        "analysis": "",
        "headline": f"You reached wave {summary['waves_reached']} with {summary['score']} points.",
        "did_well": did_well[:3],
        "improve": improve[:3],
        "tip": "Draw the shape that beats the monster: triangle>circle>square>triangle.",
        "effort": effort,
        "encouragement": "Nice run — line up the shapes and that score will climb. Press R to go again!",
    }


# =============================================================================
# Test harness: `python -m magicdodge.coach [session_log.jsonl]`
# =============================================================================
# Try the coach without playing a whole game. With no argument it uses a sample
# run; with a path it summarizes a real session log from magicdodge/logs/. It
# prints the offline feedback (always available) and, if OPENROUTER_API_KEY is
# set, the live result plus which model answered.

if __name__ == "__main__":
    import sys
    import time

    _SAMPLE = {
        "score": 1450, "waves_reached": 3, "total_casts": 12, "kills": 8,
        "misfires": 0, "blocks": 1, "empowers": 2, "damage_taken": 3,
        "accuracy_pct": 67, "max_combo": 3.0, "avg_cast_ms": 520,
        "outcome_counts": {"kill": 8, "block": 1, "empower": 2, "no_target": 1},
        "per_shape": {"circle": {"cast": 5, "kill": 5},
                      "triangle": {"cast": 4, "kill": 2},
                      "square": {"cast": 3, "kill": 1}},
        "heart_rate": {"resting_bpm": 74, "mean_bpm": 128, "peak_bpm": 152,
                       "rise_bpm": 54, "per_wave_mean_bpm": {"1": 112, "2": 129, "3": 143},
                       "samples": 95, "simulated": True},
        "history": {"runs": 2, "best_score": 1200,
                    "recent": [{"score": 900, "wave": 2, "accuracy_pct": 58},
                               {"score": 1200, "wave": 3, "accuracy_pct": 63}]},
    }

    if len(sys.argv) > 1:
        recs = read_records(sys.argv[1])
        waves = [r for r in recs if r.get("type") == "wave_summary"]
        summary = summarize(recs, score=0, wave=max((w["wave"] for w in waves), default=1))
        print(f"Loaded {len(recs)} records from {sys.argv[1]}\n")
    else:
        summary = _SAMPLE
        print("Using a sample run (pass a magicdodge/logs/session_*.jsonl to use a real one)\n")

    print("--- OFFLINE coach (always available, no key) ---")
    print(json.dumps(_fallback(summary), indent=2, ensure_ascii=False))

    if os.environ.get("OPENROUTER_API_KEY"):
        print(f"\n--- LIVE coach via OpenRouter (primary: {MODEL}) ---")
        c = Coach()
        c.request(summary)
        t0 = time.time()
        while c.status != "ready" and time.time() - t0 < 60:
            time.sleep(0.2)
        print(f"ready in {time.time() - t0:.1f}s | source={c.source} | "
              f"model={c.model_used or '(offline)'}")
        print(json.dumps(c.feedback, indent=2, ensure_ascii=False))
    else:
        print("\n(OPENROUTER_API_KEY not set — skipping the live call. "
              "Export it to test the OpenRouter path.)")
