"""Glue between Home Assistant and the pure state machine in core/.

Entity changes become core events; core commands become service calls. Test mode is
checked again here before any physical output, so a bug in the engine can't sound a siren
while testing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    STATE_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_DISARM,
    ACTION_HARMLESS,
    ACTION_SOUND_NOW,
    CONF_ARMING_AWAY,
    CONF_ARMING_NIGHT,
    CONF_AUDIO_MEDIA,
    CONF_AUDIO_PLAYERS,
    CONF_BATTERIES,
    CONF_BUILDING_MODE,
    CONF_CAM_LIGHTS,
    CONF_CAR,
    CONF_CAR_HITS,
    CONF_CONTACTS,
    CONF_FLASH_LIGHTS,
    CONF_HEALTH_EXTRA,
    CONF_INSIDE_ATTR,
    CONF_INSIDE_VALUES,
    CONF_KILLSWITCH,
    CONF_LEAVE_DELAY,
    CONF_LOCK,
    CONF_MIN_BATTERY,
    CONF_NIGHT_END,
    CONF_NIGHT_STATES,
    CONF_NOTIFY,
    CONF_OFFLIMITS,
    CONF_PAUSE_ARRIVAL,
    CONF_PAUSE_BUTTON,
    CONF_PAUSE_UNLOCK,
    CONF_PENDING_AWAY,
    CONF_PENDING_NIGHT,
    CONF_PERSONS,
    CONF_PRESS_DOUBLE,
    CONF_PRESS_LONG,
    CONF_PRESS_SINGLE,
    CONF_SIREN_TIME,
    CONF_SIRENS,
    CONF_SUMMARY_TIME,
    CONF_TRIGGER_TIME,
    CONF_VISITOR,
    CONF_VISITOR_HITS,
    CONF_WAKE,
    CONF_WAKE_WINDOW,
    DEFAULTS,
    DOMAIN,
    EVENT_JOURNAL,
    HEALTH_GRACE_SECONDS,
    JOURNAL_MAX_AGE_DAYS,
    JOURNAL_MAX_ENTRIES,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
)
from .core import Engine, build_summary
from .core.models import (
    ArmRequest,
    BuildingModeChanged,
    Command,
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
from .core.models import (
    Event as CoreEvent,
)

_LOGGER = logging.getLogger(__package__)

SNAPSHOT_SCENE = "luna_alarm_snapshot"
_BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
STATE_LOCKED = "locked"  # LockState.LOCKED; a plain string keeps older HA versions working
_CAR_KINDS = ("vehicle", "person")


def _csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _time(value: Any, fallback: str) -> time:
    try:
        return time.fromisoformat(str(value or fallback))
    except ValueError:
        return time.fromisoformat(fallback)


class LunaAlarmRuntime:
    """One running alarm system (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.opts: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.engine = Engine(self._build_config(), test_mode=True)
        self.journal: list[dict[str, Any]] = []
        self.last_summary: dict[str, Any] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[CALLBACK_TYPE] = []
        self._deadline_unsub: CALLBACK_TYPE | None = None
        self._flash_task: asyncio.Task | None = None
        self._bad_since: datetime | None = None
        self._reported_ok = True
        self.failing: list[str] = []
        self._roles: dict[str, Role] = {}
        for key, role in (
            (CONF_WAKE, Role.WAKE),
            (CONF_VISITOR, Role.VISITOR),
            (CONF_OFFLIMITS, Role.OFFLIMITS),
            (CONF_CONTACTS, Role.CONTACT),
            (CONF_CAR, Role.CAR),
        ):
            for ent in self.opts.get(key) or []:
                self._roles[ent] = role

    # ------------------------------------------------------------------ config

    def _build_config(self) -> Config:
        o = self.opts

        def num(key: str) -> float:
            value = o.get(key)
            return float(DEFAULTS[key] if value is None or value == "" else value)

        def secs(key: str) -> timedelta:
            return timedelta(seconds=int(num(key)))

        return Config(
            arming_away=secs(CONF_ARMING_AWAY),
            arming_night=secs(CONF_ARMING_NIGHT),
            pending_away=secs(CONF_PENDING_AWAY),
            pending_night=secs(CONF_PENDING_NIGHT),
            trigger_time=secs(CONF_TRIGGER_TIME),
            siren_time=secs(CONF_SIREN_TIME),
            wake_window=secs(CONF_WAKE_WINDOW),
            visitor_hits=max(1, int(num(CONF_VISITOR_HITS))),
            leave_delay=secs(CONF_LEAVE_DELAY),
            pause_unlock=secs(CONF_PAUSE_UNLOCK),
            pause_arrival=secs(CONF_PAUSE_ARRIVAL),
            pause_button=secs(CONF_PAUSE_BUTTON),
            night_end=_time(o.get(CONF_NIGHT_END), str(DEFAULTS[CONF_NIGHT_END])),
            car_hits=max(1, int(num(CONF_CAR_HITS))),
        )

    # ------------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        if engine_state := stored.get("engine"):
            self.engine.restore(engine_state)
        else:
            self.engine.test_mode = True  # a fresh install always starts in test mode
        flags = stored.get("flags", {})
        self.engine.config.auto_away = flags.get("auto_away", True)
        self.engine.config.auto_night = flags.get("auto_night", True)
        self.journal = stored.get("journal", [])
        self.last_summary = stored.get("last_summary", {})
        self._prune_journal()

        # Seed facts from the current states without triggering anything.
        self.engine.anyone_home = self._anyone_home()
        self.engine.is_night = self._building_is_night()
        self.engine.locked = self._is_locked()

        watched = [*self._roles, *self.opts.get(CONF_PERSONS, [])]
        for key in (CONF_LOCK, CONF_BUILDING_MODE, CONF_KILLSWITCH):
            if self.opts.get(key):
                watched.append(self.opts[key])
        self._unsubs.append(async_track_state_change_event(self.hass, watched, self._on_state_change))
        self._unsubs.append(self.hass.bus.async_listen("mobile_app_notification_action", self._on_notification_action))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_interval, timedelta(seconds=30)))
        ne = self.engine.config.night_end
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_night_end, hour=ne.hour, minute=ne.minute, second=0)
        )
        st = _time(self.opts.get(CONF_SUMMARY_TIME), str(DEFAULTS[CONF_SUMMARY_TIME]))
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_summary_time, hour=st.hour, minute=st.minute, second=0)
        )
        self._journal("system", "Luna Alarm started", mode=self.engine.mode.value, test_mode=self.engine.test_mode)
        self.dispatch(Tick(at=dt_util.now()))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._deadline_unsub:
            self._deadline_unsub()
        if self._flash_task:
            self._flash_task.cancel()
        await self._save()

    async def _save(self) -> None:
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {
            "engine": self.engine.snapshot(),
            "flags": {"auto_away": self.engine.config.auto_away, "auto_night": self.engine.config.auto_night},
            "journal": self.journal,
            "last_summary": self.last_summary,
        }

    # ------------------------------------------------------------------ dispatch

    @callback
    def dispatch(self, event: CoreEvent) -> None:
        """Feed one event to the engine and execute what it returns."""
        try:
            commands = self.engine.handle(event)
        except Exception:  # never let a logic bug take HA down
            _LOGGER.exception("Luna Alarm engine failed on %s", type(event).__name__)
            return
        for cmd in commands:
            try:
                self._execute(cmd)
            except Exception:  # one bad command must not drop the rest
                _LOGGER.exception("Luna Alarm failed to execute %s", cmd)
        self._schedule_deadline()
        self._store.async_delay_save(self._data, 2)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    def _schedule_deadline(self) -> None:
        if self._deadline_unsub:
            self._deadline_unsub()
            self._deadline_unsub = None
        if (deadline := self.engine.next_deadline()) is not None:
            when = max(deadline, dt_util.now() + timedelta(milliseconds=50))
            self._deadline_unsub = async_track_point_in_time(self.hass, self._on_deadline, when)

    @callback
    def _on_deadline(self, now: datetime) -> None:
        self._deadline_unsub = None
        self.dispatch(Tick(at=dt_util.now()))

    # ------------------------------------------------------------------ commands

    @callback
    def _execute(self, cmd: Command) -> None:
        if isinstance(cmd, Log):
            self._journal(cmd.kind, cmd.message, **cmd.data)
        elif isinstance(cmd, Notify):
            self.hass.async_create_task(self._notify(cmd))
        elif isinstance(cmd, Output):
            if self.engine.test_mode:  # second guard
                self._journal("simulated", f"Blocked output in test mode: {cmd.name}", output=cmd.name)
                return
            self.hass.async_create_task(self._output(cmd))
        elif isinstance(cmd, SendSummary):
            self.hass.async_create_task(self._send_summary(cmd.reason))
        elif isinstance(cmd, StateChanged):
            self.hass.async_create_task(self._save())

    @callback
    def _journal(self, kind: str, message: str, /, **data: Any) -> None:
        entry = {"ts": dt_util.now().isoformat(), "kind": kind, "message": message, "data": data}
        self.journal.append(entry)
        if len(self.journal) > JOURNAL_MAX_ENTRIES:
            del self.journal[: len(self.journal) - JOURNAL_MAX_ENTRIES]
        self.hass.bus.async_fire(EVENT_JOURNAL, {"entry_id": self.entry.entry_id, **entry})
        _LOGGER.info("[%s] %s", kind, message)

    def _prune_journal(self) -> None:
        cutoff = dt_util.now() - timedelta(days=JOURNAL_MAX_AGE_DAYS)
        self.journal = [e for e in self.journal if datetime.fromisoformat(e["ts"]) >= cutoff]

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=Context())
        except Exception as err:  # noqa: BLE001 - one failing device must not stop the rest
            self._journal("error", f"{domain}.{service} failed: {err}", data=str(data))

    async def _notify(self, cmd: Notify) -> None:
        payload: dict[str, Any] = {"group": "luna-alarm"}
        if cmd.tag:
            payload["tag"] = cmd.tag
        if cmd.level is Level.CRITICAL and not self.engine.test_mode:
            payload["push"] = {
                "interruption-level": "critical",
                "sound": {"name": "default", "critical": 1, "volume": 1.0},
            }
        else:
            payload["push"] = {"interruption-level": "time-sensitive" if cmd.actionable else "active"}
        if cmd.actionable:
            payload["actions"] = [
                {"action": ACTION_HARMLESS, "title": "Harmless", "authenticationRequired": True},
                {"action": ACTION_DISARM, "title": "Disarm", "authenticationRequired": True, "destructive": True},
                {"action": ACTION_SOUND_NOW, "title": "Sound alarm now", "destructive": True},
            ]
        if cmd.source and (camera := self._camera_for(cmd.source)):
            payload["entity_id"] = camera
        for svc in _csv(self.opts.get(CONF_NOTIFY)):
            domain, _, name = svc.partition(".")
            if not name:
                domain, name = "notify", svc
            await self._call(domain, name, {"title": cmd.title, "message": cmd.message, "data": payload})

    async def _output(self, cmd: Output) -> None:
        o = self.opts
        sirens = o.get(CONF_SIRENS) or []
        cam_lights = o.get(CONF_CAM_LIGHTS) or []
        flash = o.get(CONF_FLASH_LIGHTS) or []
        players = o.get(CONF_AUDIO_PLAYERS) or []
        name = cmd.name
        if name == "sirens_on":
            for ent in sirens:
                await self._call("siren", "turn_on", {ATTR_ENTITY_ID: ent, "duration": cmd.seconds or 180})
        elif name == "sirens_off":
            for ent in sirens:
                await self._call("siren", "turn_off", {ATTR_ENTITY_ID: ent})
        elif name == "cam_lights_on" and cam_lights:
            await self._call("homeassistant", "turn_on", {ATTR_ENTITY_ID: cam_lights})
        elif name == "cam_lights_off" and cam_lights:
            await self._call("homeassistant", "turn_off", {ATTR_ENTITY_ID: cam_lights})
        elif name == "flash_on" and flash:
            if not self._flash_task or self._flash_task.done():
                self._flash_task = self.hass.async_create_background_task(self._flash(flash), f"{DOMAIN}_flash")
        elif name == "flash_off":
            if self._flash_task:
                self._flash_task.cancel()
                self._flash_task = None
        elif name == "audio_on" and players and o.get(CONF_AUDIO_MEDIA):
            await self._call(
                "media_player",
                "play_media",
                {ATTR_ENTITY_ID: players, "media_content_id": o[CONF_AUDIO_MEDIA], "media_content_type": "music"},
            )
        elif name == "audio_off" and players:
            await self._call("media_player", "media_stop", {ATTR_ENTITY_ID: players})
        elif name == "snapshot" and (cam_lights or flash):
            await self._call("scene", "create", {"scene_id": SNAPSHOT_SCENE, "snapshot_entities": [*cam_lights, *flash]})
        elif name == "restore" and (cam_lights or flash):
            await self._call("scene", "turn_on", {ATTR_ENTITY_ID: f"scene.{SNAPSHOT_SCENE}"})

    async def _flash(self, lights: list[str]) -> None:
        """Red flashing inside while pending or triggered."""
        bright = True
        while self.engine.mode in (Mode.PENDING, Mode.TRIGGERED) and not self.engine.test_mode:
            await self._call(
                "light",
                "turn_on",
                {ATTR_ENTITY_ID: lights, "rgb_color": [255, 0, 0], "brightness": 255 if bright else 25, "transition": 0},
            )
            bright = not bright
            await asyncio.sleep(1)

    async def _send_summary(self, reason: str) -> None:
        since = self.last_summary.get("until")
        entries = [e for e in self.journal if not since or e["ts"] > since]
        title, message, details = build_summary(entries, test_mode=self.engine.test_mode, reason=reason)
        now = dt_util.now().isoformat()
        self.last_summary = {"until": now, "title": title, "message": message, **details}
        self._journal("summary", f"Summary sent ({reason})", reason=reason)
        await self._notify(Notify(title=title, message=message, level=Level.NORMAL, tag="luna-alarm-summary"))
        await self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    # ------------------------------------------------------------------ inputs

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        ent = event.data["entity_id"]
        old: State | None = event.data["old_state"]
        new: State | None = event.data["new_state"]
        if new is None:
            return
        now = dt_util.now()
        if ent in self._roles:
            self._detection(ent, old, new, now)
        elif ent in self.opts.get(CONF_PERSONS, []):
            self._presence(ent, old, new, now)
        elif ent == self.opts.get(CONF_LOCK):
            self._lock(old, new, now)
        elif ent == self.opts.get(CONF_BUILDING_MODE):
            self.dispatch(BuildingModeChanged(at=now, is_night=self._building_is_night()))
        elif ent == self.opts.get(CONF_KILLSWITCH):
            self._killswitch(old, new, now)

    def _detection(self, ent: str, old: State | None, new: State, now: datetime) -> None:
        role = self._roles[ent]
        if ent.startswith("event."):
            if old is None or old.state == STATE_UNAVAILABLE or new.state in _BAD_STATES:
                return
            kind = str(new.attributes.get("event_type", ""))
            if role is Role.CAR:
                if kind not in _CAR_KINDS:
                    return
            elif kind != "person":
                return
        else:
            if new.state != STATE_ON or old is None or old.state in (STATE_ON, STATE_UNAVAILABLE):
                return
            kind = "open" if role is Role.CONTACT else "person"
        self.dispatch(
            Detection(at=now, source=ent, role=role, kind=kind, label=self._label(ent), dark=self._is_dark())
        )

    def _simulate(self, ent: str, kind: str = "person") -> None:
        """Service luna_alarm.simulate_detection: feed a fake detection for testing."""
        role = self._roles.get(ent, Role.OFFLIMITS)
        self._journal("simulation", f"Simulated detection on {ent}", source=ent)
        self.dispatch(
            Detection(at=dt_util.now(), source=ent, role=role, kind=kind, label=self._label(ent), dark=self._is_dark())
        )

    def _presence(self, ent: str, old: State | None, new: State, now: datetime) -> None:
        if old is None or new.state in _BAD_STATES or old.state in _BAD_STATES or old.state == new.state:
            return
        name = new.attributes.get(ATTR_FRIENDLY_NAME, ent)
        arrived = (name,) if new.state == STATE_HOME else ()
        left = (name,) if old.state == STATE_HOME and new.state != STATE_HOME else ()
        if not arrived and not left:
            return
        self.dispatch(PresenceChanged(at=now, anyone_home=self._anyone_home(), arrived=arrived, left=left))

    def _lock(self, old: State | None, new: State, now: datetime) -> None:
        if old is None or new.state in _BAD_STATES or old.state == new.state:
            return
        locked = new.state == STATE_LOCKED
        inside = True
        attr = str(self.opts.get(CONF_INSIDE_ATTR) or "").strip()
        values = [v.lower() for v in _csv(self.opts.get(CONF_INSIDE_VALUES))]
        if not locked and attr:
            inside = str(new.attributes.get(attr, "")).lower() in values
        if locked or new.state == "unlocked" or new.state == "open":
            self.dispatch(LockChanged(at=now, locked=locked, inside=inside))

    def _killswitch(self, old: State | None, new: State, now: datetime) -> None:
        if old is None or old.state == STATE_UNAVAILABLE or new.state in _BAD_STATES:
            return
        etype = str(new.attributes.get("event_type", "")).lower()
        for key, press in ((CONF_PRESS_SINGLE, Press.SINGLE), (CONF_PRESS_DOUBLE, Press.DOUBLE), (CONF_PRESS_LONG, Press.LONG)):
            if etype in [v.lower() for v in _csv(self.opts.get(key))]:
                self.dispatch(KillswitchPressed(at=now, press=press))
                return
        self._journal("killswitch", f"Unmapped killswitch event: {etype}", event_type=etype)

    @callback
    def _on_notification_action(self, event: Event) -> None:
        action = event.data.get("action")
        if action not in (ACTION_HARMLESS, ACTION_DISARM, ACTION_SOUND_NOW):
            return
        who = self.person_name(event.context.user_id)
        now = dt_util.now()
        if action == ACTION_HARMLESS:
            self.dispatch(Harmless(at=now, who=who))
        elif action == ACTION_DISARM:
            self.dispatch(DisarmRequest(at=now, who=who, reason="from notification"))
        else:
            self.dispatch(SoundNow(at=now, who=who))

    @callback
    def _on_interval(self, now: datetime) -> None:
        self._check_health()
        self.dispatch(Tick(at=dt_util.now()))

    @callback
    def _on_night_end(self, now: datetime) -> None:
        self.dispatch(NightEnd(at=dt_util.now()))

    @callback
    def _on_summary_time(self, now: datetime) -> None:
        self.dispatch(SummaryDue(at=dt_util.now()))

    # ------------------------------------------------------------------ public actions (entities, services)

    def arm(self, mode: Mode, who: str) -> None:
        self.dispatch(ArmRequest(at=dt_util.now(), mode=mode, who=who))

    def disarm(self, who: str) -> None:
        self.dispatch(DisarmRequest(at=dt_util.now(), who=who, reason="manual"))

    def pause(self, minutes: float, reason: str, who: str = "") -> None:
        self.dispatch(PauseRequest(at=dt_util.now(), duration=timedelta(minutes=minutes), reason=reason, who=who))

    def set_master(self, on: bool, who: str = "") -> None:
        self.dispatch(MasterSwitch(at=dt_util.now(), on=on, who=who))

    def set_test_mode(self, on: bool, who: str = "") -> None:
        if not on and self.engine.mode in (Mode.PENDING, Mode.TRIGGERED):
            self._journal("test_mode", "Test mode stays on during an active (simulated) alarm")
            return
        self.dispatch(TestModeSwitch(at=dt_util.now(), on=on, who=who))

    def set_flag(self, name: str, on: bool) -> None:
        setattr(self.engine.config, name, on)
        self._journal("setting", f"{name} {'on' if on else 'off'}")
        self.dispatch(Tick(at=dt_util.now()))

    async def send_summary_now(self) -> None:
        await self._send_summary("requested")

    # ------------------------------------------------------------------ health

    def monitored_entities(self) -> list[str]:
        o = self.opts
        ents = [*self._roles, *(o.get(CONF_SIRENS) or []), *(o.get(CONF_HEALTH_EXTRA) or [])]
        if o.get(CONF_LOCK):
            ents.append(o[CONF_LOCK])
        for src in self._roles:
            if cam := self._camera_for(src):
                ents.append(cam)
        return sorted(set(ents))

    @callback
    def _check_health(self) -> None:
        bad: list[str] = []
        for ent in self.monitored_entities():
            state = self.hass.states.get(ent)
            if state is None or state.state == STATE_UNAVAILABLE:
                bad.append(self._label(ent))
        min_batt = float(self.opts.get(CONF_MIN_BATTERY) or 0)
        for ent in self.opts.get(CONF_BATTERIES) or []:
            state = self.hass.states.get(ent)
            try:
                if state is None or float(state.state) < min_batt:
                    bad.append(f"{self._label(ent)} battery")
            except ValueError:
                bad.append(f"{self._label(ent)} battery")
        self.failing = bad
        now = dt_util.now()
        if not bad:
            self._bad_since = None
            if not self._reported_ok or not self.engine.health_ok:
                self._reported_ok = True
                self.dispatch(HealthChanged(at=now, ok=True))
            return
        if self._bad_since is None:
            self._bad_since = now
        if now - self._bad_since >= timedelta(seconds=HEALTH_GRACE_SECONDS) and (
            self._reported_ok or tuple(bad) != self.engine.failing
        ):
            self._reported_ok = False
            self.dispatch(HealthChanged(at=now, ok=False, failing=tuple(bad)))

    # ------------------------------------------------------------------ helpers

    def _anyone_home(self) -> bool:
        persons = self.opts.get(CONF_PERSONS) or []
        if not persons:
            return True
        return any((s := self.hass.states.get(p)) is not None and s.state == STATE_HOME for p in persons)

    def _building_is_night(self) -> bool:
        ent = self.opts.get(CONF_BUILDING_MODE)
        if not ent or (state := self.hass.states.get(ent)) is None:
            return False
        nights = [v.lower() for v in _csv(self.opts.get(CONF_NIGHT_STATES))]
        return state.state.lower() in nights or state.state == STATE_ON

    def _is_locked(self) -> bool:
        ent = self.opts.get(CONF_LOCK)
        return bool(ent) and (s := self.hass.states.get(ent)) is not None and s.state == STATE_LOCKED

    def _is_dark(self) -> bool:
        sun = self.hass.states.get("sun.sun")
        return sun is not None and sun.state == "below_horizon"

    def _label(self, ent: str) -> str:
        reg = er.async_get(self.hass)
        if (entry := reg.async_get(ent)) and entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            if device:
                return device.name_by_user or device.name or ent
        state = self.hass.states.get(ent)
        return state.attributes.get(ATTR_FRIENDLY_NAME, ent) if state else ent

    def _camera_for(self, ent: str) -> str | None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(ent)
        if not entry or not entry.device_id:
            return None
        for other in er.async_entries_for_device(reg, entry.device_id):
            if other.domain == "camera" and not other.disabled_by:
                return other.entity_id
        return None

    def person_name(self, user_id: str | None) -> str:
        if not user_id:
            return ""
        for state in self.hass.states.async_all("person"):
            if state.attributes.get("user_id") == user_id:
                return state.attributes.get(ATTR_FRIENDLY_NAME, state.entity_id)
        return ""
