"""Constants for Luna Alarm."""

from __future__ import annotations

DOMAIN = "luna_alarm"
STORAGE_VERSION = 1
SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"  # formatted with entry_id
EVENT_JOURNAL = f"{DOMAIN}_event"

# Detection sources by role
CONF_WAKE = "wake_sensors"
CONF_VISITOR = "visitor_sensors"
CONF_OFFLIMITS = "offlimits_sensors"
CONF_CONTACTS = "contact_sensors"
CONF_CAR = "car_events"

# House
CONF_PERSONS = "persons"
CONF_LOCK = "lock"
CONF_INSIDE_ATTR = "inside_unlock_attribute"
CONF_INSIDE_VALUES = "inside_unlock_values"
CONF_BUILDING_MODE = "building_mode"
CONF_NIGHT_STATES = "night_states"
CONF_KILLSWITCH = "killswitch"
CONF_PRESS_SINGLE = "press_single"
CONF_PRESS_DOUBLE = "press_double"
CONF_PRESS_LONG = "press_long"

# Outputs
CONF_SIRENS = "sirens"
CONF_CAM_LIGHTS = "camera_lights"
CONF_FLASH_LIGHTS = "flash_lights"
CONF_AUDIO_PLAYERS = "audio_players"
CONF_AUDIO_MEDIA = "audio_media"

# Notifications & health
CONF_NOTIFY = "notify_services"
CONF_HEALTH_EXTRA = "health_entities"
CONF_BATTERIES = "battery_sensors"
CONF_MIN_BATTERY = "min_battery"

# Timings (seconds unless noted)
CONF_ARMING_AWAY = "arming_away"
CONF_ARMING_NIGHT = "arming_night"
CONF_PENDING_AWAY = "pending_away"
CONF_PENDING_NIGHT = "pending_night"
CONF_TRIGGER_TIME = "trigger_time"
CONF_SIREN_TIME = "siren_time"
CONF_WAKE_WINDOW = "wake_window"
CONF_VISITOR_HITS = "visitor_hits"
CONF_LEAVE_DELAY = "leave_delay"
CONF_PAUSE_UNLOCK = "pause_unlock"
CONF_PAUSE_ARRIVAL = "pause_arrival"
CONF_PAUSE_BUTTON = "pause_button"
CONF_NIGHT_END = "night_end"
CONF_SUMMARY_TIME = "summary_time"
CONF_CAR_HITS = "car_hits"

DEFAULTS: dict[str, object] = {
    CONF_INSIDE_ATTR: "",
    CONF_INSIDE_VALUES: "",
    CONF_NIGHT_STATES: "Nacht, night",
    CONF_PRESS_SINGLE: "single, single_press, press",
    CONF_PRESS_DOUBLE: "double, double_press",
    CONF_PRESS_LONG: "long, long_press, hold",
    CONF_AUDIO_MEDIA: "",
    CONF_MIN_BATTERY: 15,
    CONF_ARMING_AWAY: 60,
    CONF_ARMING_NIGHT: 300,
    CONF_PENDING_AWAY: 120,
    CONF_PENDING_NIGHT: 60,
    CONF_TRIGGER_TIME: 600,
    CONF_SIREN_TIME: 180,
    CONF_WAKE_WINDOW: 180,
    CONF_VISITOR_HITS: 3,
    CONF_LEAVE_DELAY: 120,
    CONF_PAUSE_UNLOCK: 300,
    CONF_PAUSE_ARRIVAL: 600,
    CONF_PAUSE_BUTTON: 300,
    CONF_NIGHT_END: "06:30:00",
    CONF_SUMMARY_TIME: "09:00:00",
    CONF_CAR_HITS: 3,
}

ACTION_HARMLESS = "LUNA_ALARM_HARMLESS"
ACTION_DISARM = "LUNA_ALARM_DISARM"
ACTION_SOUND_NOW = "LUNA_ALARM_SOUND_NOW"

JOURNAL_MAX_AGE_DAYS = 14
JOURNAL_MAX_ENTRIES = 5000
HEALTH_GRACE_SECONDS = 60
