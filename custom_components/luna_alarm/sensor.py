"""Journal and summary sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LunaAlarmConfigEntry
from .entity import LunaAlarmEntity

JOURNAL_ATTR_ENTRIES = 25


async def async_setup_entry(
    hass: HomeAssistant, entry: LunaAlarmConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities([LastEvent(rt), LastSummary(rt)])


class LastEvent(LunaAlarmEntity, SensorEntity):
    """Newest journal line; recent entries as attribute (full history via the luna_alarm_event bus event)."""

    _attr_icon = "mdi:text-box-outline"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "last_event")

    @property
    def native_value(self) -> str | None:
        j = self.runtime.journal
        return j[-1]["message"][:255] if j else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        recent = self.runtime.journal[-JOURNAL_ATTR_ENTRIES:]
        return {"recent": [f"{e['ts'][11:19]} [{e['kind']}] {e['message']}" for e in reversed(recent)]}


class LastSummary(LunaAlarmEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clipboard-text-clock-outline"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "last_summary")

    @property
    def native_value(self) -> datetime | None:
        until = self.runtime.last_summary.get("until")
        return datetime.fromisoformat(until) if until else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {k: v for k, v in self.runtime.last_summary.items() if k != "until"}
