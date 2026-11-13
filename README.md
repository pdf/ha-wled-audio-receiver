# WLED Audio-Reactive Receiver

A Home Assistant custom integration that receives WLED "audio sync" packets and drives Home Assistant lights and a Hyperion.NG ambilight with them.

This is an unofficial receiver for WLED's audio sync protocol. It is not part of, or endorsed by, the WLED project, and it is separate from Home Assistant's built-in WLED integration (this one uses the domain `wled_audio_receiver`).

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pdf&repository=ha-wled-audio-receiver&category=integration)

## What it does

WLED's audio-reactive usermod analyses sound (volume, peaks and 16 frequency bands) and can share that analysis with other devices as small UDP packets, about 43 per second. This integration listens for those packets and, while music is playing:

- sets the brightness and colour of the lights you choose, a few times per second: either every light follows the whole mix, or each light takes its own part of the spectrum;
- sends a small image to a Hyperion.NG server, so an ambilight shows the 16 frequency bands around the screen, in one of two layouts.

## Senders

Anything that sends WLED audio sync V2 packets works, for example:

- a WLED controller with a microphone or line-in, with Sync > "Send" enabled in its audio-reactive settings;
- [visualization.wledproxy](https://github.com/pdf/kodi.visualization.wledproxy), a Kodi visualization add-on that analyses the music Kodi plays and sends the packets;
- other software that implements the format.

The packet format is described in [docs/wled-sync.md](https://github.com/pdf/kodi.visualization.wledproxy/blob/main/docs/wled-sync.md) of the Kodi add-on's repository. The integration only accepts the V2 format (header `00002`, 44 bytes).

### Network

WLED sends to multicast group 239.0.0.1 on UDP port 11988 by default, with a TTL of 1. Multicast with TTL 1 does not leave the local network segment: if Home Assistant is on another subnet or VLAN than the sender, the packets never reach it. In that case set the sender to unicast to Home Assistant's IP address (for WLED or the Kodi add-on: the "unicast" or "host" setting) and you can turn off "Join multicast group" here.

If multicast stops after a few minutes on the same network, the switch probably has IGMP snooping enabled without an IGMP querier; enable a querier, or use unicast.

Home Assistant has to accept UDP on the chosen port. Home Assistant OS does; with Docker, use host networking or publish the port.

## Installation

The integration is installed as a custom repository: it is not in the default catalog.

On Home Assistant 2026.11 and later, through the built-in Marketplace:

1. Open the link above, or go to Settings > Marketplace, select "Add from link" in the upper-right corner, enter `pdf/ha-wled-audio-receiver` and select "Add". Adding a custom repository asks you to connect Home Assistant to GitHub once.
2. Open "WLED Audio-Reactive Receiver" in the Marketplace, install it and restart Home Assistant.
3. Settings > Devices & services > Add integration > WLED Audio-Reactive Receiver.

On Home Assistant 2026.10, through HACS:

1. Open the link above, or in HACS open the three-dot menu > Custom repositories, add `https://github.com/pdf/ha-wled-audio-receiver` with type Integration.
2. Download "WLED Audio-Reactive Receiver" in HACS and restart Home Assistant.
3. Settings > Devices & services > Add integration > WLED Audio-Reactive Receiver.

The Marketplace (or HACS) then offers updates like for any other integration.

## Configuration

Everything is set in the UI. The same fields are available later under the integration's "Configure" button; the integration reloads when they change.

| Field | Default | Meaning |
|---|---|---|
| UDP port | 11988 | Port the sender sends to. Each receiver needs its own port. |
| Join multicast group 239.0.0.1 | on | Receive WLED's default multicast packets. |
| Sender IP address | empty | Accept packets only from this IPv4 address. Empty accepts any sender. |
| Media player | none | Drive the outputs only while this media player is `playing`. Empty: react whenever packets arrive. |
| Lights | none | Lights that follow the music. In split mode, the order you select them in is the order from bass to treble. |
| Light mode | Mix | Mix: every light follows the whole mix. Split: the frequency bands are divided among the lights. See [Lights](#lights). |
| Minimum / maximum brightness | 10 % / 100 % | Light brightness at silence and at full volume. |
| Light update rate | 3 per second | Light commands per second; each uses a transition as long as the interval. |
| Restore lights afterwards | off | Snapshot the lights when music starts and restore them when it stops. |
| Send to Hyperion | off | Send the frequency bands to Hyperion.NG. |
| Hyperion host / port | - / 19444 | Hyperion's JSON API. |
| Hyperion layout | Bottom to top | Where the bands appear around the screen. See [Hyperion](#hyperion). |
| Hyperion priority | 150 | Lower wins. 150 is above screen capture (230-250) and below Home Assistant's Hyperion integration (128), so a colour set by hand still wins. |

### The "Music reactive" switch

The integration adds one switch, "Music reactive", to turn it on or off from dashboards and automations. Its state survives restarts. Its `active` attribute shows whether the outputs are being driven right now.

## When the outputs run

Lights and Hyperion are driven only while all of these hold:

1. the "Music reactive" switch is on;
2. the selected media player is `playing` (skipped if none is selected);
3. a valid packet arrived within the last second.

When one of them ends, pending light commands are cancelled and Hyperion's priority is cleared, so Hyperion returns to its screen capture. The lights are left as they are, unless "Restore lights afterwards" is on. This leaves the lights before and after music to your own automations: for example, an automation that dims the lights on pause keeps working, because the integration stops sending as soon as Home Assistant sees the pause. With a media player integration that pushes state changes (such as Kodi's) that is immediate; with one that polls, it follows the polling delay.

## Lights

Each update interval, the received frames are averaged and turned into light states according to the light mode.

### Mix

Every light gets the same state:

- brightness: minimum + volume x (maximum - minimum);
- colour: the hue between red (bass, 43-301 Hz), green (mids, 301-2412 Hz) and blue (treble, 2412-9259 Hz), weighted by the level of each band, at full saturation.

### Split

The 16 frequency bands (43 Hz to 9.3 kHz) are divided among the lights as evenly as possible, in the order the lights were selected: the first light gets the lowest bands, the last light the highest. With 3 lights, for example, they get bands 0-4 (43-430 Hz), 5-9 (430-1895 Hz) and 10-15 (1895-9259 Hz). With more than 16 lights, neighbouring lights share a band.

- brightness: minimum + level of the light's bands x (maximum - minimum), so each light pulses with its own part of the music;
- colour: fixed per light, from red for the lowest bands to violet for the highest, the same colours Hyperion shows.

To change the order, clear the lights in the options and select them again in the order you want.

Changes smaller than 3 brightness points and 10 degrees of hue are not sent. Each light gets the latest wanted state only: if a light is slow to respond, intermediate states are skipped instead of queued.

## Hyperion

The integration keeps one connection to Hyperion's JSON API and sends a 32 x 18 pixel image for every packet, with a duration of 500 ms. Hyperion scales the image onto your LED layout, so no LED layout needs to be configured here.

The 16 frequency bands run along the edges of the image, each in its own colour from red (bass) to violet (treble), with its brightness following its level. The layout decides where they go:

- **Bottom to top** (default): bass at the bottom centre, rising up both sides to treble at the top centre. Left and right are mirror images.
- **Sides to top and bottom**: bass at the middle of the left and right sides, rising up to the top corners and along the top to treble at the top centre, and the same downwards to the bottom centre. Mirrored four ways, so each quarter of the frame shows the whole spectrum.

If Home Assistant stops, Hyperion falls back to its capture within half a second. In Hyperion's list of sources it shows as "WLED Audio Receiver". Commands Hyperion rejects are logged as warnings, at most once a minute.

## Troubleshooting

Turn on debug logging for `custom_components.wled_audio_receiver`. Ignored packets (wrong format or filtered sender) are logged at most once a minute.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/pytest
```

Releases are made by pushing a tag `v<version>` that matches `version` in `manifest.json`.

## License

MIT
