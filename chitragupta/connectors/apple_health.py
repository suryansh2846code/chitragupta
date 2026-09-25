"""Apple Health — the user's own export, read on their machine.

There is no API to do this with. HealthKit is iOS-only; the Health app does not
exist on macOS and nothing on a Mac can query it. The one sanctioned path is the
export the Health app itself offers — *Profile → Export All Health Data* — which
produces a zip the user AirDrops or saves, and points us at. Reading a file they
handed us is the most local-first thing in the whole product: nothing leaves the
machine and no account is involved.

Three things shape the implementation.

**The file is enormous.** A few years of an Apple Watch is commonly 300 MB of
XML and can pass a gigabyte. So it is streamed with `iterparse` straight out of
the zip, never extracted and never read whole — and the tree is cleared as it
goes, or the parser retains everything it has seen and the memory cost is the
file size again.

**Most of it is noise.** Steps arrive as hundreds of samples a day. Nobody asks
"how many steps between 14:05 and 14:09", and storing each sample is millions of
rows to answer a question in the shape of "how many steps on Tuesday". So
cumulative metrics are **summed per day** and only readings that are genuinely
point-in-time — weight, resting heart rate, VO2 max — are kept individually.

**It is re-exported, not updated.** The user will point at a new export in three
months, overlapping the old one entirely. Dedup is the unique index in
`metrics.py` on (metric, at, source); a second import of the same day upserts
the same row rather than doubling it.
"""
from __future__ import annotations

import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..log import get_logger, suppressed
from .export_file import ExportConnector

log = get_logger(__name__)

#: Apple's identifiers → ours. Anything not in here is skipped, deliberately:
#: Health records well over a hundred types and an agent does not get better at
#: training advice for knowing the user's audiogram.
QUANTITIES = {
    "HKQuantityTypeIdentifierBodyMass": "weight",
    "HKQuantityTypeIdentifierBodyFatPercentage": "body_fat",
    "HKQuantityTypeIdentifierWaistCircumference": "waist",
    "HKQuantityTypeIdentifierHeight": "height",
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2max",
    "HKQuantityTypeIdentifierDietaryEnergyConsumed": "energy_in",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "energy_out",
    "HKQuantityTypeIdentifierDietaryProtein": "protein",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "distance",
    "HKQuantityTypeIdentifierAppleExerciseTime": "workout_minutes",
}

#: Summed per day. The rest are kept reading by reading.
DAILY = {"steps", "energy_in", "energy_out", "protein", "distance",
         "workout_minutes", "sleep"}

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
#: Apple splits sleep into core/deep/REM plus "InBed", which is not sleep —
#: counting it would add an hour of lying awake to every night.
ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
}

#: How often to look at the stop flag and report progress. Per-record would
#: cost more than the parse.
CHECK_EVERY = 5000


class AppleHealthConnector(ExportConnector):
    name = "apple_health"
    runs_on_device = True
    label = "Apple Health"
    platforms = ("darwin",)

    SETUP_HINT = ("choose your Apple Health export — on your iPhone, "
                  "Health → your picture → Export All Health Data")
    NOT_OURS = ("That file is not an Apple Health export. It should be the zip "
                "the Health app gives you, usually called export.zip.")

    # ── the parse ────────────────────────────────────────────────────────
    def _read(self, source: Path, *, cancel=None,
              progress=None) -> tuple[list[dict], bool]:
        """Every reading we care about, and whether we were stopped."""
        import xml.etree.ElementTree as ET

        points: list[dict] = []
        totals: dict[tuple[str, str], float] = defaultdict(float)
        seen = 0
        stopped = False

        try:
            stream = _open_export(source)
        except zipfile.BadZipFile as exc:
            raise ValueError(self.NOT_OURS) from exc

        with stream:
            # `iterparse` on "end" so each element is complete when seen, and
            # cleared immediately after — without the clear the parser keeps
            # every record it has read and the peak memory is the file size.
            for _event, element in ET.iterparse(stream, events=("end",)):
                tag = element.tag
                if tag == "Record":
                    self._record(element, points, totals)
                elif tag == "Workout":
                    self._workout(element, totals)
                elif tag not in ("HealthData", "ExportDate", "Me"):
                    element.clear()
                    continue

                element.clear()
                seen += 1
                if seen % CHECK_EVERY == 0:
                    if cancel is not None and cancel.is_set():
                        stopped = True
                        break
                    if progress is not None:
                        progress(seen, 0, f"{seen:,} records read")

        for (metric, day), total in totals.items():
            points.append({"metric": metric, "value": total,
                           "unit": _UNIT_FOR[metric], "at": f"{day}T00:00:00+00:00",
                           "note": "daily total"})
        return points, stopped

    def _record(self, element: Any, points: list[dict],
                totals: dict[tuple[str, str], float]) -> None:
        kind = element.get("type", "")

        if kind == SLEEP_TYPE:
            if element.get("value", "") not in ASLEEP_VALUES:
                return
            hours = _hours_between(element.get("startDate"), element.get("endDate"))
            day = _day(element.get("startDate"))
            if hours and day:
                totals[("sleep", day)] += hours
            return

        metric = QUANTITIES.get(kind)
        if metric is None:
            return
        with suppressed("reading one Apple Health record"):
            value = float(element.get("value"))
            unit = element.get("unit") or ""
            when = element.get("startDate") or element.get("endDate")
            if metric == "body_fat":
                value = _as_percentage(value)
            if metric in DAILY:
                day = _day(when)
                if day:
                    totals[(metric, day)] += _to_canonical(metric, value, unit)
                return
            moment = _moment(when)
            if moment:
                points.append({"metric": metric, "value": value, "unit": unit,
                               "at": moment, "note": ""})

    def _workout(self, element: Any, totals: dict[tuple[str, str], float]) -> None:
        """A workout contributes its length, and its distance if it has one."""
        day = _day(element.get("startDate"))
        if not day:
            return
        with suppressed("reading one Apple Health workout"):
            minutes = _to_canonical(
                "workout_minutes", float(element.get("duration") or 0),
                element.get("durationUnit") or "min")
            if minutes:
                totals[("workout_minutes", day)] += minutes
        with suppressed("reading a workout's distance"):
            distance = element.get("totalDistance")
            if distance:
                totals[("distance", day)] += _to_canonical(
                    "distance", float(distance),
                    element.get("totalDistanceUnit") or "km")


def _open_export(source: Path):
    """The XML stream, out of the zip or straight off disk.

    Accepts the unzipped `export.xml` too — somebody will unzip it first, and
    refusing that would be refusing the same data for the shape of its wrapper.
    """
    if source.suffix.lower() == ".xml":
        return source.open("rb")
    archive = zipfile.ZipFile(source)
    for name in archive.namelist():
        if name.endswith("export.xml") and "cda" not in name.lower():
            return archive.open(name)
    archive.close()
    raise ValueError(
        "That zip has no export.xml in it, so it is not an Apple Health export.")


def _to_canonical(metric: str, value: float, unit: str) -> float:
    from ..metrics import METRICS, convert

    found = METRICS.get(metric)
    if found is None:
        return 0.0
    converted, problem = convert(found, value, unit)
    return 0.0 if problem else converted


#: The unit a daily total is already in, since it was converted on the way in.
_UNIT_FOR = {
    "steps": "steps", "energy_in": "kcal", "energy_out": "kcal",
    "protein": "g", "distance": "km", "workout_minutes": "min",
    "sleep": "hours",
}


def _as_percentage(value: float) -> float:
    """Apple writes body fat as a fraction in some exports and a percent in others.

    Nobody has one percent body fat and nobody has ninety, so the two ranges do
    not overlap and this is a safe read rather than a guess.
    """
    return value * 100 if 0 < value <= 1 else value


def _parse(stamp: str | None) -> datetime | None:
    """Apple's `2026-01-01 08:00:00 +0000`, which is not ISO 8601."""
    raw = str(stamp or "").strip()
    if not raw:
        return None
    with suppressed("reading an Apple Health timestamp"):
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
    with suppressed("reading an Apple Health timestamp without a zone"):
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return None


def _moment(stamp: str | None) -> str:
    parsed = _parse(stamp)
    return parsed.isoformat() if parsed else ""


def _day(stamp: str | None) -> str:
    parsed = _parse(stamp)
    return parsed.date().isoformat() if parsed else ""


def _hours_between(start: str | None, end: str | None) -> float:
    first, last = _parse(start), _parse(end)
    if not first or not last or last <= first:
        return 0.0
    return (last - first).total_seconds() / 3600
