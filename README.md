# Moonraker for Home Assistant

A Home Assistant custom integration for [Moonraker](https://moonraker.readthedocs.io/),
the API server for [Klipper](https://www.klipper3d.org/)-based 3D printers
(Klipper, Mainsail, Fluidd, Kiauh, BTT CB1/CM4, Creality Sonic Pad, etc.).

This is a fork of
[marcolivierarsenault/moonraker-home-assistant](https://github.com/marcolivierarsenault/moonraker-home-assistant)
with HACS-compliant layout, fixes, and some quality-of-life improvements.

## Features

- **Config flow** — set up entirely from the Home Assistant UI; no YAML.
- **Adaptive polling** — fast (1s) while a print is running, slow (configurable,
  30s default) when idle, with hysteresis so the cadence doesn't flap.
- **Stays loaded when the printer is offline** — if the printer or its host
  is unreachable at HA boot (or goes away later), the integration loads
  anyway and entities are simply marked **Unavailable** until the
  connection comes back. No more "Failed setup, will retry" red card.
- **Rich sensors** — printer/state, current print state, temperatures
  (extruder, bed, MCU, host, additional heaters), fans (part/controller/heater),
  print progress, ETA, layer info, filament used, history totals, and more.
- **Cameras** — auto-discovers webcams from Moonraker's `server.webcams.list`,
  plus a thumbnail preview camera that updates from the current g-code file.
  Honors **Use TLS** for `https://` hosts.
- **Buttons** — Emergency Stop, Pause/Resume/Cancel, Server/Host/Firmware
  Restart, Host Shutdown, Update Refresh, Reset Totals, Home X/Y/Z/All,
  and one button per discovered Klipper g-code macro. Start-From-Queue is
  shipped disabled by default (some Moonraker versions require job IDs);
  enable it from the entity registry if your setup supports it.
- **Switches** — `[power]` devices (Moonraker power plugins) and digital
  (non-PWM) `output_pin` switches.
- **Fans** — PWM `output_pin` entries whose name contains `fan` as an
  underscore-delimited token (e.g. `output_pin part_fan`, `case_fan`,
  `controller_fan_1` — but not `output_pin infant_heater`).
- **Numbers** — temperature targets, speed factor, fan speed, PWM output_pins.
- **Lights** — `neopixel` / `dotstar` / `led` / `pca9533` / `pca9632`
  objects, plus PWM `output_pin` entries whose name contains `led` as an
  underscore-delimited token.
- **Binary sensors** — filament switch/motion sensors and an Update Available
  sensor wired to Moonraker's update manager.
- **`moonraker.send_gcode` service** — send any g-code to any configured
  printer device. Works on older Home Assistant cores too (falls back to
  scanning `device.config_entries` when `primary_config_entry` isn't
  available).

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → top-right menu → **Custom repositories**.
2. Add `https://github.com/holdestmade/moonraker-home-assistant` as an
   **Integration**.
3. Install **Moonraker** from HACS.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration → Moonraker**.

### Manual

1. Copy `custom_components/moonraker/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Moonraker**.

## Configuration

When adding the integration you'll be asked for:

| Field | Required | Notes |
| --- | --- | --- |
| **Host** | yes | IP or hostname of the Moonraker host. Do **not** include `http://`, `https://`, or a trailing slash. |
| **Port** | no | Defaults to `7125`. |
| **Use TLS** | no | Tick if Moonraker is reachable over `wss://` / `https://`. |
| **API Key** | no | Required only if `[authorization]` is enabled in `moonraker.conf` and your HA IP isn't in `trusted_clients`. Must be 32 alphanumeric characters. |
| **Printer Name** | no | Defaults to the hostname reported by Moonraker. |

### Options

Open the integration's **Configure** dialog to set:

- **Integration polling rate (seconds)** — idle/slow cadence. While printing,
  the coordinator automatically polls every 1 second.
- **Camera Stream URL** / **Camera Snapshot URL** — override the auto-discovered
  webcam endpoints (use full URL or a path beginning with `/`).
- **Camera Port** / **Thumbnail Port** — separate ports for the MJPEG stream and
  for fetching g-code thumbnails (defaults to `80`).

## Services

### `moonraker.send_gcode`

Send any g-code command to the selected printer device.

```yaml
service: moonraker.send_gcode
target:
  device_id: <your moonraker device>
data:
  gcode: G28
```

## Offline behaviour

If the printer (or its host machine) is powered off when Home Assistant
starts — or it disappears later — the integration stays loaded and all of
its entities show as **Unavailable** in the UI. The coordinator keeps
retrying the websocket in the background and entities recover
automatically once it can talk to Moonraker again.

One caveat: Home Assistant platforms cannot add new entities after setup
finishes. So if the **very first** setup happens while the printer is
offline, only the static entities (the standard sensors and buttons)
register. Dynamically-discovered entities — g-code macros, `output_pin`s,
filament sensors, `[power]` devices, etc. — appear after a one-time
reload (**⋮ → Reload** on the integration card) once the printer is
reachable. After that first successful connect they're persisted in HA's
entity registry, so subsequent reboots-with-printer-off keep them present
(just unavailable).

## Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.moonraker: debug
    moonraker_api: debug
```

Common issues:

- **Entities stuck Unavailable** — confirm the host is reachable from HA
  and that Moonraker is listening
  (`curl http://<host>:7125/printer/info`). Debug logs will show the
  reconnect attempts.
- **"Invalid API key" in the config flow** — must be exactly 32
  alphanumeric characters.
- **No cameras appear** — make sure your webcams are configured in
  Mainsail/Fluidd (Moonraker's webcam list); otherwise set the camera
  URLs in the integration's options. If you're on `https://` make sure
  **Use TLS** is ticked when you set the integration up.
- **A dynamic entity (macro / power device / filament sensor) is
  missing** after the printer comes back from being offline — reload
  the integration once (**⋮ → Reload**). See *Offline behaviour* above.
- **Duplicate-entry error when adding a printer** — the integration
  uses `host:port` as a unique ID, so the same Moonraker can only be
  added once. Use a different host/port (or remove the existing entry
  first).

## Credits

- Original work by [Marc-Olivier Arsenault](https://github.com/marcolivierarsenault)
  and contributors of the upstream
  [moonraker-home-assistant](https://github.com/marcolivierarsenault/moonraker-home-assistant)
  project.
- Built on top of [`moonraker-api`](https://github.com/cmroche/moonraker-api).

## License

MIT — see [`LICENSE`](LICENSE) if included, otherwise inherits the upstream
project's license.
