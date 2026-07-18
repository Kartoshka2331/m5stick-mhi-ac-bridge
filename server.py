from typing import Literal

import aiohttp
import uvicorn
from fastapi import FastAPI, status
from pydantic import BaseModel


BRIDGE_HOST = "192.168.1.100"
BRIDGE_PORT = 80
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000


Mode = Literal["Auto", "Cool", "Heat 1", "Heat 2", "Dry"]
FanSpeed = Literal["Auto", "Hi", "Med", "Lo"]
AirflowVertical = Literal["All", "1", "2", "3", "4", "5", "NoN"]
AirflowHorizontal = Literal["All", "Left, Left", "Left, Middle", "Middle, Middle", "Middle, Right", "Right, Right", "Left, Right", "Right, Left", "NoN"]

MHI_HEADER = [0x52, 0xAE, 0xC3, 0x26, 0xD9]

MODE_NIBBLES = {
    "Auto": 0x07,
    "Cool": 0x06,
    "Heat 1": 0x03,
    "Heat 2": 0x04,
    "Dry": 0x05,
}
FAN_NIBBLES = {
    "Auto": 0xE0,
    "Hi": 0x60,
    "Med": 0x80,
    "Lo": 0xA0,
}
VERTICAL_AIRFLOW = {
    "All": (0x0F, False),
    "1": (0x07, False),
    "2": (0x1F, True),
    "3": (0x17, True),
    "4": (0x0F, True),
    "5": (0x07, True),
    "NoN": (0x1F, False),
}
HORIZONTAL_AIRFLOW = {
    "All": 0x7F,
    "Left, Left": 0xFB,
    "Left, Middle": 0xBB,
    "Middle, Middle": 0x7B,
    "Middle, Right": 0x3B,
    "Right, Right": 0xF7,
    "Left, Right": 0xB7,
    "Right, Left": 0x77,
    "NoN": 0xFF,
}


class ClimateCommand(BaseModel):
    power_on: bool = True
    mode: Mode = "Cool"
    temperature: int = 18
    fan_speed: FanSpeed = "Hi"
    airflow_vertical: AirflowVertical = "All"
    airflow_horizontal: AirflowHorizontal = "All"


def build_temperature_nibble(mode: Mode, temperature: int) -> int:
    if mode == "Auto":
        offset = max(-6, min(6, temperature))
        return 0x80 - (offset * 0x10)
    if mode == "Heat 2":
        return 0xE0

    celsius = max(18, min(30, temperature))
    return 0xE0 - ((celsius - 18) * 0x10)


def build_ac_payload(command: ClimateCommand) -> list[int]:
    fan_speed = "Auto" if command.mode == "Dry" else command.fan_speed
    mode_nibble = MODE_NIBBLES[command.mode]

    control_byte = build_temperature_nibble(command.mode, command.temperature) | mode_nibble
    if not command.power_on:
        control_byte += 0x08
    control_byte_inverted = 0xFF - control_byte

    horizontal_byte = HORIZONTAL_AIRFLOW[command.airflow_horizontal]
    vertical_modifier, shifts_horizontal = VERTICAL_AIRFLOW[command.airflow_vertical]
    if shifts_horizontal and command.airflow_horizontal == "All":
        horizontal_byte &= ~0x02
    horizontal_byte_inverted = 0xFF - horizontal_byte

    fan_byte = (FAN_NIBBLES[fan_speed] + vertical_modifier) & 0xFF
    fan_byte_inverted = 0xFF - fan_byte

    return MHI_HEADER + [
        horizontal_byte,
        horizontal_byte_inverted,
        fan_byte,
        fan_byte_inverted,
        control_byte,
        control_byte_inverted,
    ]


async def send_payload(payload: list[int]) -> None:
    url = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/transmit"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                await response.read()
    except aiohttp.ClientError as error:
        print(f"Failed to reach bridge at {url}: {error}")
        raise


app = FastAPI(title="Mitsubishi Heavy AC Bridge")


@app.post("/command", status_code=status.HTTP_200_OK)
async def send_command(command: ClimateCommand) -> dict[str, list[int]]:
    payload = build_ac_payload(command)
    await send_payload(payload)
    return {"payload": payload}


@app.get("/on", status_code=status.HTTP_200_OK)
async def turn_on() -> dict[str, list[int]]:
    payload = build_ac_payload(ClimateCommand(power_on=True))
    await send_payload(payload)
    return {"payload": payload}


@app.get("/off", status_code=status.HTTP_200_OK)
async def turn_off() -> dict[str, list[int]]:
    payload = build_ac_payload(ClimateCommand(power_on=False))
    await send_payload(payload)
    return {"payload": payload}


if __name__ == "__main__":
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
