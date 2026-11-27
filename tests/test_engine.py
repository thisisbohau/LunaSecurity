"""Scenario tests for the Luna Alarm state machine. Run: pytest tests/"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "luna_alarm"))

from core import Engine, build_summary  # noqa: E402
from core.models import (  # noqa: E402
    ArmRequest,
    BuildingModeChanged,
    Config,
    Detection,
    DisarmRequest,
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
    PresenceChanged,
    Press,
    Role,
    SendSummary,
    SoundNow,
    SummaryDue,
    TestModeSwitch,
    Tick,
)

TZ = timezone(timedelta(hours=1))
T0 = datetime(2026, 11, 28, 14, 0, tzinfo=TZ)


class Sim:
    """Drives an engine and collects everything it emits."""

    def __init__(self, test_mode: bool = False, **cfg) -> None:
        self.engine = Engine(Config(**cfg), test_mode=test_mode)
        self.now = T0
        self.out: list = []

    def send(self, cls, **kw):
        cmds = self.engine.handle(cls(at=self.now, **kw))
        self.out.extend(cmds)
        return cmds

    def wait(self, **delta):
        """Advance time, ticking at every deadline on the way (like the HA timer would)."""
        end = self.now + timedelta(**delta)
        while (d := self.engine.next_deadline()) is not None and d <= end:
            self.now = max(d, self.now)
            self.out.extend(self.engine.handle(Tick(at=self.now)))
        self.now = end
        self.out.extend(self.engine.handle(Tick(at=self.now)))

    @property
    def mode(self) -> Mode:
        return self.engine.mode

    def outputs(self) -> list[str]:
        return [c.name for c in self.out if isinstance(c, Output)]

    def notes(self, level: Level | None = None) -> list[Notify]:
        return [c for c in self.out if isinstance(c, Notify) and (level is None or c.level is level)]

    def logs(self, kind: str) -> list[Log]:
        return [c for c in self.out if isinstance(c, Log) and c.kind == kind]

    def det(self, source: str, role: Role, **kw):
        return self.send(Detection, source=source, role=role, label=source, **kw)

    def arm_away(self):
        self.send(ArmRequest, mode=Mode.ARMED_AWAY)
        self.wait(seconds=61)
        assert self.mode is Mode.ARMED_AWAY

    def arm_night(self):
        self.send(ArmRequest, mode=Mode.ARMED_NIGHT)
        self.wait(minutes=5, seconds=1)
        assert self.mode is Mode.ARMED_NIGHT


# ----------------------------------------------------------------------- arming


def test_auto_arm_away_after_everyone_left():
    s = Sim()
    s.send(PresenceChanged, anyone_home=False, left=("Alex",))
    s.wait(minutes=1)
    assert s.mode is Mode.DISARMED
    s.wait(minutes=1, seconds=1)
    assert s.mode is Mode.ARMING
    s.wait(seconds=60)
    assert s.mode is Mode.ARMED_AWAY
    assert any("Away protection armed" in n.message for n in s.notes())


def test_return_within_leave_delay_does_not_arm():
    s = Sim()
    s.send(PresenceChanged, anyone_home=False)
    s.wait(seconds=90)
    s.send(PresenceChanged, anyone_home=True, arrived=("Alex",))
    s.wait(minutes=5)
    assert s.mode is Mode.DISARMED


def test_failed_self_check_blocks_arming():
    s = Sim()
    s.send(HealthChanged, ok=False, failing=("camera.entry_cam",))
    s.send(ArmRequest, mode=Mode.ARMED_AWAY)
    assert s.mode is Mode.DISARMED
    assert any("Self-check failed" in n.message for n in s.notes())


def test_health_failure_during_arming_aborts():
    s = Sim()
    s.send(ArmRequest, mode=Mode.ARMED_NIGHT)
    s.send(HealthChanged, ok=False, failing=("siren.homebase_siren",))
    assert s.mode is Mode.DISARMED


def test_health_failure_while_armed_stays_armed_and_alerts_once():
    s = Sim()
    s.arm_away()
    s.send(HealthChanged, ok=False, failing=("camera.garden_cam",))
    s.send(HealthChanged, ok=False, failing=("camera.garden_cam", "camera.entry_cam"))
    assert s.mode is Mode.ARMED_AWAY
    assert len([n for n in s.notes(Level.CRITICAL) if "tampering" in n.message]) == 1


def test_disabled_system_never_arms():
    s = Sim()
    s.send(MasterSwitch, on=False)
    s.send(PresenceChanged, anyone_home=False)
    s.wait(minutes=10)
    assert s.mode is Mode.DISABLED


# ----------------------------------------------------------------------- away detection


def test_mailman_gives_visitor_info_not_alarm():
    s = Sim()
    s.arm_away()
    s.det("doorbell", Role.WAKE)
    s.wait(seconds=20)
    s.det("entry", Role.VISITOR)
    s.wait(minutes=5)
    assert s.mode is Mode.ARMED_AWAY
    assert [n.title for n in s.notes()][-1] == "Visitor"


def test_entry_without_wake_is_pending():
    s = Sim()
    s.arm_away()
    s.det("entry", Role.VISITOR)
    assert s.mode is Mode.PENDING


def test_repeated_entry_visits_escalate():
    s = Sim()
    s.arm_away()
    s.det("doorbell", Role.WAKE)
    for _ in range(3):
        s.wait(seconds=40)
        s.det("entry", Role.VISITOR)
    assert s.mode is Mode.PENDING


def test_fence_jump_pending_then_triggered_with_outputs():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    assert s.mode is Mode.PENDING
    assert s.notes(Level.CRITICAL)[-1].actionable
    s.wait(seconds=119)
    assert s.mode is Mode.PENDING
    s.wait(seconds=2)
    assert s.mode is Mode.TRIGGERED
    assert {"sirens_on", "cam_lights_on", "flash_on", "audio_on"} <= set(s.outputs())
    s.wait(minutes=3, seconds=1)
    assert "sirens_off" in s.outputs()
    s.wait(minutes=8)
    assert s.mode is Mode.ARMED_AWAY  # times out back to armed, never disarmed
    assert "restore" in s.outputs()


def test_window_contact_pending_in_away_only():
    s = Sim()
    s.arm_away()
    s.det("window", Role.CONTACT, kind="open")
    assert s.mode is Mode.PENDING


def test_car_watch_notifies_but_never_pending():
    s = Sim()
    s.arm_away()
    for _ in range(3):
        s.det("garage_vehicle", Role.CAR, kind="vehicle", dark=True)
        s.wait(seconds=60)
    assert s.mode is Mode.ARMED_AWAY
    assert len(s.notes(Level.CRITICAL)) == 1
    assert any(n.title == "Car watch" for n in s.notes(Level.NORMAL))


def test_arrival_disarms_away_even_during_alarm():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    s.send(PresenceChanged, anyone_home=True, arrived=("Sam",))
    assert s.mode is Mode.DISARMED
    assert "sirens_off" in s.outputs()


# ----------------------------------------------------------------------- responses


def test_harmless_rearms_same_mode():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    s.send(Harmless, who="Alex")
    assert s.mode is Mode.ARMING
    s.wait(seconds=61)
    assert s.mode is Mode.ARMED_AWAY


def test_sound_now_triggers_immediately():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    s.send(SoundNow, who="Alex")
    assert s.mode is Mode.TRIGGERED


# ----------------------------------------------------------------------- night


def _night_sim(**cfg) -> Sim:
    s = Sim(**cfg)
    s.now = datetime(2026, 11, 28, 22, 30, tzinfo=TZ)
    return s


def test_auto_night_needs_night_mode_and_lock():
    s = _night_sim()
    s.send(BuildingModeChanged, is_night=True)
    assert s.mode is Mode.DISARMED
    s.send(LockChanged, locked=True)
    assert s.mode is Mode.ARMING
    assert "arms in 5 min" in s.notes()[-1].message
    s.wait(minutes=5, seconds=1)
    assert s.mode is Mode.ARMED_NIGHT


def test_night_detection_pending_turns_on_lights_and_flash():
    s = _night_sim()
    s.arm_night()
    s.det("entry", Role.VISITOR)
    assert s.mode is Mode.PENDING
    assert {"cam_lights_on", "flash_on"} <= set(s.outputs())
    s.wait(seconds=61)
    assert s.mode is Mode.TRIGGERED


def test_night_ignores_wake_cameras():
    s = _night_sim()
    s.arm_night()
    s.det("doorbell", Role.WAKE)
    s.det("garage_vehicle", Role.CAR, kind="vehicle", dark=True)
    assert s.mode is Mode.ARMED_NIGHT
    assert not s.notes(Level.CRITICAL)


def test_inside_unlock_pauses_five_minutes():
    s = _night_sim()
    s.arm_night()
    s.send(LockChanged, locked=False, inside=True)
    s.det("entry", Role.VISITOR)
    assert s.mode is Mode.ARMED_NIGHT
    assert s.logs("detection")[-1].data["action"] == "paused"
    s.wait(minutes=5, seconds=1)
    assert any("resumed" in n.message for n in s.notes())
    s.det("entry", Role.VISITOR)
    assert s.mode is Mode.PENDING


def test_arrival_at_night_pauses_ten_minutes_and_pause_only_extends():
    s = _night_sim()
    s.arm_night()
    s.send(PresenceChanged, anyone_home=True, arrived=("Robin",))
    until = s.engine.pause_until
    s.send(LockChanged, locked=False, inside=True)  # shorter pause must not shorten
    assert s.engine.pause_until == until


def test_night_end_time_disarms_and_sends_summary_and_latches():
    s = _night_sim()
    s.send(BuildingModeChanged, is_night=True)
    s.send(LockChanged, locked=True)
    s.wait(minutes=5, seconds=1)
    s.now = datetime(2026, 11, 29, 6, 30, tzinfo=TZ)
    s.send(NightEnd)
    assert s.mode is Mode.DISARMED
    assert any(isinstance(c, SendSummary) for c in s.out)
    s.send(LockChanged, locked=False, inside=True)
    s.send(LockChanged, locked=True)
    assert s.mode is Mode.DISARMED  # latch: no re-arm the same morning


def test_inside_unlock_after_night_end_ends_night():
    s = _night_sim()
    s.arm_night()
    s.now = datetime(2026, 11, 29, 6, 45, tzinfo=TZ)
    s.send(LockChanged, locked=False, inside=True)
    assert s.mode is Mode.DISARMED
    assert any(isinstance(c, SendSummary) for c in s.out)


def test_building_mode_day_ends_night():
    s = _night_sim()
    s.send(BuildingModeChanged, is_night=True)
    s.send(LockChanged, locked=True)
    s.wait(minutes=6)
    s.send(BuildingModeChanged, is_night=False)
    assert s.mode is Mode.DISARMED


# ----------------------------------------------------------------------- killswitch


def test_killswitch_nobody_home_is_suspicious():
    s = Sim()
    s.arm_away()
    s.send(PresenceChanged, anyone_home=False)
    s.send(KillswitchPressed, press=Press.DOUBLE)
    assert s.mode is Mode.PENDING


def test_killswitch_presses_when_home():
    s = _night_sim()
    s.arm_night()
    s.send(KillswitchPressed, press=Press.SINGLE)
    assert s.engine.pause_until is not None
    s.send(KillswitchPressed, press=Press.DOUBLE)
    assert s.mode is Mode.DISARMED
    s.send(KillswitchPressed, press=Press.LONG)
    assert s.mode is Mode.DISABLED


# ----------------------------------------------------------------------- test mode


def test_test_mode_never_emits_outputs_or_critical():
    s = Sim(test_mode=True)
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    s.wait(minutes=15)
    assert s.outputs() == []
    assert s.notes(Level.CRITICAL) == []
    assert all(n.title.startswith("[TEST]") for n in s.notes())
    assert {lg.data["output"] for lg in s.logs("simulated")} >= {"sirens_on", "flash_on", "audio_on"}
    assert any("simulated" in n.message for n in s.notes())


def test_switching_test_mode_on_mid_alarm_stops_future_outputs():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    s.send(TestModeSwitch, on=True)
    before = len(s.outputs())
    s.wait(minutes=15)
    assert len(s.outputs()) == before


# ----------------------------------------------------------------------- summary & persistence


def test_summary_from_journal():
    s = Sim(test_mode=True)
    s.arm_away()
    s.det("doorbell", Role.WAKE)
    s.det("garden", Role.OFFLIMITS)
    s.send(Harmless, who="Alex")
    s.wait(minutes=2)
    s.send(DisarmRequest, who="Alex")
    journal = [{"ts": T0.isoformat(), "kind": c.kind, "message": c.message, "data": c.data} for c in s.out if isinstance(c, Log)]
    title, msg, details = build_summary(journal, test_mode=True, reason="test")
    assert title.startswith("[TEST]")
    assert "Alerts: 1 (harmless: 1)" in msg
    assert details["detections"]["garden"]["pending"] == 1


def test_daily_summary_only_when_none_recent():
    s = Sim()
    s.send(SummaryDue)
    s.now += timedelta(hours=5)
    s.send(SummaryDue)
    assert len([c for c in s.out if isinstance(c, SendSummary)]) == 1


def test_snapshot_roundtrip_keeps_pending_deadline():
    s = Sim()
    s.arm_away()
    s.det("garden", Role.OFFLIMITS)
    data = s.engine.snapshot()
    e2 = Engine(Config())
    e2.restore(data)
    assert e2.mode is Mode.PENDING
    cmds = e2.handle(Tick(at=s.now + timedelta(minutes=3)))
    assert e2.mode is Mode.TRIGGERED
    assert any(isinstance(c, Output) and c.name == "sirens_on" for c in cmds)


@pytest.mark.parametrize("role", [Role.VISITOR, Role.OFFLIMITS, Role.CONTACT])
def test_no_alarm_when_disarmed(role):
    s = Sim()
    s.det("x", role)
    assert s.mode is Mode.DISARMED
    assert s.logs("detection")[-1].data["action"] == "not_armed"
