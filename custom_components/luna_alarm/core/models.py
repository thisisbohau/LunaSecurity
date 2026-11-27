"""Plain data types for the Luna Alarm state machine.

Nothing in core/ imports Home Assistant. The engine takes events with a timestamp and
returns commands; the HA layer translates entity changes into events and commands into
service calls. That split is what makes the logic testable without HA.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    """States of the alarm. Values match HA's alarm_control_panel states where one exists."""

    DISABLED = "disabled"  # master switch off; nothing arms
    DISARMED = "disarmed"
    ARMING = "arming"
    ARMED_AWAY = "armed_away"
    ARMED_NIGHT = "armed_night"
    PENDING = "pending"  # response window: critical alert + countdown
    TRIGGERED = "triggered"


ARMED_MODES = (Mode.ARMED_AWAY, Mode.ARMED_NIGHT)
ALARM_MODES = (Mode.PENDING, Mode.TRIGGERED)


class Role(StrEnum):
    """What a detection source means for the decision."""

    WAKE = "wake"  # outside the gate: doorbell, garage. Opens the wake window.
    VISITOR = "visitor"  # entry zone inside the gate (E42): tiered.
    OFFLIMITS = "offlimits"  # garden/fence: person = pending while armed.
    CONTACT = "contact"  # window contacts: pending in away mode.
    CAR = "car"  # garage car watch: notifications only, never pending.


class Level(StrEnum):
    """Notification urgency. In test mode CRITICAL is sent as NORMAL."""

    INFO = "info"  # log + summary only, no push
    NORMAL = "normal"
    CRITICAL = "critical"


class Press(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    LONG = "long"


# --------------------------------------------------------------------------- config


@dataclass(slots=True)
class Config:
    """Tunable values. Defaults are the guesses from the planning board."""

    arming_away: timedelta = timedelta(seconds=60)
    arming_night: timedelta = timedelta(minutes=5)
    pending_away: timedelta = timedelta(seconds=120)
    pending_night: timedelta = timedelta(seconds=60)
    trigger_time: timedelta = timedelta(minutes=10)
    siren_time: timedelta = timedelta(minutes=3)
    wake_window: timedelta = timedelta(minutes=3)
    visitor_hits: int = 3  # person hits at the entry within one wake window -> pending
    leave_delay: timedelta = timedelta(minutes=2)  # everyone away this long -> auto-arm away
    pause_unlock: timedelta = timedelta(minutes=5)
    pause_arrival: timedelta = timedelta(minutes=10)
    pause_button: timedelta = timedelta(minutes=5)
    night_end: time = time(6, 30)
    car_window: timedelta = timedelta(minutes=10)
    car_hits: int = 3  # car events within car_window -> escalated notification
    auto_away: bool = True
    auto_night: bool = True


# --------------------------------------------------------------------------- events


@dataclass(slots=True, kw_only=True)
class Event:
    at: datetime


@dataclass(slots=True, kw_only=True)
class Tick(Event):
    """Time passed; resolve any deadline that is due."""


@dataclass(slots=True, kw_only=True)
class Detection(Event):
    source: str  # entity id
    role: Role
    kind: str = "person"  # person, vehicle, open, ...
    label: str = ""  # friendly name for messages
    dark: bool = False  # sun below horizon (car watch)


@dataclass(slots=True, kw_only=True)
class PresenceChanged(Event):
    anyone_home: bool
    arrived: tuple[str, ...] = ()  # names that just arrived
    left: tuple[str, ...] = ()


@dataclass(slots=True, kw_only=True)
class BuildingModeChanged(Event):
    is_night: bool


@dataclass(slots=True, kw_only=True)
class LockChanged(Event):
    locked: bool
    inside: bool = True  # unlocked from the inside (thumb turn / button)


@dataclass(slots=True, kw_only=True)
class ArmRequest(Event):
    mode: Mode  # ARMED_AWAY or ARMED_NIGHT
    who: str = ""


@dataclass(slots=True, kw_only=True)
class DisarmRequest(Event):
    who: str = ""
    reason: str = "manual"


@dataclass(slots=True, kw_only=True)
class Harmless(Event):
    """'Harmless' answer to a pending/triggered alert: stop and re-arm the same mode."""

    who: str = ""


@dataclass(slots=True, kw_only=True)
class SoundNow(Event):
    who: str = ""


@dataclass(slots=True, kw_only=True)
class PauseRequest(Event):
    duration: timedelta
    reason: str = "manual"
    who: str = ""


@dataclass(slots=True, kw_only=True)
class KillswitchPressed(Event):
    press: Press


@dataclass(slots=True, kw_only=True)
class MasterSwitch(Event):
    on: bool
    who: str = ""


@dataclass(slots=True, kw_only=True)
class TestModeSwitch(Event):
    __test__ = False  # not a pytest test class
    on: bool
    who: str = ""


@dataclass(slots=True, kw_only=True)
class HealthChanged(Event):
    ok: bool
    failing: tuple[str, ...] = ()


@dataclass(slots=True, kw_only=True)
class NightEnd(Event):
    """The configured night end time was reached (sent by the HA layer's daily timer)."""


@dataclass(slots=True, kw_only=True)
class SummaryDue(Event):
    """Daily fallback: send a summary if none went out since the last one."""


# --------------------------------------------------------------------------- commands


@dataclass(slots=True)
class Command:
    pass


@dataclass(slots=True)
class Notify(Command):
    title: str
    message: str
    level: Level = Level.NORMAL
    actionable: bool = False  # adds Harmless / Disarm / Sound now
    source: str = ""  # detection entity, for the camera picture
    tag: str = ""


@dataclass(slots=True)
class Output(Command):
    """A physical alarm output. Never executed in test mode."""

    name: str  # sirens_on, sirens_off, cam_lights_on, cam_lights_off,
    # flash_on, flash_off, audio_on, audio_off, snapshot, restore
    seconds: int = 0


@dataclass(slots=True)
class Log(Command):
    """One journal entry. Everything the engine decides is logged."""

    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SendSummary(Command):
    reason: str


@dataclass(slots=True)
class StateChanged(Command):
    mode: Mode
    previous: Mode
