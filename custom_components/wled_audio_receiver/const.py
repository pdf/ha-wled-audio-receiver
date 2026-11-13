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
CONF_LIGHTS: Final = "lights"
CONF_MIN_BRIGHTNESS: Final = "min_brightness"
CONF_MAX_BRIGHTNESS: Final = "max_brightness"
CONF_UPDATE_RATE: Final = "update_rate"
CONF_RESTORE: Final = "restore"

# Hyperion
CONF_HYPERION: Final = "hyperion"
CONF_HYPERION_HOST: Final = "hyperion_host"
CONF_HYPERION_PORT: Final = "hyperion_port"
CONF_HYPERION_PRIORITY: Final = "hyperion_priority"

DEFAULT_PORT: Final = 11988
DEFAULT_MULTICAST: Final = True
DEFAULT_MIN_BRIGHTNESS: Final = 10
DEFAULT_MAX_BRIGHTNESS: Final = 100
DEFAULT_UPDATE_RATE: Final = 3.0
DEFAULT_RESTORE: Final = False
DEFAULT_HYPERION: Final = False
DEFAULT_HYPERION_PORT: Final = 19444
DEFAULT_HYPERION_PRIORITY: Final = 150

MULTICAST_GROUP: Final = "239.0.0.1"

# A gap longer than this ends the active state (WLED uses about a second).
PACKET_TIMEOUT: Final = 1.0

# Hyperion limits origin to 4-20 characters (schema-image.json).
HYPERION_ORIGIN: Final = "WLED Audio Receiver"
HYPERION_DURATION_MS: Final = 500
