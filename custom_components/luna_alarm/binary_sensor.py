"""Self-check problem sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LunaAlarmConfigEntry
from .entity import LunaAlarmEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LunaAlarmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SelfCheck(entry.runtime_data)])


class SelfCheck(LunaAlarmEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "self_check")

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"failing": self.runtime.failing, "monitored": self.runtime.monitored_entities()}
