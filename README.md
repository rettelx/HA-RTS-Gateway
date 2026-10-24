# RTS Gateway

[![GH-release](https://img.shields.io/github/v/release/rettelx/HA-RTS-Gateway.svg?style=flat-square)](https://github.com/rettelx/HA-RTS-Gateway/releases)
[![GH-downloads](https://img.shields.io/github/downloads/rettelx/HA-RTS-Gateway/total?style=flat-square)](https://github.com/rettelx/HA-RTS-Gateway/releases)
[![GH-last-commit](https://img.shields.io/github/last-commit/rettelx/HA-RTS-Gateway.svg?style=flat-square)](https://github.com/rettelx/HA-RTS-Gateway/commits/main)
[![GH-code-size](https://img.shields.io/github/languages/code-size/rettelx/HA-RTS-Gateway.svg?color=red&style=flat-square)](https://github.com/rettelx/HA-RTS-Gateway)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://github.com/hacs)

## 📌 Overview

**RTS Gateway** is an **unofficial** Home Assistant integration that lets you control your **RTS shutters, blinds, curtains, awnings and gates** (Somfy and compatible) directly from Home Assistant — **no cloud, no subscription and no proprietary hub**.

Everything goes through a radio gateway you build yourself: an **ESP32** board and a **CC1101** module, roughly ten euros of hardware. The integration transmits RTS frames (including rolling codes, persisted in Home Assistant), **listens to your existing physical remotes**, and keeps a position estimate up to date even when a shutter is moved by hand.

This project is not developed or maintained by Somfy. It builds on the community-documented RTS protocol and on Home Assistant's native `radio_frequency` integration.

> This integration was created with the help of Mistral Vibe.

---

## ⚠️ Testing status

Only the following parts have been tested by the author on real hardware:

- **56-bit RTS roller shutters** (position estimate, MY position, recalibration);
- **shutter groups**;
- **physical remotes** (antenna detection, command tracking).

Everything else is implemented from the protocol documentation but has **not been tested on real hardware**. In particular, the **80-bit RTS protocol is UNTESTED** (step-wheel motors and Modulis/Situo-style remotes): no 80-bit device has been tried with this gateway. The same goes for the **light and switch platforms** (On/Off sockets, dimmers and light receivers), sun/wind sensors, tilt (56 and 80 bit), gates and curtains. Feedback on any of these is welcome.

---

## ✅ Features

### 🪟 Covers

Roller shutters, shutters, venetian blinds, curtains (left/right/center opening), awnings, garage doors (1 or 3 buttons) and gates (left/right/center, 1 button):

- **estimated position** based on travel times (RTS has no position feedback: the estimate is re-anchored by a calibration cycle);
- **MY favorite position**, simulated by Home Assistant or native depending on the motor;
- **tilt** for venetian blinds: 80-bit step wheel or 56-bit timed motor;
- two everyday buttons: *MY position* and *Position recalibration*.

### 👥 RTS groups

A group is not a device: it is an extra RTS address paired into the memory of several motors. The integration creates the address (reserved range 1,000,000 – 2,097,151), walks you through pairing each member, then **drives all shutters with a single frame**. Member entities follow the movement at their own speed, and the group's displayed position is the average of its members — even when one of them is operated individually.

### 🎮 Physical remotes

Your existing remotes keep working — and Home Assistant **tracks their use**: every received frame updates the position estimate of the matching shutter, group or individual cover. You do not need to know a remote's address in advance: the assistant detects the addresses seen over the air while you press buttons.

### 💡 Sockets, lights, sensors — ⚠️ UNTESTED

On/Off RTS sockets (`switch`), dimmer sockets and light receivers (`light`), sun/wind sensors Sunis / Eolis / Soliris (`binary_sensor`, wind and sun alarms reported as attributes on linked shutters). The detailed model list is in the Installation section. **This whole part is untested on real hardware** — see the testing status above.

### 🧩 Services and events

About a dozen services (MY position, recalibration, pairing, raw commands…) and a `rts_gateway_frame` event fired for **every decoded frame** — enough to trigger automations from physical remote buttons. See the Services section below.

---

## 🔧 Required hardware

- 1 × ESP32-S3-Zero (Waveshare) — the only board tested with this project (it should work with similar boards, contributions welcome!)
- 1 × CC1101 module
- 1 × 433 MHz antenna

The CC1101 does everything: **transmit and receive** on the  RTS frequency, **433.42 MHz**.

| CC1101 pin | Role |
|---|---|
| GDO0 | transmit (TX) |
| GDO2 | receive (RX) |
| SCK / MISO / MOSI / CSN | SPI |

The board is selected in **one line** of `esphome.yaml` — the gateway
itself only needs SPI, one output pin and one input pin:

```yaml
substitutions:
  board_package: esphome_boards/esp32-s3-zero.yaml   # board + pin map
```

### Board variants

Only the **ESP32-S3-Zero (Waveshare)** has been tested with this
project — it is the default board, wired as follows (defined in
`esphome_boards/esp32-s3-zero.yaml`):

| CC1101 | SCK | MISO | MOSI | CSN | GDO0 (TX) | GDO2 (RX) |
|---|---|---|---|---|---|---|
| ESP32-S3-Zero | IO4 | IO5 | IO6 | IO7 | IO8 | IO1 |


Other ESP32 boards should work but are **untested**. The configuration
is modular: each board is a small ESPHome package in `esphome_boards/`
(board, framework and pin map) pulled in by the single `board_package`
line above — the rest of `esphome.yaml` is board-agnostic. The
Installation section below has a **step-by-step guide** for adapting
another board (board ids, variant, flash size, pin pitfalls), and if
yours works, open a pull request: contributions welcome!


---

## 🚀 Installation

### 1️⃣ Prepare the ESPHome gateway

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in
   `wifi_ssid`, `wifi_password`, `api_key` (the real file is ignored by
   git; OTA updates are protected by the API key).
2. Flash the ESP32 with `esphome.yaml`, keeping `esphome_components/`
   next to the YAML (local RTS decoding component).
3. Once the gateway is connected, its `radio_frequency.*` entity appears
   in Home Assistant ("RTS Gateway" device).

#### No ESP32-S3-Zero? Any ESP32 board can be used

The S3-Zero is only the tested default: everything board-specific
lives in a small package file under `esphome_boards/` and the rest of
`esphome.yaml` is board-agnostic. To use another board:

1. **Copy the board package**:
   `esphome_boards/esp32-s3-zero.yaml` → `esphome_boards/my-board.yaml`.
2. **Adjust the `esp32:` block** to your board:
   - `board:` — your board's ESPHome id (`esp32dev` for a classic
     ESP32 DevKit, `esp32-c3-devkitm-1` for a C3, `esp32-s2-saola-1`…),
   - `variant:` — `esp32`, `esp32s2`, `esp32s3` or `esp32c3`,
   - `flash_size:` — match your module (4MB is the safe default),
   - `framework:` — leave the Arduino framework unless your board
     requires ESP-IDF.
3. **Remap the pins** in the same file. The gateway needs 4 SPI pins,
   one **output** pin for GDO0 (TX) and one **input** pin for GDO2 (RX).
4. **Point `esphome.yaml` at your package**:

   ```yaml
   substitutions:
     board_package: esphome_boards/my-board.yaml   # board + pin map
   ```

5. **Flash** (`esphome run esphome.yaml`) and wire the CC1101 to the
   pins you chose — everything else (CC1101 setup, RTS component,
   radio_frequency entity) is board-independent.

Notes:

- **ESP8266 boards are not supported**: the transmitter uses the
  ESP32 RMT peripheral and its non-blocking send queue.
- If your board works, please open a pull request adding its package
  to `esphome_boards/` — contributions welcome!

### 2️⃣ Install the integration

**Via HACS (recommended)**:

1. Open **HACS**
2. Go to **⋮ → Custom repositories**
3. Add:
   - **Repository**: `https://github.com/rettelx/HA-RTS-Gateway`
   - **Category**: `Integration`
4. Download **RTS Gateway**, then restart Home Assistant

**Manually**: copy `custom_components/rts_gateway` into
`config/custom_components/`, then restart.

### 3️⃣ Configure the integration

1. In Home Assistant **2026.10 or newer**, go to
   **Settings → Devices & Services → Add integration**
2. Search for **RTS Gateway**
3. Select the transmitter, then add your devices

### Pairing a device

Pairing follows the Somfy procedure, guided by the assistant:

1. Hold **PROG** on a physical remote already paired with the motor,
   until the motor jogs briefly (memory open).
2. Let the assistant send the pairing command.

Pairing entirely from Home Assistant: the `enter_programming_mode`
and `send_prog` services, one after the other.

If the gateway is renamed, repair the entry via
Modify → Change RF transmitter.

### Supported devices

| HA platform | RTS devices                                                                                                                                                          |
|---|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cover` | Roller shutters and shutters, venetian blinds (56 and 80 bit tilt), curtains (L/R/center opening), awnings, garage doors (1/3 buttons), gates (L/R/center, 1 button) |
| `switch` | UNTESTED - On/Off RTS sockets (2401091, 2401093, 2401363), contact receivers                                                                                                    |
| `light` | UNTESTED - 200 W dimmer socket (2401092), RTS light receivers (1811251, 1822215), Modulis-compatible ranges                                                          |
| `binary_sensor` | UNTESTED - Sun/wind sensors Sunis, Eolis, Soliris (wind alarm + sun alarm)                                                                                                      |

Not covered: dry contacts on GPIO relays, **io-homecontrol**
motors, **RTW/RTV** transmission (433.92 MHz).

---

## 📖 Usage

### MY (favorite) position

- `my_position` (default 50 %) is set through the wizard or the
  `set_my_position` service.
- The **simulated MY** setting (default) fits motors without a real MY
  position: Home Assistant drives the shutter to `my_position`. A short
  MY press on a physical remote triggers the same movement — the antenna
  hears it, HA drives the shutter.
- The **native MY** setting fits motors that memorize their own favorite:
  a single MY frame is enough, and the *Memorize MY position* button
  writes the current position into the motor and into HA.

### Settings, editable at any time

Settings → Devices & Services → **RTS Gateway** → Modify:

- open/close travel times, tilt, step size, toggle command;
- up/down inversion (motor mounted upside down);
- position scale inversion (open = 0 %, closed = 100 %);
- stop method: *MY* (default) or *opposite pulse* (motors without MY).

When adding a device, only the options relevant to its type and protocol
are offered, grouped into sections.

### Physical remotes

You do **not need to know the address** of your remotes: in
RTS Gateway → Modify, the *Link a physical remote* entry detects the
addresses seen over the air. Press a button, refresh, select. Linked
addresses show up in the `linked_remote_addresses` attribute and can
also be typed in manually:

```yaml
service: rts_gateway.link_remote
data:
  entity_id: cover.bedroom
  remote_address: "01234567"
```

A remote's address can also be observed in the ESPHome logs
(`RTS frame: address=...`) or in the `rts_gateway_frame` event.

### Position drift

RTS motors have no position feedback: the estimate drifts slowly.
`recalibrate` (or the *Position recalibration* button) runs a
close → open → back-to-start cycle, re-anchored on the mechanical end
stops. Best scheduled as a nightly automation.

### RTS groups

A group is created from the dedicated **"Add a shutter group"** entry
(setup and reconfiguration menus). The wizard is minimal — name, type,
generated address in the reserved range — then moves on to member
selection.

Every checked or unchecked shutter goes through the guided pairing
sequence (Somfy procedure):

1. The gateway holds a long PROG press: the motor does a **short
   back-and-forth** (memory open). Confirm — or open the memory yourself
   with a physical remote if the motor does not react.
2. The group's PROG press is sent: the motor reacts again
   (pairing confirmed). Confirm.

Membership (`group_addresses` on each shutter, `members` on the group)
is saved only after confirmation. Removing a group follows the same
principle from **"Remove a shutter group"**: guided unpairing of each
member (optional), then removal of the group.

Driving the group also moves the member entities: their estimate follows
the movement (at their own speed, without extra radio), including target
position, MY, tilt and recalibration. The group's displayed position is
the **average** of its members.

The `add_to_group` / `remove_from_group` services send the same
sequence without confirmation — keep them for automations.

### Sun/wind sensors

Declare them from the add menu: each sensor exposes two `binary_sensor`
entities (wind, sun) and updates the `wind_alarm`/`sun_alarm` attributes
of linked shutters (`link_remote` with the sensor's address). If a motor
reacts on its own to an alarm, realign its position with `set_position`.

---

## 🛠️ Services

| Service | Purpose |
|---|---|
| `goto_my_position` | go to the favorite position |
| `set_my_position` | save the favorite position |
| `memorize_my_position` | long MY: the motor memorizes its position (native MY) |
| `link_remote` / `unlink_remote` | link / detach a physical remote |
| `add_to_group` / `remove_from_group` | pair / unpair shutters to a group |
| `enter_programming_mode` | long PROG: the motor enters programming mode |
| `send_prog` | short PROG: pairs / unpairs the gateway's address |
| `set_position` / `set_tilt_position` | realign the estimate without moving the motor |
| `recalibrate` | close → open → back cycle |
| `send_command` | raw RTS command (UP, DOWN, MY, STOP, PROG, STEP_*) |
| `send_step_command` | step with explicit size, 1-127 (80 bit) |

Every decoded frame also fires a `rts_gateway_frame` event with
`address`, `command`, `rolling_code`, `step`, `bits`, `device` and
`source` (`remote` or `gateway`).

---

## 🔎 Troubleshooting

- **The shutter does not respond**: check that the gateway's
  `radio_frequency` entity is available; look at the ESPHome logs
  (`rts_gateway`) and the integration diagnostics.
- **The state no longer follows the physical remote**: check that RX is
  enabled (General settings) and the address is linked; use the
  *Link a physical remote* assistant.
- **The position drifts**: run `recalibrate` (ideally as a nightly
  automation) and check the open/close travel times.
- **A command stops working after using other software**: rolling codes
  desynchronized; re-pair the motor.
- **Pairing fails**: the gateway must see the PROG frame in the ESPHome
  logs; check the antenna and the CC1101 power supply.

---

## 🗑️ Uninstalling

1. Settings → Devices & Services → RTS Gateway → the three dots →
   **Delete entry**.
2. So the motors stop answering the gateway: unpair them first
   (`enter_programming_mode` then `send_prog`, or the motor's PROG
   button).
3. Then remove the `custom_components/rts_gateway` folder.

---

## 🧪 Development

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements_test.txt
```

```bash
.venv/Scripts/python.exe -m pytest tests/ -q          # 324 tests
.venv/Scripts/python.exe -m mypy custom_components/rts_gateway   # strict
.venv/Scripts/python.exe -m ruff check tests custom_components/rts_gateway
```

Strict typing (Platinum checklist), ~99 % coverage — configuration in
`mypy.ini`. A GitHub Actions CI runs on every push: tests, typing, lint,
ESPHome configuration validation and HACS validation (see
`.github/workflows/`).

### Architecture

```
Home Assistant (rts_gateway integration)
   |  SomfyRTSCommand (rf-protocols) via radio_frequency.async_send_command
   v
radio_frequency.radio_433_42_mhz entity (created by the esphome integration)
   |  Native ESPHome API (radio_frequency_transmit_raw_timings)
   v
ESP32: ir_rf_proxy -> remote_transmitter -> GPIO3 == GDO0 (CC1101, TX ASK)
                                                            ──> 433.42 MHz
ESP32: CC1101 (RX OOK) -> GDO2 -> remote_receiver -> rts_gateway component
        -> HA event esphome.rts_gateway_rx -> covers update
```

Frame encoding (checksum, obfuscation, Manchester, repeats) is handled by
the `rf-protocols` library of the native `radio_frequency` integration.
Rolling codes are persisted in Home Assistant storage
(`rts_gateway.rolling_codes`): they survive restarts — an absolute
requirement not to unpair the motors.

### Protocols and caveats

- **56-bit RTS** (default): the vast majority of RTS motors (Somfy and
  compatible).
- **80-bit RTS** (untested on real hardware — see the testing status):
  step-wheel remotes (Modulis, Situo) and their motors — 56-bit frame
  + 3 non-obfuscated extension bytes, 12/14-pulse hardware sync.
- **Tilt**: on 80 bit, step-wheel commands (Step Up/Down,
  `tilt_step_percent` for the estimate, `step_size` for the frame);
  on 56 bit, a timed tilt motor (`tilt_time_ms`). A short press of a
  physical remote is read as up/down, a long press (≥ 15 repeats) as
  tilt.
- Use **only one command source** per address: rolling codes otherwise
  desynchronize (remedy: re-pair the motor).
- Do not reuse an existing remote's address: every RTS transmitter must
  have a unique address.
- The RTS frequency is **433.42 MHz**: configure the CC1101 with
  `frequency: 433.42MHz`.

---

## 🙏 Acknowledgments

This project is inspired by:

- [ESPSomfy-RTS](https://github.com/rstrouse/ESPSomfy-RTS) (rstrouse)
  — opening types, tilt model, 80-bit step-wheel format.
- esphome_somfy (community ESPHome project) — RTS frame decoding on the
  ESPHome side.
- [Somfy RTS protocol](https://pushstack.wordpress.com/somfy-rts-protocol/)
  (pushstack) — RTS protocol documentation.

---

## 🤝 Contributing

Community project: issues, suggestions and pull requests are welcome on
GitHub.

---

## 📄 License

This project is released under the **MIT License** — see [LICENSE](LICENSE).
