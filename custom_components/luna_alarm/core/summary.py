"""Builds the morning summary from journal entries.

A journal entry is a dict: {"ts": iso string, "kind": str, "message": str, "data": dict}.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Any

_ACTION_TEXT = {
    "pending": "alert",
    "visitor_info": "visitor",
    "wake": "wake",
    "paused": "ignored (paused)",
    "not_armed": "not armed",
    "car_info": "car",
    "car_escalated": "car (repeated)",
}


def _hhmm(ts: str) -> str:
    return datetime.fromisoformat(ts).strftime("%H:%M")


def build_summary(
    entries: Iterable[dict[str, Any]],
    *,
    test_mode: bool,
    reason: str,
) -> tuple[str, str, dict[str, Any]]:
    """Return (title, push message, details for the summary sensor)."""
    entries = list(entries)
    armed_periods: list[str] = []
    arm_start: str | None = None
    alerts: list[str] = []
    triggered: list[str] = []
    simulated: Counter[str] = Counter()
    harmless = 0
    pauses: list[str] = []
    health: list[str] = []
    per_camera: dict[str, Counter[str]] = {}

    for e in entries:
        kind, data, ts = e["kind"], e.get("data", {}), e["ts"]
        if kind == "state":
            mode, prev = data.get("mode"), data.get("previous")
            if mode in ("armed_away", "armed_night") and prev == "arming":
                arm_start = ts
            elif mode == "disarmed" and prev != "arming":
                if arm_start:
                    armed_periods.append(f"{_hhmm(arm_start)}–{_hhmm(ts)}")
                arm_start = None
        elif kind == "detection":
            cam = data.get("label", data.get("source", "?"))
            per_camera.setdefault(cam, Counter())[data.get("action", "logged")] += 1
        elif kind == "pending":
            alerts.append(f"{_hhmm(ts)} {e['message']}")
        elif kind == "triggered":
            triggered.append(f"{_hhmm(ts)} {e['message']}")
        elif kind == "simulated":
            simulated[data.get("output", "?")] += 1
        elif kind == "harmless":
            harmless += 1
        elif kind == "pause":
            pauses.append(f"{_hhmm(ts)} {data.get('reason', '')}")
        elif kind == "health" and "failed" in e["message"]:
            health.append(f"{_hhmm(ts)} {e['message']}")

    if arm_start:
        armed_periods.append(f"{_hhmm(arm_start)}–now")

    lines: list[str] = []
    if armed_periods:
        lines.append("Armed: " + ", ".join(armed_periods))
    det_total = sum(sum(c.values()) for c in per_camera.values())
    if det_total:
        parts = []
        for cam, counts in sorted(per_camera.items()):
            total = sum(counts.values())
            notable = [f"{n} {_ACTION_TEXT.get(a, a)}" for a, n in counts.items() if a in ("pending", "visitor_info", "car_escalated")]
            parts.append(f"{cam} {total}" + (f" ({', '.join(notable)})" if notable else ""))
        lines.append("Detections: " + "; ".join(parts))
    else:
        lines.append("Detections: none")
    word = "Simulated alarms" if test_mode else "Alarms"
    if triggered:
        lines.append(f"{word}: {len(triggered)}")
    if alerts:
        lines.append(f"Alerts: {len(alerts)} (harmless: {harmless})")
        lines.extend(f"• {a}" for a in alerts[:5])
    else:
        lines.append("Alerts: none")
    if pauses:
        lines.append(f"Pauses: {len(pauses)}")
    if health:
        lines.append(f"Self-check problems: {len(health)}")
        lines.extend(f"• {h}" for h in health[:3])

    title = ("[TEST] " if test_mode else "") + "Alarm summary"
    details = {
        "reason": reason,
        "test_mode": test_mode,
        "armed_periods": armed_periods,
        "detections": {cam: dict(c) for cam, c in per_camera.items()},
        "alerts": alerts,
        "triggered": triggered,
        "simulated_outputs": dict(simulated),
        "harmless": harmless,
        "pauses": pauses,
        "health_problems": health,
        "entries": len(entries),
    }
    return title, "\n".join(lines), details
