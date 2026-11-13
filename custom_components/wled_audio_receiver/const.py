"""Constants for the WLED Audio-Reactive Receiver integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "wled_audio_receiver"
NAME: Final = "WLED Audio-Reactive Receiver"

# Input
CONF_PORT: Final = "port"
CONF_MULTICAST: Final = "multicast"
CONF_SENDER: Final = "sender"

# Gating
CONF_MEDIA_PLAYER: Final = "media_player"

# Lights
CONF_SEND_TO_LIGHTS: Final = "send_to_lights"
CONF_LIGHTS: Final = "lights"
CONF_LIGHT_MODE: Final = "light_mode"
CONF_LIGHT_HUE_START: Final = "light_hue_start"
CONF_LIGHT_HUE_END: Final = "light_hue_end"
CONF_LIGHT_HUE_REVERSE: Final = "light_hue_reverse"
CONF_MIN_BRIGHTNESS: Final = "min_brightness"
CONF_MAX_BRIGHTNESS: Final = "max_brightness"
CONF_UPDATE_RATE: Final = "update_rate"
CONF_RESTORE: Final = "restore"

# Hyperion
CONF_HYPERION: Final = "hyperion"
CONF_HYPERION_HOST: Final = "hyperion_host"
CONF_HYPERION_PORT: Final = "hyperion_port"
CONF_HYPERION_PRIORITY: Final = "hyperion_priority"
CONF_HYPERION_LAYOUT: Final = "hyperion_layout"
CONF_HYPERION_HUE_START: Final = "hyperion_hue_start"
CONF_HYPERION_HUE_END: Final = "hyperion_hue_end"
CONF_HYPERION_HUE_REVERSE: Final = "hyperion_hue_reverse"
CONF_HYPERION_TOKEN: Final = "hyperion_token"
CONF_HYPERION_INSTANCES: Final = "hyperion_instances"
CONF_HYPERION_SERVER: Final = "hyperion_server"

# Value of the server picker for entering host and port by hand.
HYPERION_SERVER_MANUAL: Final = "manual"
# Home Assistant's own Hyperion integration, whose servers are offered.
HYPERION_CORE_DOMAIN: Final = "hyperion"

DEFAULT_PORT: Final = 11988
DEFAULT_MULTICAST: Final = True
LIGHT_MODE_MIX: Final = "mix"
LIGHT_MODE_SPLIT: Final = "split"
LIGHT_MODES: Final = [LIGHT_MODE_MIX, LIGHT_MODE_SPLIT]

DEFAULT_LIGHT_MODE: Final = LIGHT_MODE_MIX
DEFAULT_MIN_BRIGHTNESS: Final = 10
DEFAULT_MAX_BRIGHTNESS: Final = 100
DEFAULT_UPDATE_RATE: Final = 3.0
DEFAULT_RESTORE: Final = False
DEFAULT_HYPERION: Final = False
DEFAULT_SEND_TO_LIGHTS: Final = True
DEFAULT_HYPERION_PORT: Final = 19444
DEFAULT_HYPERION_PRIORITY: Final = 150

HYPERION_LAYOUT_BOTTOM_UP: Final = "bottom_up"
HYPERION_LAYOUT_FOUR_WAY: Final = "four_way"
HYPERION_LAYOUTS: Final = [HYPERION_LAYOUT_BOTTOM_UP, HYPERION_LAYOUT_FOUR_WAY]
DEFAULT_HYPERION_LAYOUT: Final = HYPERION_LAYOUT_BOTTOM_UP

MULTICAST_GROUP: Final = "239.0.0.1"

# Default colour range: red (0 degrees) for bass up to violet (270) for treble.
DEFAULT_HUE_START: Final = 0.0
DEFAULT_HUE_END: Final = 270.0
DEFAULT_HUE_REVERSE: Final = False

# A gap longer than this ends the active state (WLED uses about a second).
PACKET_TIMEOUT: Final = 1.0

# Hyperion limits origin to 4-20 characters (schema-image.json).
HYPERION_ORIGIN: Final = "WLED Audio Receiver"
HYPERION_DURATION_MS: Final = 500
