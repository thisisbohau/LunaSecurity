# Luna Security – Luna Alarm

Away protection and night watch for Home Assistant, built on eufy cameras (ha-eufy-sdk),
presence, the front door lock and the building mode helper.

**It starts in test mode.** Everything is evaluated, logged and notified as normal
(non-critical) pushes marked `[TEST]`, but no siren, light, flash or audio is ever
switched. Test mode is enforced twice: in the state machine and again right before any
service call.

## Install

**HACS:** HACS → ⋮ → Custom repositories → add `https://github.com/thisisbohau/LunaSecurity`
as type *Integration*, download **Luna Alarm**, restart Home Assistant.

**Manual:** copy `custom_components/luna_alarm` to `/config/custom_components/` and restart.

Then:

1. Settings → Devices & services → Add integration → **Luna Alarm**.
2. Pick the entities per role (below). Timings live in the integration's **Configure** dialog.

### Roles

| Field | What to pick (eufy via ha-eufy-sdk) |
|---|---|
| Wake cameras | Person sensors of the cameras **outside** the gate (doorbell, driveway) |
| Visitor zone | Person sensor of the entry camera **inside** the gate |
| Off-limits zone | Person sensor of the garden / fence camera |
| Window contacts | Window contact sensors |
| Car watch | `event.*_detection` of the camera that watches the car |
| Sirens | The HomeBase siren plus any camera sirens |
| Camera lights | The cameras' `switch.*_light` entities |
| Notify services | `notify.mobile_app_<phone>` per phone |
| Battery sensors | The cameras' `sensor.*_battery` entities |

## Entities

| Entity | Purpose |
|---|---|
| `alarm_control_panel.luna_alarm` | Arm away / night, disarm. Attributes: test mode, pause, alarm reason |
| `switch.luna_alarm_alarm_system` | Master switch (off = disabled, nothing arms) |
| `switch.luna_alarm_test_mode` | Test mode (on after install) |
| `switch.luna_alarm_auto_arm_away` / `…_night` | Automatic arming |
| `binary_sensor.luna_alarm_self_check` | On = something monitored is missing; `failing` attribute |
| `sensor.luna_alarm_last_event` | Newest journal line; last 25 in `recent` |
| `sensor.luna_alarm_last_summary` | Time of the last summary; full details as attributes |
| `button.luna_alarm_pause_5_minutes`, `button.luna_alarm_send_summary_now` | |

Every decision is also fired as a `luna_alarm_event` on the event bus (Developer tools →
Events → listen to `luna_alarm_event`) and kept in a 14-day journal in `.storage`.

## Services

- `luna_alarm.pause` – pause the armed system for N minutes.
- `luna_alarm.send_summary` – send the summary now.
- `luna_alarm.simulate_detection` – feed a fake detection for a configured source. Good for
  walking through flows without going outside.

## Morning summary

Sent when the night watch ends (night end time, inside unlock after that time, building
mode leaving night, or a manual disarm of the night watch). If no summary went out in the
last 20 hours, one is sent at the fallback time (default 09:00), so away-only days get one
too. Works the same in test mode, marked `[TEST]` and listing the simulated outputs.

## Things to know during the test week

- **Inside unlock:** if your lock doesn't report the unlock source, leave the attribute
  field empty. Every unlock then counts as "from inside" (pause, never alarm).
- **HomeKit / other bridges:** if the alarm panel is exposed to HomeKit, a simulated
  `triggered` state can still show up as an alarm on your iPhones. Leave the panel out of
  HomeKit until test mode is off.
- **Notification buttons:** Harmless / Disarm need Face ID on the lock screen.
- Run the tests: `pip install pytest-homeassistant-custom-component && pytest`.
