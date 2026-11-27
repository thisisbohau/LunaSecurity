"""Loads the integration in a real (test) Home Assistant and walks the main flows."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.luna_alarm.const import DOMAIN

GARDEN = "binary_sensor.garden_cam_person"
DOORBELL = "binary_sensor.doorbell_person"
ENTRY_CAM = "binary_sensor.entry_cam_person"
SIREN = "siren.homebase_siren"

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


DATA = {
    "wake_sensors": [DOORBELL],
    "visitor_sensors": [ENTRY_CAM],
    "offlimits_sensors": [GARDEN],
    "contact_sensors": [],
    "car_events": [],
    "persons": ["person.alex"],
    "sirens": [SIREN],
    "camera_lights": ["switch.garden_cam_light"],
    "flash_lights": [],
    "audio_players": [],
    "notify_services": ["notify.mobile_app_test"],
}


async def _setup(hass: HomeAssistant):
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    for ent in (GARDEN, DOORBELL, ENTRY_CAM):
        hass.states.async_set(ent, "off")
    hass.states.async_set(SIREN, "off")
    hass.states.async_set("switch.garden_cam_light", "off")
    calls = {
        "siren": async_mock_service(hass, "siren", "turn_on"),
        "siren_off": async_mock_service(hass, "siren", "turn_off"),
        "notify": async_mock_service(hass, "notify", "mobile_app_test"),
        "light_on": async_mock_service(hass, "homeassistant", "turn_on"),
    }
    async_mock_service(hass, "homeassistant", "turn_off")
    async_mock_service(hass, "scene", "create")
    async_mock_service(hass, "scene", "turn_on")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


def _panel(hass):
    return hass.states.get("alarm_control_panel.luna_alarm")


async def _advance(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_config_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "detection": {"offlimits_sensors": [GARDEN]},
            "house": {"persons": ["person.alex"]},
            "outputs": {"sirens": [SIREN]},
            "notifications": {"notify_services": ["notify.mobile_app_test"]},
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"]["offlimits_sensors"] == [GARDEN]


async def test_options_flow_shows_timings(hass: HomeAssistant):
    entry, _ = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert "timings" in [str(k) for k in result["data_schema"].schema]


async def test_test_mode_simulates_without_outputs(hass: HomeAssistant, freezer):
    entry, calls = await _setup(hass)
    assert hass.states.get("switch.luna_alarm_test_mode").state == "on"
    assert _panel(hass).state == "disarmed"

    await hass.services.async_call("alarm_control_panel", "alarm_arm_away", {"entity_id": "alarm_control_panel.luna_alarm"}, blocking=True)
    assert _panel(hass).state == "arming"
    freezer.tick(timedelta(seconds=61))
    await _advance(hass, 0)
    assert _panel(hass).state == "armed_away"

    hass.states.async_set(GARDEN, "on")
    await hass.async_block_till_done()
    assert _panel(hass).state == "pending"
    alert = calls["notify"][-1].data
    assert alert["title"].startswith("[TEST]")
    assert alert["data"]["push"]["interruption-level"] != "critical"
    assert [a["action"] for a in alert["data"]["actions"]][0] == "LUNA_ALARM_HARMLESS"

    freezer.tick(timedelta(seconds=121))
    await _advance(hass, 0)
    assert _panel(hass).state == "triggered"
    assert calls["siren"] == []
    assert calls["light_on"] == []

    await hass.services.async_call("button", "press", {"entity_id": "button.luna_alarm_send_summary_now"}, blocking=True)
    await hass.async_block_till_done()
    summary = calls["notify"][-1].data
    assert summary["title"] == "[TEST] Alarm summary"
    assert "Alerts: 1" in summary["message"]
    assert hass.states.get("sensor.luna_alarm_last_summary").attributes["simulated_outputs"]["sirens_on"] == 1


async def test_live_mode_sounds_sirens(hass: HomeAssistant, freezer):
    entry, calls = await _setup(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.luna_alarm_test_mode"}, blocking=True)
    await hass.services.async_call("alarm_control_panel", "alarm_arm_away", {"entity_id": "alarm_control_panel.luna_alarm"}, blocking=True)
    freezer.tick(timedelta(seconds=61))
    await _advance(hass, 0)
    hass.states.async_set(GARDEN, "on")
    await hass.async_block_till_done()
    assert calls["notify"][-1].data["data"]["push"]["interruption-level"] == "critical"
    freezer.tick(timedelta(seconds=121))
    await _advance(hass, 0)
    assert _panel(hass).state == "triggered"
    assert calls["siren"] and calls["siren"][0].data["duration"] == 180
    await hass.services.async_call("alarm_control_panel", "alarm_disarm", {"entity_id": "alarm_control_panel.luna_alarm"}, blocking=True)
    await hass.async_block_till_done()
    assert _panel(hass).state == "disarmed"
    assert calls["siren_off"]


async def test_notification_action_harmless(hass: HomeAssistant, freezer):
    entry, calls = await _setup(hass)
    await hass.services.async_call("alarm_control_panel", "alarm_arm_away", {"entity_id": "alarm_control_panel.luna_alarm"}, blocking=True)
    freezer.tick(timedelta(seconds=61))
    await _advance(hass, 0)
    await hass.services.async_call(DOMAIN, "simulate_detection", {"entity_id": GARDEN}, blocking=True)
    assert _panel(hass).state == "pending"
    hass.bus.async_fire("mobile_app_notification_action", {"action": "LUNA_ALARM_HARMLESS"})
    await hass.async_block_till_done()
    assert _panel(hass).state == "arming"


async def test_self_check_blocks_arming(hass: HomeAssistant, freezer):
    entry, calls = await _setup(hass)
    hass.states.async_set(SIREN, "unavailable")
    freezer.tick(timedelta(seconds=35))
    await _advance(hass, 0)
    freezer.tick(timedelta(seconds=65))
    await _advance(hass, 0)
    assert hass.states.get("binary_sensor.luna_alarm_self_check").state == "on"
    await hass.services.async_call("alarm_control_panel", "alarm_arm_away", {"entity_id": "alarm_control_panel.luna_alarm"}, blocking=True)
    assert _panel(hass).state == "disarmed"
    assert "Self-check failed" in calls["notify"][-1].data["message"]
