# M5Stick Mitsubishi AC Bridge

Control a Mitsubishi Heavy Industries (MHI) air conditioner over your local network using an **M5StickC (ESP32)** as an infrared bridge.

The M5Stick runs MicroPython firmware that exposes a mini HTTP server: it accepts raw IR byte payloads, encodes them into the MHI infrared protocol, and transmits them. It also serves standalone power on/off/toggle endpoints that work without the host server, and it can **decode** incoming MHI signals (useful for reverse-engineering your remote).

A companion FastAPI server runs on your host machine, builds the MHI payloads from friendly parameters (mode, temperature, fan speed, airflow), and forwards them to the bridge.

```
  ┌─────────────┐   HTTP/JSON    ┌────────────────────┐    38kHz IR    ┌────────────────┐
  │  server.py  │ ─────────────► │  M5Stick (ESP32)   │ ─────────────► │  MHI A/C unit  │
  │  (FastAPI)  │                │  main.py + IR LED  │                │                │
  └─────────────┘                └────────────────────┘                └────────────────┘
```

## Repository layout

| Path              | Description                                                                    |
|-------------------|--------------------------------------------------------------------------------|
| `server.py`       | Host-side FastAPI server. Builds MHI payloads and calls the bridge.            |
| `m5stick/boot.py` | Device boot script: PMIC power-on and long-press power-off.                    |
| `m5stick/main.py` | Device firmware: WiFi, IR transmit/receive, battery, power state, HTTP server. |

## Hardware

- M5StickC / M5StickC PLUS (ESP32)
- IR transmitter LED and IR receiver module (optional)

Default GPIO wiring (edit the constants at the top of `m5stick/main.py` to match your build):

| Function          | GPIO |
|-------------------|------|
| IR receive (RX)   | 33   |
| IR transmit (TX)  | 26   |
| Front button      | 37   |
| Battery ADC       | 38   |

## Flashing the M5Stick

1. Download the MicroPython ESP32 firmware image from [micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/).

2. Erase the flash and write the firmware:

   ```bash
   esptool --chip esp32 --port <PORT> erase_flash
   esptool --chip esp32 --port <PORT> --baud 460800 write_flash -z 0x1000 ESP32_GENERIC-<version>.bin
   ```

3. Set your WiFi credentials in `m5stick/main.py`:

   ```python
   WIFI_SSID = "YOUR_WIFI_SSID"
   WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
   ```

4. Upload the scripts to the device:

   ```bash
   ampy --port <PORT> put m5stick/boot.py
   ampy --port <PORT> put m5stick/main.py
   ```

5. (Optional) Open a serial console to see the device log and its assigned IP address:

   ```bash
   python -m serial.tools.miniterm <PORT> 115200
   ```

## Bridge HTTP API (on the M5Stick)

| Method | Endpoint    | Body                   | Description                                                       |
|--------|-------------|------------------------|-------------------------------------------------------------------|
| `POST` | `/transmit` | JSON list of byte ints | Encodes and transmits the raw MHI payload.                        |
| `GET`  | `/battery`  | —                      | Returns `{"voltage": ..., "percentage": ...}`.                    |
| `GET`  | `/on`       | —                      | Sends the built-in power-on command. Returns `{"state": "on"}`.   |
| `GET`  | `/off`      | —                      | Sends the built-in power-off command. Returns `{"state": "off"}`. |
| `GET`  | `/toggle`   | —                      | Flips the power state and returns the new one.                    |

`/on`, `/off` and `/toggle` transmit the hardcoded `COMMAND_ON` / `COMMAND_OFF` payloads defined at the top of `m5stick/main.py`, so basic power control keeps working even when the host server is down. They share their power state with the front button, so pressing the button and calling `/toggle` stay in sync.

> **Note:** the bridge *assumes* the power state rather than measuring it. If the A/C is switched with its original remote, the bridge's idea of the state goes stale and the next `/toggle` will be off by one. Call `/on` or `/off` to resynchronise.

## Host server

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Point the server at your bridge by editing the constants at the top of `server.py`:

   ```python
   BRIDGE_HOST = "192.168.1.100" # the M5Stick's IP address on your network
   BRIDGE_PORT = 80
   ```

3. Run the server:

   ```bash
   python server.py
   ```

### Host API

| Method | Endpoint   | Description                                  |
|--------|------------|----------------------------------------------|
| `GET`  | `/on`      | Turn the A/C on with default settings.       |
| `GET`  | `/off`     | Turn the A/C off.                            |
| `POST` | `/command` | Send a full climate command (see below).     |

`POST /command` accepts a JSON body:

```json
{
  "power_on": true,
  "mode": "Cool",
  "temperature": 22,
  "fan_speed": "Hi",
  "airflow_vertical": "All",
  "airflow_horizontal": "All"
}
```

| Field                | Values                                                                                                                      | Notes                               |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------|-------------------------------------|
| `mode`               | `Auto`, `Cool`, `Heat 1`, `Heat 2`, `Dry`                                                                                   | `Dry` ignores fan speed.            |
| `temperature`        | Standard modes: `18`–`30`. `Auto`: `-6`–`+6` offset. `Heat 2`: ignored.                                                     | Clamped to the valid range.         |
| `fan_speed`          | `Auto`, `Hi`, `Med`, `Lo`                                                                                                   |                                     |
| `airflow_vertical`   | `All`, `1`–`5`, `NoN`                                                                                                       | Vertical (up/down) louver position. |
| `airflow_horizontal` | `All`, `Left, Left`, `Left, Middle`, `Middle, Middle`, `Middle, Right`, `Right, Right`, `Left, Right`, `Right, Left`, `NoN` | Horizontal louver position.         |

Every response returns the raw byte payload that was transmitted, e.g. `{"payload": [82, 174, ...]}`.