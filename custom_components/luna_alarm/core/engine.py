"""The Luna Alarm state machine.

`Engine.handle(event)` updates the state and returns a list of commands. It never sleeps
and never reads the clock: every event carries its own timestamp, and the HA layer asks
`next_deadline()` when to send the next `Tick`. Test mode is enforced here (outputs become
log entries, critical alerts become normal ones) and again in the HA layer.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from .models import (
    ALARM_MODES,
    ARMED_MODES,
    ArmRequest,
    BuildingModeChanged,
    Command,
    Config,
    Detection,
    DisarmRequest,
    Event,
    Harmless,
    HealthChanged,
    KillswitchPressed,
    Level,
    LockChanged,
    Log,
    MasterSwitch,
    Mode,
    NightEnd,
    Notify,
    Output,
    PauseRequest,
    PresenceChanged,
    Press,
    Role,
    SendSummary,
    SoundNow,
    StateChanged,
    SummaryDue,
    TestModeSwitch,
    Tick,
)

MORNING_CUTOFF = time(12, 0)  # an inside unlock between night_end and noon ends the night
SUMMARY_MIN_GAP = timedelta(hours=20)

_MODE_NAMES = {
    Mode.ARMED_AWAY: "Away protection",
    Mode.ARMED_NIGHT: "Night watch",
}


def _mins(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    return f"{secs // 60} min" if secs >= 60 and secs % 60 == 0 else f"{secs} s"


class Engine:
    """Deterministic alarm logic. One instance per config entry."""

    def __init__(
        self,
        config: Config,
        *,
        master_on: bool = True,
        test_mode: bool = False,
    ) -> None:
        self.config = config
        self.mode: Mode = Mode.DISARMED if master_on else Mode.DISABLED
        self.armed_mode: Mode | None = None  # target while arming, origin while pending/triggered
        self.test_mode = test_mode

        self.arming_until: datetime | None = None
        self.pending_until: datetime | None = None
        self.triggered_until: datetime | None = None
        self.siren_until: datetime | None = None
        self.pause_until: datetime | None = None
        self.wake_until: datetime | None = None
        self.leave_since: datetime | None = None

        self.visitor_hits: list[datetime] = []
        self.visitor_notified = False
        self.car_hits: list[datetime] = []
        self.car_escalated = False

        self.anyone_home = True
        self.is_night = False
        self.locked = False
        self.night_done = False  # latch: night already ended this cycle, don't re-arm

        self.health_ok = True
        self.failing: tuple[str, ...] = ()
        self.health_alerted = False

        self.alarm_reason = ""
        self.alarm_source = ""
        self.last_summary_at: datetime | None = None

        self._out: list[Command] = []
        self._now: datetime | None = None

    # ------------------------------------------------------------------ public API

    def handle(self, event: Event) -> list[Command]:
        self._out = []
        self._now = event.at
        handler = getattr(self, f"_on_{type(event).__name__}", None)
        if handler is None:
            raise TypeError(f"Unhandled event {type(event).__name__}")
        handler(event)
        if not isinstance(event, Tick):
            self._resolve_due(event.at)
        out, self._out = self._out, []
        return out

    def next_deadline(self) -> datetime | None:
        candidates = [
            self.arming_until,
            self.pending_until,
            self.triggered_until,
            self.siren_until,
            self.pause_until,
            self.wake_until,
        ]
        if self.leave_since is not None:
            candidates.append(self.leave_since + self.config.leave_delay)
        if self.car_hits:
            candidates.append(self.car_hits[0] + self.config.car_window)
        due = [c for c in candidates if c is not None]
        return min(due) if due else None

    @property
    def paused(self) -> bool:
        return self.pause_until is not None and self._now is not None and self._now < self.pause_until

    def snapshot(self) -> dict[str, Any]:
        """Everything needed to survive an HA restart."""

        def iso(v: datetime | None) -> str | None:
            return v.isoformat() if v else None

        return {
            "mode": self.mode.value,
            "armed_mode": self.armed_mode.value if self.armed_mode else None,
            "test_mode": self.test_mode,
            "arming_until": iso(self.arming_until),
            "pending_until": iso(self.pending_until),
            "triggered_until": iso(self.triggered_until),
            "siren_until": iso(self.siren_until),
            "pause_until": iso(self.pause_until),
            "wake_until": iso(self.wake_until),
            "leave_since": iso(self.leave_since),
            "visitor_hits": [iso(v) for v in self.visitor_hits],
            "visitor_notified": self.visitor_notified,
            "anyone_home": self.anyone_home,
            "is_night": self.is_night,
            "locked": self.locked,
            "night_done": self.night_done,
            "health_alerted": self.health_alerted,
            "alarm_reason": self.alarm_reason,
            "alarm_source": self.alarm_source,
            "last_summary_at": iso(self.last_summary_at),
        }

    def restore(self, data: dict[str, Any]) -> None:
        def dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        self.mode = Mode(data.get("mode", self.mode))
        self.armed_mode = Mode(data["armed_mode"]) if data.get("armed_mode") else None
        self.test_mode = bool(data.get("test_mode", self.test_mode))
        self.arming_until = dt(data.get("arming_until"))
        self.pending_until = dt(data.get("pending_until"))
        self.triggered_until = dt(data.get("triggered_until"))
        self.siren_until = dt(data.get("siren_until"))
        self.pause_until = dt(data.get("pause_until"))
        self.wake_until = dt(data.get("wake_until"))
        self.leave_since = dt(data.get("leave_since"))
        self.visitor_hits = [d for d in (dt(v) for v in data.get("visitor_hits", [])) if d]
        self.visitor_notified = bool(data.get("visitor_notified", False))
        self.anyone_home = bool(data.get("anyone_home", True))
        self.is_night = bool(data.get("is_night", False))
        self.locked = bool(data.get("locked", False))
        self.night_done = bool(data.get("night_done", False))
        self.health_alerted = bool(data.get("health_alerted", False))
        self.alarm_reason = data.get("alarm_reason", "")
        self.alarm_source = data.get("alarm_source", "")
        self.last_summary_at = dt(data.get("last_summary_at"))

    # ------------------------------------------------------------------ emit helpers

    def _log(self, entry_kind: str, entry_message: str, /, **data: Any) -> None:
        self._out.append(Log(kind=entry_kind, message=entry_message, data=data))

    def _notify(
        self,
        title: str,
        message: str,
        level: Level = Level.NORMAL,
        *,
        actionable: bool = False,
        source: str = "",
        tag: str = "",
    ) -> None:
        if level is Level.INFO:
            self._log("info", f"{title}: {message}", source=source)
            return
        if self.test_mode:
            title = f"[TEST] {title}"
            if level is Level.CRITICAL:
                level = Level.NORMAL
        self._log("notify", f"{title}: {message}", level=level.value, source=source)
        self._out.append(
            Notify(title=title, message=message, level=level, actionable=actionable, source=source, tag=tag)
        )

    def _output(self, name: str, seconds: int = 0) -> None:
        if self.test_mode:
            self._log("simulated", f"Would run output: {name}", output=name, seconds=seconds)
            return
        self._log("output", f"Output: {name}", output=name, seconds=seconds)
        self._out.append(Output(name=name, seconds=seconds))

    def _set_mode(self, new: Mode) -> None:
        if new is self.mode:
            return
        prev, self.mode = self.mode, new
        self._log("state", f"{prev.value} → {new.value}", previous=prev.value, mode=new.value)
        self._out.append(StateChanged(mode=new, previous=prev))

    def _summary(self, reason: str) -> None:
        self.last_summary_at = self._now
        self._out.append(SendSummary(reason=reason))

    # ------------------------------------------------------------------ transitions

    def _clear_timers(self) -> None:
        self.arming_until = None
        self.pending_until = None
        self.triggered_until = None
        self.siren_until = None
        self.pause_until = None
        self._clear_wake()

    def _clear_wake(self) -> None:
        self.wake_until = None
        self.visitor_hits = []
        self.visitor_notified = False

    def _stop_outputs(self) -> None:
        for name in ("sirens_off", "audio_off", "flash_off", "restore"):
            self._output(name)

    def _arm(self, target: Mode, at: datetime, who: str) -> None:
        name = _MODE_NAMES[target]
        if self.mode is Mode.DISABLED:
            self._log("arming_blocked", f"{name} not armed: system disabled", who=who)
            if who != "auto":
                self._notify("Not armed", "The alarm system is disabled (master switch off).")
            return
        if self.mode is not Mode.DISARMED:
            self._log("arming_ignored", f"{name} requested while {self.mode.value}", who=who)
            return
        if not self.health_ok:
            failing = ", ".join(self.failing) or "unknown"
            self._log("arming_blocked", f"{name} not armed: self-check failed", failing=list(self.failing), who=who)
            self._notify("Not armed", f"{name} did not arm. Self-check failed: {failing}.")
            return
        delay = self.config.arming_night if target is Mode.ARMED_NIGHT else self.config.arming_away
        self.armed_mode = target
        self.arming_until = at + delay
        self._set_mode(Mode.ARMING)
        self._log("arming", f"{name} arming in {_mins(delay)}", target=target.value, who=who)
        if target is Mode.ARMED_NIGHT:
            self._notify("Night watch", f"Night watch arms in {_mins(delay)}.", tag="luna-alarm-arming")

    def _disarm(self, who: str, reason: str) -> None:
        if self.mode in (Mode.DISABLED, Mode.DISARMED):
            return
        was_alarm = self.mode in ALARM_MODES
        origin = self.armed_mode
        self._clear_timers()
        if was_alarm:
            self._stop_outputs()
        self.armed_mode = None
        self._set_mode(Mode.DISARMED)
        name = _MODE_NAMES.get(origin, "Alarm") if origin else "Alarm"
        by = f" by {who}" if who else ""
        self._log("disarm", f"{name} disarmed{by} ({reason})", who=who, reason=reason, origin=origin.value if origin else None)
        self._notify("Disarmed", f"{name} off{by} ({reason}).", tag="luna-alarm-state")

    def _end_night(self, who: str, reason: str) -> None:
        self.night_done = True
        if self._night_active():
            self._disarm(who, reason)
            self._summary(reason)

    def _night_active(self) -> bool:
        return self.armed_mode is Mode.ARMED_NIGHT and self.mode is not Mode.DISARMED

    def _away_active(self) -> bool:
        return self.armed_mode is Mode.ARMED_AWAY and self.mode is not Mode.DISARMED

    def _pending(self, at: datetime, reason: str, source: str = "") -> None:
        if self.mode not in ARMED_MODES:
            return
        night = self.mode is Mode.ARMED_NIGHT
        window = self.config.pending_night if night else self.config.pending_away
        self.alarm_reason, self.alarm_source = reason, source
        self.pending_until = at + window
        self._set_mode(Mode.PENDING)
        self._log("pending", reason, source=source, origin=self.armed_mode.value if self.armed_mode else None)
        self._output("snapshot")
        if night:
            self._output("cam_lights_on")
            self._output("flash_on")
        note = " Test mode: nothing will sound." if self.test_mode else ""
        self._notify(
            "Alarm: respond now",
            f"{reason}. Sirens in {_mins(window)} unless someone responds.{note}",
            Level.CRITICAL,
            actionable=True,
            source=source,
            tag="luna-alarm-alert",
        )

    def _trigger(self, at: datetime, why: str) -> None:
        self.pending_until = None
        self.triggered_until = at + self.config.trigger_time
        self.siren_until = at + self.config.siren_time
        self._set_mode(Mode.TRIGGERED)
        self._log("triggered", f"Alarm triggered ({why}): {self.alarm_reason}", why=why, source=self.alarm_source)
        self._output("sirens_on", int(self.config.siren_time.total_seconds()))
        self._output("cam_lights_on")
        self._output("flash_on")
        self._output("audio_on")
        if self.test_mode:
            msg = f"{self.alarm_reason}. Sirens, lights and audio would be running now (simulated)."
        else:
            msg = f"{self.alarm_reason}. Sirens are sounding."
        self._notify("ALARM triggered", msg, Level.CRITICAL, actionable=True, source=self.alarm_source, tag="luna-alarm-alert")

    def _pause(self, at: datetime, duration: timedelta, reason: str, who: str = "") -> None:
        if self.mode not in ARMED_MODES:
            self._log("pause_ignored", f"Pause requested while {self.mode.value} ({reason})", reason=reason)
            return
        until = at + duration
        if self.pause_until and self.pause_until >= until:
            self._log("pause_ignored", f"Already paused longer ({reason})", reason=reason)
            return
        extending = self.paused
        self.pause_until = until
        name = _MODE_NAMES[self.mode]
        self._log("pause", f"{name} paused {_mins(duration)}: {reason}", reason=reason, who=who, seconds=int(duration.total_seconds()))
        verb = "Pause extended" if extending else "Paused"
        self._notify(name, f"{verb} for {_mins(duration)}: {reason}.", tag="luna-alarm-pause")

    def _car(self, ev: Detection) -> None:
        window = self.config.car_window
        self.car_hits = [h for h in self.car_hits if ev.at - h < window] + [ev.at]
        if not ev.dark:
            self._log("detection", f"{ev.label or ev.source}: {ev.kind} (daylight, logged)", **self._det(ev, "logged"))
            return
        if len(self.car_hits) >= self.config.car_hits and not self.car_escalated:
            self.car_escalated = True
            self._log("detection", f"{ev.label}: repeated {ev.kind}", **self._det(ev, "car_escalated"))
            self._notify("Car watch", f"Repeated activity at the car ({len(self.car_hits)}× in {_mins(window)}).", Level.CRITICAL, source=ev.source)
        elif len(self.car_hits) == 1:
            self._log("detection", f"{ev.label}: {ev.kind}", **self._det(ev, "car_info"))
            self._notify("Car watch", f"{ev.kind.capitalize()} detected at the car.", source=ev.source)
        else:
            self._log("detection", f"{ev.label}: {ev.kind}", **self._det(ev, "logged"))

    @staticmethod
    def _det(ev: Detection, action: str, **extra: Any) -> dict[str, Any]:
        return {"source": ev.source, "label": ev.label or ev.source, "role": ev.role.value, "kind": ev.kind, "action": action, **extra}

    # ------------------------------------------------------------------ deadlines

    def _resolve_due(self, now: datetime) -> None:
        if self.arming_until and now >= self.arming_until and self.mode is Mode.ARMING:
            self.arming_until = None
            target = self.armed_mode or Mode.ARMED_AWAY
            self._set_mode(target)
            self._clear_wake()
            self._notify(_MODE_NAMES[target], f"{_MODE_NAMES[target]} armed.", tag="luna-alarm-state")

        if self.pending_until and now >= self.pending_until and self.mode is Mode.PENDING:
            self._trigger(now, "no response")

        if self.siren_until and now >= self.siren_until:
            self.siren_until = None
            self._output("sirens_off")
            self._output("audio_off")

        if self.triggered_until and now >= self.triggered_until and self.mode is Mode.TRIGGERED:
            self.triggered_until = None
            self._output("flash_off")
            self._output("restore")
            target = self.armed_mode or Mode.ARMED_AWAY
            self._set_mode(target)
            self._clear_wake()
            self._notify("Alarm ended", f"Alarm time ran out. {_MODE_NAMES[target]} is armed again.", Level.CRITICAL)

        if self.pause_until and now >= self.pause_until:
            self.pause_until = None
            if self.mode in ARMED_MODES:
                name = _MODE_NAMES[self.mode]
                self._log("resume", f"{name} resumed")
                self._notify(name, f"{name} resumed.", tag="luna-alarm-pause")

        if self.wake_until and now >= self.wake_until:
            self._log("wake_closed", "Wake window closed", hits=len(self.visitor_hits))
            self._clear_wake()

        if self.car_hits and now - self.car_hits[0] >= self.config.car_window:
            self.car_hits = [h for h in self.car_hits if now - h < self.config.car_window]
            if not self.car_hits:
                self.car_escalated = False

        if (
            self.leave_since is not None
            and now - self.leave_since >= self.config.leave_delay
            and not self.anyone_home
        ):
            self.leave_since = None
            if self.config.auto_away and self.mode is Mode.DISARMED:
                self._arm(Mode.ARMED_AWAY, now, "auto")

    # ------------------------------------------------------------------ handlers

    def _on_Tick(self, ev: Tick) -> None:
        self._resolve_due(ev.at)

    def _on_Detection(self, ev: Detection) -> None:
        self._resolve_due(ev.at)  # close an expired wake window before judging
        label = ev.label or ev.source
        if self.mode in (Mode.DISABLED, Mode.DISARMED, Mode.ARMING):
            self._log("detection", f"{label}: {ev.kind} (not armed)", **self._det(ev, "not_armed"))
            return
        if self.mode in ALARM_MODES:
            self._log("detection", f"{label}: {ev.kind} (during alarm)", **self._det(ev, "during_alarm"))
            return
        if self.paused:
            self._log("detection", f"{label}: {ev.kind} (paused, ignored)", **self._det(ev, "paused"))
            return

        if self.mode is Mode.ARMED_NIGHT:
            if ev.role in (Role.VISITOR, Role.OFFLIMITS):
                self._log("detection", f"{label}: {ev.kind}", **self._det(ev, "pending"))
                self._pending(ev.at, f"Person detected: {label}", ev.source)
            else:
                self._log("detection", f"{label}: {ev.kind} (night, report only)", **self._det(ev, "logged"))
            return

        # ARMED_AWAY
        if ev.role is Role.WAKE:
            self.wake_until = ev.at + self.config.wake_window
            self._log("detection", f"{label}: {ev.kind} → wake window open", **self._det(ev, "wake"))
        elif ev.role is Role.VISITOR:
            awake = self.wake_until is not None and ev.at < self.wake_until
            if not awake:
                self._log("detection", f"{label}: {ev.kind} without passing the gate", **self._det(ev, "pending", reason="no_wake"))
                self._pending(ev.at, f"Person at {label} without passing the gate cameras", ev.source)
                return
            self.visitor_hits = [h for h in self.visitor_hits if ev.at - h < self.config.wake_window] + [ev.at]
            self.wake_until = ev.at + self.config.wake_window
            if len(self.visitor_hits) >= self.config.visitor_hits:
                self._log("detection", f"{label}: {ev.kind} ({len(self.visitor_hits)}×)", **self._det(ev, "pending", reason="repeated"))
                self._pending(ev.at, f"Person keeps returning to {label} ({len(self.visitor_hits)}×)", ev.source)
            elif not self.visitor_notified:
                self.visitor_notified = True
                self._log("detection", f"{label}: visitor", **self._det(ev, "visitor_info"))
                self._notify("Visitor", f"Someone is at {label}.", source=ev.source, tag="luna-alarm-visitor")
            else:
                self._log("detection", f"{label}: {ev.kind} ({len(self.visitor_hits)}×)", **self._det(ev, "logged"))
        elif ev.role is Role.OFFLIMITS:
            self._log("detection", f"{label}: {ev.kind}", **self._det(ev, "pending"))
            self._pending(ev.at, f"Person detected: {label}", ev.source)
        elif ev.role is Role.CONTACT:
            self._log("detection", f"{label} opened", **self._det(ev, "pending"))
            self._pending(ev.at, f"{label} opened", ev.source)
        elif ev.role is Role.CAR:
            self._car(ev)

    def _on_PresenceChanged(self, ev: PresenceChanged) -> None:
        self.anyone_home = ev.anyone_home
        if ev.anyone_home:
            self.leave_since = None
        elif self.leave_since is None:
            self.leave_since = ev.at
        if ev.left:
            self._log("presence", f"Left: {', '.join(ev.left)}", left=list(ev.left), anyone_home=ev.anyone_home)
        for name in ev.arrived:
            self._log("presence", f"Arrived: {name}", arrived=name)
            if self._away_active():
                self._disarm(name, "arrived home")
            elif self._night_active() and self.mode in ARMED_MODES:
                self._pause(ev.at, self.config.pause_arrival, f"{name} arrived home", name)

    def _on_BuildingModeChanged(self, ev: BuildingModeChanged) -> None:
        if ev.is_night == self.is_night:
            return
        self.is_night = ev.is_night
        self.night_done = False
        self._log("building_mode", "Building mode: night" if ev.is_night else "Building mode: day", is_night=ev.is_night)
        if ev.is_night:
            self._try_auto_night(ev.at)
        elif self._night_active():
            self._end_night("", "building mode left night")

    def _on_LockChanged(self, ev: LockChanged) -> None:
        self.locked = ev.locked
        self._log("lock", "Locked" if ev.locked else f"Unlocked ({'inside' if ev.inside else 'outside'})", locked=ev.locked, inside=ev.inside)
        if ev.locked:
            self._try_auto_night(ev.at)
            return
        if not self._night_active():
            return
        local = ev.at.timetz().replace(tzinfo=None)
        morning = self.config.night_end <= local < MORNING_CUTOFF
        if ev.inside and morning:
            self._end_night("", "unlocked after night end")
        elif ev.inside and self.mode in ARMED_MODES:
            self._pause(ev.at, self.config.pause_unlock, "front door unlocked from inside")
        elif not ev.inside:
            self._notify("Night watch", "Front door unlocked from outside during the night watch.")

    def _try_auto_night(self, at: datetime) -> None:
        if (
            self.config.auto_night
            and self.is_night
            and self.locked
            and self.anyone_home
            and not self.night_done
            and self.mode is Mode.DISARMED
        ):
            self._arm(Mode.ARMED_NIGHT, at, "auto")

    def _on_NightEnd(self, ev: NightEnd) -> None:
        if self._night_active():
            self._end_night("", "night end time")
        elif self.is_night:
            self.night_done = True  # don't re-arm this morning if the lock is touched

    def _on_SummaryDue(self, ev: SummaryDue) -> None:
        if self.last_summary_at is None or ev.at - self.last_summary_at >= SUMMARY_MIN_GAP:
            self._summary("daily")

    def _on_ArmRequest(self, ev: ArmRequest) -> None:
        if ev.mode not in ARMED_MODES:
            raise ValueError(f"Cannot arm to {ev.mode}")
        self._arm(ev.mode, ev.at, ev.who or "manual")

    def _on_DisarmRequest(self, ev: DisarmRequest) -> None:
        if self._night_active():
            self.night_done = True
            self._disarm(ev.who, ev.reason)
            self._summary(ev.reason)
        else:
            self._disarm(ev.who, ev.reason)

    def _on_Harmless(self, ev: Harmless) -> None:
        if self.mode not in ALARM_MODES:
            self._log("response_ignored", f"'Harmless' from {ev.who or 'someone'} while {self.mode.value}")
            return
        target = self.armed_mode or Mode.ARMED_AWAY
        self._stop_outputs()
        self.pending_until = self.triggered_until = self.siren_until = None
        self._clear_wake()
        delay = self.config.arming_night if target is Mode.ARMED_NIGHT else self.config.arming_away
        self.arming_until = ev.at + delay
        self._set_mode(Mode.ARMING)
        who = ev.who or "Someone"
        self._log("harmless", f"{who} marked it harmless", who=ev.who, reason=self.alarm_reason)
        self._notify("All clear", f"{who} marked it harmless. {_MODE_NAMES[target]} re-arms in {_mins(delay)}.", tag="luna-alarm-alert")

    def _on_SoundNow(self, ev: SoundNow) -> None:
        if self.mode is Mode.PENDING:
            self._log("sound_now", f"{ev.who or 'Someone'} sounded the alarm", who=ev.who)
            self._trigger(ev.at, f"sounded by {ev.who or 'user'}")
        else:
            self._log("response_ignored", f"'Sound now' while {self.mode.value}")

    def _on_PauseRequest(self, ev: PauseRequest) -> None:
        self._pause(ev.at, ev.duration, ev.reason, ev.who)

    def _on_KillswitchPressed(self, ev: KillswitchPressed) -> None:
        self._log("killswitch", f"Killswitch: {ev.press.value}", press=ev.press.value, anyone_home=self.anyone_home)
        if not self.anyone_home:
            if self.mode in ARMED_MODES:
                self._pending(ev.at, "Inside killswitch pressed while nobody is home")
            else:
                self._notify("Killswitch", "Inside killswitch pressed while nobody is home.")
            return
        if ev.press is Press.SINGLE:
            self._pause(ev.at, self.config.pause_button, "killswitch")
        elif ev.press is Press.DOUBLE:
            self._on_DisarmRequest(DisarmRequest(at=ev.at, who="killswitch", reason="killswitch"))
        elif ev.press is Press.LONG:
            self._on_MasterSwitch(MasterSwitch(at=ev.at, on=False, who="killswitch"))

    def _on_MasterSwitch(self, ev: MasterSwitch) -> None:
        by = f" by {ev.who}" if ev.who else ""
        if not ev.on and self.mode is not Mode.DISABLED:
            if self.mode in ALARM_MODES:
                self._stop_outputs()
            self._clear_timers()
            self.armed_mode = None
            self._set_mode(Mode.DISABLED)
            self._notify("Alarm system", f"Disabled{by}. Nothing will arm until it is switched on again.", tag="luna-alarm-state")
        elif ev.on and self.mode is Mode.DISABLED:
            self._set_mode(Mode.DISARMED)
            self._notify("Alarm system", f"Enabled{by}.", tag="luna-alarm-state")
            self._try_auto_night(ev.at)

    def _on_TestModeSwitch(self, ev: TestModeSwitch) -> None:
        if ev.on == self.test_mode:
            return
        self.test_mode = ev.on
        self._log("test_mode", f"Test mode {'on' if ev.on else 'off'}", on=ev.on, who=ev.who)
        if ev.on:
            self._notify("Alarm system", "Test mode on: everything is logged and simulated. No sirens, lights or audio.")
        else:
            self._notify("Alarm system", "Test mode off: alarms are live.", Level.CRITICAL)

    def _on_HealthChanged(self, ev: HealthChanged) -> None:
        was_ok = self.health_ok
        self.health_ok, self.failing = ev.ok, ev.failing
        if ev.ok:
            if not was_ok:
                self._log("health", "Self-check OK")
            if self.health_alerted:
                self.health_alerted = False
                self._notify("Self-check", "All monitored devices are back.")
            return
        failing = ", ".join(ev.failing) or "unknown"
        self._log("health", f"Self-check failed: {failing}", failing=list(ev.failing))
        if self.mode is Mode.ARMING:
            self.arming_until = None
            self.armed_mode = None
            self._set_mode(Mode.DISARMED)
            self._notify("Not armed", f"Arming aborted. Self-check failed: {failing}.")
        elif self.mode in ARMED_MODES or self.mode in ALARM_MODES:
            if not self.health_alerted:
                self.health_alerted = True
                self._notify("Self-check failed", f"Lost while armed: {failing}. Possible tampering. System stays armed.", Level.CRITICAL)
