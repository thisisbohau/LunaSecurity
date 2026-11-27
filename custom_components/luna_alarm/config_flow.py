"""Config and options flow. Setup picks the entities; options add the timings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)

from .const import (
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
)


def _ents(domains: list[str], multiple: bool = True) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=domains, multiple=multiple))


def _secs(maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=0, max=maximum, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
    )


def _count() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=1, max=20, step=1, mode=NumberSelectorMode.BOX))


_DETECTION = {
    CONF_WAKE: _ents(["binary_sensor", "event"]),
    CONF_VISITOR: _ents(["binary_sensor", "event"]),
    CONF_OFFLIMITS: _ents(["binary_sensor", "event"]),
    CONF_CONTACTS: _ents(["binary_sensor"]),
    CONF_CAR: _ents(["event"]),
}
_HOUSE = {
    CONF_PERSONS: _ents(["person"]),
    CONF_LOCK: _ents(["lock"], multiple=False),
    CONF_INSIDE_ATTR: TextSelector(),
    CONF_INSIDE_VALUES: TextSelector(),
    CONF_BUILDING_MODE: _ents(["input_select", "select", "input_boolean", "sensor"], multiple=False),
    CONF_NIGHT_STATES: TextSelector(),
    CONF_KILLSWITCH: _ents(["event"], multiple=False),
    CONF_PRESS_SINGLE: TextSelector(),
    CONF_PRESS_DOUBLE: TextSelector(),
    CONF_PRESS_LONG: TextSelector(),
}
_OUTPUTS = {
    CONF_SIRENS: _ents(["siren"]),
    CONF_CAM_LIGHTS: _ents(["switch", "light"]),
    CONF_FLASH_LIGHTS: _ents(["light"]),
    CONF_AUDIO_PLAYERS: _ents(["media_player"]),
    CONF_AUDIO_MEDIA: TextSelector(),
}
_NOTIFY = {
    CONF_NOTIFY: TextSelector(TextSelectorConfig(multiple=True)),
    CONF_HEALTH_EXTRA: _ents([]),
    CONF_BATTERIES: _ents(["sensor"]),
    CONF_MIN_BATTERY: NumberSelector(NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX)),
}
_TIMINGS = {
    CONF_ARMING_AWAY: _secs(600),
    CONF_ARMING_NIGHT: _secs(1800),
    CONF_PENDING_AWAY: _secs(600),
    CONF_PENDING_NIGHT: _secs(600),
    CONF_SIREN_TIME: _secs(900),
    CONF_TRIGGER_TIME: _secs(3600),
    CONF_WAKE_WINDOW: _secs(1800),
    CONF_VISITOR_HITS: _count(),
    CONF_CAR_HITS: _count(),
    CONF_LEAVE_DELAY: _secs(1800),
    CONF_PAUSE_UNLOCK: _secs(3600),
    CONF_PAUSE_ARRIVAL: _secs(3600),
    CONF_PAUSE_BUTTON: _secs(3600),
    CONF_NIGHT_END: TimeSelector(),
    CONF_SUMMARY_TIME: TimeSelector(),
}
_SECTIONS = {"detection": _DETECTION, "house": _HOUSE, "outputs": _OUTPUTS, "notifications": _NOTIFY}


def _schema(current: dict[str, Any], with_timings: bool) -> vol.Schema:
    sections = dict(_SECTIONS)
    if with_timings:
        sections["timings"] = _TIMINGS
    fields: dict[Any, Any] = {}
    for name, selectors in sections.items():
        inner: dict[Any, Any] = {}
        for key, selector in selectors.items():
            value = current.get(key, DEFAULTS.get(key))
            inner[vol.Optional(key, description={"suggested_value": value} if value not in (None, "", []) else None)] = selector
        fields[vol.Required(name)] = section(vol.Schema(inner), {"collapsed": name in ("timings", "outputs")})
    return vol.Schema(fields)


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Sections come back nested; store flat. A cleared field is stored as None so it
    overrides the value from setup instead of silently falling back to it."""
    flat: dict[str, Any] = {}
    for name, selectors in {**_SECTIONS, "timings": _TIMINGS}.items():
        if name not in user_input:
            continue
        values = user_input[name] or {}
        for key in selectors:
            flat[key] = values.get(key)
    return flat


class LunaAlarmConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Luna Alarm", data=_flatten(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}, with_timings=False))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LunaAlarmOptionsFlow()


class LunaAlarmOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_flatten(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current, with_timings=True))
