"""Buttons: pause 5 minutes, send the summary now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LunaAlarmConfigEntry
from .entity import LunaAlarmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LunaAlarmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities([PauseButton(rt), SummaryButton(rt)])


class PauseButton(LunaAlarmEntity, ButtonEntity):
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "pause")

    async def async_press(self) -> None:
        who = self.runtime.person_name(self._context.user_id if self._context else None)
        self.runtime.pause(5, "pause button", who)


class SummaryButton(LunaAlarmEntity, ButtonEntity):
    _attr_icon = "mdi:clipboard-text-outline"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "send_summary")

    async def async_press(self) -> None:
        await self.runtime.send_summary_now()
