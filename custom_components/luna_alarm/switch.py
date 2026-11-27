"""Switches: master, test mode, auto-arm away, auto-arm night."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LunaAlarmConfigEntry
from .core.models import Mode
from .entity import LunaAlarmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LunaAlarmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities([MasterSwitch(rt), TestModeSwitch(rt), FlagSwitch(rt, "auto_away"), FlagSwitch(rt, "auto_night")])


class MasterSwitch(LunaAlarmEntity, SwitchEntity):
    """Off = disabled: disarms and blocks all arming. The app-side killswitch."""

    _attr_icon = "mdi:shield-home"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "master")

    @property
    def is_on(self) -> bool:
        return self.runtime.engine.mode is not Mode.DISABLED

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.runtime.set_master(True, self.runtime.person_name(self._context.user_id if self._context else None))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.runtime.set_master(False, self.runtime.person_name(self._context.user_id if self._context else None))


class TestModeSwitch(LunaAlarmEntity, SwitchEntity):
    """On = everything is simulated and logged; no sirens, lights, audio or critical alerts."""

    _attr_icon = "mdi:test-tube"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "test_mode")

    @property
    def is_on(self) -> bool:
        return self.runtime.engine.test_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.runtime.set_test_mode(True, self.runtime.person_name(self._context.user_id if self._context else None))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.runtime.set_test_mode(False, self.runtime.person_name(self._context.user_id if self._context else None))


class FlagSwitch(LunaAlarmEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, flag: str) -> None:
        super().__init__(runtime, flag)
        self._flag = flag
        self._attr_icon = "mdi:home-export-outline" if flag == "auto_away" else "mdi:weather-night"

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.runtime.engine.config, self._flag))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.runtime.set_flag(self._flag, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.runtime.set_flag(self._flag, False)
