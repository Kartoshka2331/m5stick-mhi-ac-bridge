import gc
import json
import socket
import time

import esp32
import machine
import micropython
import network


WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

RX_PIN_NUMBER = 33
TX_PIN_NUMBER = 26
BUTTON_PIN_NUMBER = 37

BATTERY_ADC_PIN = 38
BATTERY_CALIBRATION_FACTOR = 1.08
BATTERY_MIN_VOLTAGE = 3.0
BATTERY_MAX_VOLTAGE = 4.2

MHI_HEADER_MARK = 3200
MHI_HEADER_SPACE = 1600
MHI_BIT_MARK = 400
MHI_ONE_SPACE = 1200
MHI_ZERO_SPACE = 400
MHI_TOLERANCE = 250
MHI_MESSAGE_LENGTH = 11

COMMAND_ON = [82, 174, 195, 38, 217, 251, 4, 135, 120, 230, 25]
COMMAND_OFF = [82, 174, 195, 38, 217, 251, 4, 135, 120, 238, 17]

micropython.alloc_emergency_exception_buf(100)


class BatteryMonitor:
    def __init__(self):
        self.adc = machine.ADC(machine.Pin(BATTERY_ADC_PIN))
        self.adc.atten(machine.ADC.ATTN_11DB)
        self.adc.width(machine.ADC.WIDTH_12BIT)

    def get_status(self):
        raw_value = self.adc.read()
        voltage = (raw_value / 4095.0) * 3.3 * 2.0 * BATTERY_CALIBRATION_FACTOR
        percentage = (voltage - BATTERY_MIN_VOLTAGE) / (BATTERY_MAX_VOLTAGE - BATTERY_MIN_VOLTAGE) * 100.0
        percentage = max(0.0, min(100.0, percentage))
        return {"voltage": round(voltage, 2), "percentage": round(percentage, 1)}


class Button:
    def __init__(self):
        self.pin = machine.Pin(BUTTON_PIN_NUMBER, machine.Pin.IN)
        self.last_state = 1
        self.last_trigger_time = 0
        self.debounce_cooldown = 300

    def is_pressed(self):
        current_state = self.pin.value()
        current_time = time.ticks_ms()

        if current_state == 0 and self.last_state == 1:
            if time.ticks_diff(current_time, self.last_trigger_time) > self.debounce_cooldown:
                self.last_trigger_time = current_time
                self.last_state = 0
                return True

        if current_state == 1:
            self.last_state = 1

        return False


class InfraredService:
    def __init__(self):
        self.tx_channel = esp32.RMT(0, pin=machine.Pin(TX_PIN_NUMBER, machine.Pin.OUT, value=0), clock_div=80, tx_carrier=(38000, 50, 1))
        self.rx_pin = machine.Pin(RX_PIN_NUMBER, machine.Pin.IN)
        self.rx_timestamps = []
        self.last_edge_time = 0
        self.max_pulses = 1000
        self._start_listening()
        print(f"IR: RX listening on G{RX_PIN_NUMBER}, TX ready on G{TX_PIN_NUMBER}")

    def _start_listening(self):
        self.rx_timestamps = []
        self.rx_pin.irq(trigger=machine.Pin.IRQ_FALLING | machine.Pin.IRQ_RISING, handler=self._handle_interrupt)

    def _stop_listening(self):
        self.rx_pin.irq(handler=None)

    def _handle_interrupt(self, pin):
        if len(self.rx_timestamps) < self.max_pulses:
            self.rx_timestamps.append(time.ticks_us())
            self.last_edge_time = time.ticks_ms()

    def _is_match(self, duration, target):
        return target - MHI_TOLERANCE <= duration <= target + MHI_TOLERANCE

    def _decode_mhi(self, durations):
        if len(durations) < 20:
            return None

        if not (self._is_match(durations[0], MHI_HEADER_MARK) and self._is_match(durations[1], MHI_HEADER_SPACE)):
            return None

        bits = []
        for index in range(2, len(durations) - 1, 2):
            mark = durations[index]
            space = durations[index + 1]

            if not self._is_match(mark, MHI_BIT_MARK):
                break

            if self._is_match(space, MHI_ONE_SPACE):
                bits.append(1)
            elif self._is_match(space, MHI_ZERO_SPACE):
                bits.append(0)
            else:
                break

        decoded_bytes = []
        current_byte = 0
        bit_index = 0

        for bit in bits:
            if bit:
                current_byte |= (1 << bit_index)
            bit_index += 1
            if bit_index == 8:
                decoded_bytes.append(current_byte)
                current_byte = 0
                bit_index = 0

        return decoded_bytes

    def _encode_mhi(self, byte_list):
        pulses = [MHI_HEADER_MARK, MHI_HEADER_SPACE]

        for byte_value in byte_list:
            for bit_index in range(8):
                bit = (byte_value >> bit_index) & 1
                pulses.append(MHI_BIT_MARK)
                pulses.append(MHI_ONE_SPACE if bit else MHI_ZERO_SPACE)

        pulses.append(MHI_BIT_MARK)
        pulses.append(0)

        return pulses

    def process_incoming_signals(self):
        if not self.rx_timestamps:
            return

        if time.ticks_diff(time.ticks_ms(), self.last_edge_time) <= 15:
            return

        self._stop_listening()
        raw_timestamps = self.rx_timestamps[:]
        self._start_listening()

        if len(raw_timestamps) > 10:
            durations = [time.ticks_diff(raw_timestamps[index], raw_timestamps[index - 1]) for index in range(1, len(raw_timestamps))]
            decoded_bytes = self._decode_mhi(durations)

            print("\n--- SIGNAL RECEIVED ---")
            if decoded_bytes and len(decoded_bytes) == MHI_MESSAGE_LENGTH:
                hex_string = ", ".join([f"0x{byte_value:02X}" for byte_value in decoded_bytes])
                print(f"DECODED BYTES: [{hex_string}]")
            print("-----------------------\n")

        gc.collect()

    def transmit_signal(self, byte_data):
        if not byte_data:
            return

        print(f"IR: Encoding and transmitting {len(byte_data)} bytes")
        pulses = self._encode_mhi(byte_data)

        self._stop_listening()
        self.tx_channel.write_pulses(tuple(pulses), 1)
        time.sleep_ms(100)
        self._start_listening()


class HttpServer:
    def __init__(self, ir_service, battery_service):
        self.ir_service = ir_service
        self.battery_service = battery_service

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", 80))
        self.socket.listen(5)
        self.socket.setblocking(False)

    def _build_response(self, body, content_type):
        return f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n{body}"

    def handle_client(self):
        try:
            connection, _ = self.socket.accept()
        except OSError:
            return

        try:
            connection.settimeout(0.5)
            request = connection.recv(4096).decode()
            body = "Endpoints: POST /transmit (JSON list), GET /battery"
            content_type = "text/plain"

            if "POST /transmit" in request:
                body = self._handle_transmit(request)
            elif "GET /battery" in request:
                body = json.dumps(self.battery_service.get_status())
                content_type = "application/json"

            connection.send(self._build_response(body, content_type).encode())
        except OSError:
            pass
        except Exception as error:
            print(f"Web error: {error}")
        finally:
            connection.close()

    def _handle_transmit(self, request):
        parts = request.split("\r\n\r\n")
        if len(parts) <= 1:
            return "Error: No body found"

        try:
            byte_data = json.loads(parts[1])
        except ValueError:
            return "Error: Invalid JSON"

        if not isinstance(byte_data, list):
            return "Error: Body must be a JSON list of bytes"

        self.ir_service.transmit_signal(byte_data)
        return "Bytes sent"


class NetworkManager:
    def __init__(self):
        self.interface = network.WLAN(network.STA_IF)
        self.interface.active(True)

        print(f"Connecting to WiFi '{WIFI_SSID}'")
        self.interface.connect(WIFI_SSID, WIFI_PASSWORD)

        while not self.interface.isconnected():
            time.sleep(0.5)

        ip_address = self.interface.ifconfig()[0]
        print(f"Connected successfully. IP address: {ip_address}")


def main():
    NetworkManager()

    ir_service = InfraredService()
    battery_service = BatteryMonitor()
    button = Button()
    web_server = HttpServer(ir_service, battery_service)

    is_on = False
    print("SYSTEM: Ready")

    while True:
        ir_service.process_incoming_signals()
        web_server.handle_client()

        if button.is_pressed():
            is_on = not is_on
            command = COMMAND_ON if is_on else COMMAND_OFF
            print(f"MANUAL TRIGGER: Sending {'ON' if is_on else 'OFF'}")
            ir_service.transmit_signal(command)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Critical: {error}")
        machine.reset()
