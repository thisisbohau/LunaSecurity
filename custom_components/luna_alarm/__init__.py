"""Luna Alarm: away protection and night watch on top of eufy cameras and HA presence."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .runtime import LunaAlarmRuntime

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

type LunaAlarmConfigEntry = ConfigEntry[LunaAlarmRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: LunaAlarmConfigEntry) -> bool:
    runtime = LunaAlarmRuntime(hass, entry)
    entry.runtime_data = runtime
    await runtime.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LunaAlarmConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _async_reload(hass: HomeAssistant, entry: LunaAlarmConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _runtimes(hass: HomeAssistant) -> list[LunaAlarmRuntime]:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Luna Alarm is not set up")
    return [e.runtime_data for e in entries]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "pause"):
        return

    async def pause(call: ServiceCall) -> None:
        who = ""
        for rt in _runtimes(hass):
            who = who or rt.person_name(call.context.user_id)
            rt.pause(call.data["minutes"], call.data.get("reason", "manual"), who)

    async def send_summary(call: ServiceCall) -> None:
        for rt in _runtimes(hass):
            await rt.send_summary_now()

    async def simulate(call: ServiceCall) -> None:
        for rt in _runtimes(hass):
            rt._simulate(call.data["entity_id"], call.data.get("kind", "person"))  # noqa: SLF001

    hass.services.async_register(
        DOMAIN,
        "pause",
        pause,
        schema=vol.Schema(
            {
                vol.Required("minutes", default=5): vol.All(vol.Coerce(float), vol.Range(min=1, max=120)),
                vol.Optional("reason", default="manual"): cv.string,
            }
        ),
    )
    hass.services.async_register(DOMAIN, "send_summary", send_summary)
    hass.services.async_register(
        DOMAIN,
        "simulate_detection",
        simulate,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Optional("kind", default="person"): cv.string}),
    )
