"""What was actually lifted, set by set.

`metrics.py` holds one number at one moment, which is the right shape for a
body weight and the wrong shape for a training session. "Five sets of five at
100 kg, last one a grind" is four numbers and a judgement, and flattening it to
a single figure throws away the part that decides what to do next week.

The unit here is a **block** — sets × reps at one weight — because that is how
people actually write a log. `5x5 @ 100` is one row; `100x5, 105x3, 110x1` is
three. A session is the blocks that share a `session_id`.

Two derived numbers earn their place, and nothing else does:

* **Volume** (sets × reps × weight) is what progression is measured in, and it
  is exact arithmetic that a model asked to do it in its head gets wrong.
* **Estimated 1RM** is the only honest way to compare 5 at 100 against 3 at 110.
  It is an *estimate* from a formula that other formulas disagree with, so it is
  reported with the formula named — the same rule the health safety block
  already states about contested science.

No exercise taxonomy. "Squat", "back squat" and "Back Squat" are whatever the
user calls them; names are matched case-insensitively so the first two do not
silently become separate exercises with separate histories, and the agent is
told to check `list_exercises` before inventing a new spelling. Imposing a
canonical list would be wrong for anyone whose gym vocabulary is not ours.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import get_settings
from .log import get_logger, suppressed

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifts (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    exercise   TEXT NOT NULL,      -- as the user says it
    lookup     TEXT NOT NULL,      -- lowercased, for matching
    sets       INTEGER NOT NULL,
    reps       INTEGER NOT NULL,
    weight     REAL NOT NULL,      -- always kg
    rpe        REAL,               -- 1-10, how hard it felt. Optional.
    at         TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lift_exercise ON lifts(lookup, at);
CREATE INDEX IF NOT EXISTS idx_lift_session ON lifts(session_id);
"""

#: Bodyweight work is a real entry with no external load, so zero is allowed —
#: but a negative or absurd weight is a typo, and a typo in a log is a wrong
#: trend for months.
MAX_WEIGHT_KG = 600.0
MAX_SETS = 100
MAX_REPS = 500

#: Epley. Brzycki and Lombardi disagree with it, mildly, and all three are
#: estimates — so the formula is named wherever the number is shown rather than
#: presented as a measurement.
FORMULA = "Epley: weight × (1 + reps / 30)"

_DB: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        path = get_settings().home / "metrics.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _DB = sqlite3.connect(str(path), check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.executescript(_SCHEMA)
    return _DB


@dataclass(frozen=True)
class Block:
    """Sets × reps at one weight."""

    exercise: str
    sets: int
    reps: int
    weight: float
    rpe: float | None = None
    note: str = ""

    @property
    def volume(self) -> float:
        return self.sets * self.reps * self.weight

    @property
    def one_rep_max(self) -> float:
        """What this set suggests a single would be. An estimate, not a lift."""
        return self.weight * (1 + self.reps / 30)

    def say(self) -> str:
        """How a person writes it down."""
        load = f"{self.weight:g} kg" if self.weight else "bodyweight"
        effort = f", RPE {self.rpe:g}" if self.rpe else ""
        return f"{self.exercise} {self.sets}×{self.reps} @ {load}{effort}"


def parse_blocks(raw: object) -> tuple[list[Block], str]:
    """Clean what was proposed, or say what is wrong with it.

    Refuses the whole session rather than part of it: the user confirmed a card
    that listed every block, and storing some of them means they approved
    something that did not happen.
    """
    if isinstance(raw, dict):
        raw = raw.get("blocks") or raw.get("items")
    if not isinstance(raw, list) or not raw:
        return [], "No exercises were listed."

    blocks: list[Block] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "One of the exercises was not described properly."
        name = str(entry.get("exercise") or "").strip()
        if not name:
            return [], "One of the entries has no exercise name."
        try:
            sets = int(_number(entry, "sets", 1))
            reps = int(_number(entry, "reps", 1))
            weight = _number(entry, "weight", 0)
        except (TypeError, ValueError):
            return [], f"The numbers for “{name}” are not numbers."

        if not 1 <= sets <= MAX_SETS:
            return [], f"“{name}”: {sets} sets is not a number of sets."
        if not 1 <= reps <= MAX_REPS:
            return [], f"“{name}”: {reps} reps is not a number of reps."
        if not 0 <= weight <= MAX_WEIGHT_KG:
            return [], (f"“{name}”: {weight:g} kg looks like a typo. "
                        f"Use 0 for bodyweight.")

        rpe: float | None = None
        if entry.get("rpe") not in (None, "", 0):
            with suppressed("reading how hard a set felt"):
                value = float(entry["rpe"])
                rpe = value if 1 <= value <= 10 else None
        blocks.append(Block(exercise=name, sets=sets, reps=reps, weight=weight,
                            rpe=rpe, note=str(entry.get("note") or "")))
    return blocks, ""


def _number(entry: dict, key: str, default: float) -> float:
    """One field, where an explicit zero is a value and not a missing one.

    `entry.get(key, 1) or 1` reads naturally and is wrong: zero is falsy, so
    "0 sets" became "1 set" and a card the user had emptied on purpose logged
    a set they did not do.
    """
    raw = entry.get(key, default)
    return float(default if raw is None or raw == "" else raw)


def summarise_session(blocks: list[Block]) -> str:
    """The card's line, and the agent's confirmation. One phrasing, not two."""
    if not blocks:
        return "Nothing to log"
    volume = sum(b.volume for b in blocks)
    what = "; ".join(b.say() for b in blocks)
    total = f" — {volume:,.0f} kg total" if volume else ""
    return f"{what}{total}"


# ── writing ──────────────────────────────────────────────────────────────
def log_session(blocks: list[Block], *, at: str = "", note: str = "") -> dict:
    """Store one session. Returns `{ok, ...}` — never raises for bad input."""
    if not blocks:
        return {"ok": False, "error": "Nothing to log."}

    when = _stamp(at)
    session = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    _conn().executemany(
        "INSERT INTO lifts (id, session_id, exercise, lookup, sets, reps, "
        "weight, rpe, at, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(uuid.uuid4().hex, session, b.exercise, b.exercise.strip().lower(),
          b.sets, b.reps, b.weight, b.rpe, when, b.note or note, now)
         for b in blocks])
    _conn().commit()
    return {"ok": True, "session_id": session, "blocks": len(blocks),
            "volume": round(sum(b.volume for b in blocks), 1),
            "at": when, "detail": summarise_session(blocks)}


def get_session(session_id: str) -> dict | None:
    """One logged session, read back out of the table.

    For verification: `log_session` returning a `session_id` proves it built an
    insert, not that the rows are there. A handler that says "logged" and a
    table that disagrees is exactly the case rung 5 exists to catch.
    """
    if not session_id:
        return None
    rows = _conn().execute(
        "SELECT exercise, sets, reps, weight, at FROM lifts WHERE session_id=?",
        (session_id,)).fetchall()
    if not rows:
        return None
    return {"session_id": session_id, "blocks": len(rows),
            "at": rows[0]["at"],
            "exercises": sorted({r["exercise"] for r in rows})}


def forget_session(session_id: str) -> int:
    """Remove a session that was logged wrong."""
    conn = _conn()
    cursor = conn.execute("DELETE FROM lifts WHERE session_id=?", (session_id,))
    conn.commit()
    return cursor.rowcount


def last_session() -> dict | None:
    """The most recent session, so "undo that" has something to point at."""
    row = _conn().execute(
        "SELECT session_id, at FROM lifts ORDER BY at DESC, created_at DESC "
        "LIMIT 1").fetchone()
    return dict(row) if row else None


# ── reading ──────────────────────────────────────────────────────────────
def exercises() -> list[dict]:
    """Every exercise with a history, most recently trained first.

    The agent checks this before inventing a spelling — otherwise "Squat" and
    "back squat" become two exercises with two half-histories.
    """
    rows = _conn().execute(
        "SELECT exercise, COUNT(*) AS entries, MAX(at) AS last, "
        "MAX(weight) AS heaviest FROM lifts GROUP BY lookup "
        "ORDER BY last DESC").fetchall()
    return [dict(r) for r in rows]


def history(exercise: str, *, days: int = 180, limit: int = 200) -> list[dict]:
    """Every block of one exercise, oldest first."""
    since = (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat()
    rows = _conn().execute(
        "SELECT session_id, exercise, sets, reps, weight, rpe, at, note "
        "FROM lifts WHERE lookup=? AND at>=? ORDER BY at LIMIT ?",
        (str(exercise or "").strip().lower(), since, max(1, limit))).fetchall()
    return [dict(r) for r in rows]


def progress(exercise: str, *, days: int = 180) -> dict:
    """What has happened to one lift: volume, best set, estimated max.

    The arithmetic is done here rather than by a model reading a list of sets,
    for the same reason `metrics.summarise` exists — a model asked to total
    forty sets will produce a number, and it will not be the right one.
    """
    rows = history(exercise, days=days, limit=2000)
    if not rows:
        return {"ok": True, "exercise": exercise, "n": 0,
                "detail": f"No {exercise} logged in the last {days} days."}

    def e1rm(row: dict) -> float:
        return row["weight"] * (1 + row["reps"] / 30)

    best = max(rows, key=e1rm)
    heaviest = max(rows, key=lambda r: r["weight"])
    by_session: dict[str, float] = {}
    for row in rows:
        by_session[row["session_id"]] = by_session.get(row["session_id"], 0.0) + (
            row["sets"] * row["reps"] * row["weight"])

    sessions = len(by_session)
    volumes = list(by_session.values())
    first_half = volumes[: max(1, sessions // 2)]
    second_half = volumes[max(1, sessions // 2):] or first_half

    return {
        "ok": True,
        "exercise": rows[-1]["exercise"],
        "n": len(rows),
        "sessions": sessions,
        "first": rows[0]["at"][:10],
        "last": rows[-1]["at"][:10],
        "total_volume": round(sum(volumes), 1),
        "volume_per_session": round(sum(volumes) / sessions, 1),
        "volume_early": round(sum(first_half) / len(first_half), 1),
        "volume_recent": round(sum(second_half) / len(second_half), 1),
        "heaviest": {"weight": heaviest["weight"], "reps": heaviest["reps"],
                     "at": heaviest["at"][:10]},
        "best_set": {"weight": best["weight"], "reps": best["reps"],
                     "at": best["at"][:10], "estimated_max": round(e1rm(best), 1)},
        "formula": FORMULA,
    }


def weekly_volume(*, days: int = 84) -> list[dict]:
    """Total tonnage per week, for "am I training more or less than I was"."""
    since = (datetime.now(UTC) - timedelta(days=max(7, days))).isoformat()
    rows = _conn().execute(
        "SELECT sets, reps, weight, at FROM lifts WHERE at>=? ORDER BY at",
        (since,)).fetchall()
    weeks: dict[str, float] = {}
    for row in rows:
        with suppressed("bucketing a lift into its week"):
            moment = datetime.fromisoformat(row["at"])
            monday = (moment - timedelta(days=moment.weekday())).date().isoformat()
            weeks[monday] = weeks.get(monday, 0.0) + (
                row["sets"] * row["reps"] * row["weight"])
    return [{"week_of": week, "volume": round(total, 1)}
            for week, total in sorted(weeks.items())]


def _stamp(value: str) -> str:
    raw = str(value or "").strip()
    if raw:
        with suppressed("reading when a session happened"):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).isoformat()
        with suppressed("reading a plain date a session happened"):
            from .core.dateparse import parse_date_range

            start, _end = parse_date_range(raw)
            if start:
                return datetime.fromisoformat(start).replace(tzinfo=UTC).isoformat()
    return datetime.now(UTC).isoformat()
