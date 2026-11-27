"""The alarm panel entity: native HA alarm card, app support, arm/disarm."""

from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LunaAlarmConfigEntry
from .core.models import Mode
from .entity import LunaAlarmEntity

_STATE = {
    Mode.DISABLED: AlarmControlPanelState.DISARMED,
    Mode.DISARMED: AlarmControlPanelState.DISARMED,
    Mode.ARMING: AlarmControlPanelState.ARMING,
    Mode.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    Mode.ARMED_NIGHT: AlarmControlPanelState.ARMED_NIGHT,
    Mode.PENDING: AlarmControlPanelState.PENDING,
    Mode.TRIGGERED: AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: LunaAlarmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LunaAlarmPanel(entry.runtime_data)])


class LunaAlarmPanel(LunaAlarmEntity, AlarmControlPanelEntity):
    _attr_name = None  # the device name: "Luna Alarm"
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY | AlarmControlPanelEntityFeature.ARM_NIGHT

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "panel")

    @property
    def alarm_state(self) -> AlarmControlPanelState:
        return _STATE[self.runtime.engine.mode]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = self.runtime.engine
        return {
            "mode": e.mode.value,
            "test_mode": e.test_mode,
            "armed_mode": e.armed_mode.value if e.armed_mode else None,
            "paused_until": e.pause_until.isoformat() if e.paused and e.pause_until else None,
            "wake_until": e.wake_until.isoformat() if e.wake_until else None,
            "alarm_reason": e.alarm_reason or None,
            "self_check_failing": list(self.runtime.failing),
        }

    def _who(self) -> str:
        ctx = getattr(self, "_context", None)  # set by HA for the service call being handled
        return self.runtime.person_name(ctx.user_id if ctx else None)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self.runtime.disarm(self._who() or "app")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self.runtime.arm(Mode.ARMED_AWAY, self._who() or "app")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        self.runtime.arm(Mode.ARMED_NIGHT, self._who() or "app")
