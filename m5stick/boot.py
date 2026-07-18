from machine import I2C, Pin, PWM
import time
import _thread


PMIC_ADDR = 0x51
POWER_OFF_BUTTON_PIN = 35
LED_PIN = 19

i2c = I2C(0, scl=Pin(22), sda=Pin(21))
led = Pin(LED_PIN, Pin.OUT)


def pmic_write(register, value):
    i2c.writeto_mem(PMIC_ADDR, register, bytes([value]))

def power_on():
    pmic_write(0x00, 0x37)
    pmic_write(0x01, 0x1F)
    pmic_write(0x02, 0x0F)
    pmic_write(0x03, 0x03)
    pmic_write(0x04, 0x20)
    pmic_write(0x05, 0x80)
    led.value(1)
    time.sleep(0.5)
    led.value(0)

def power_off():
    led.value(1)
    time.sleep(1)
    led.value(0)
    pmic_write(0x00, 0x00)
    pmic_write(0x01, 0x00)
    pmic_write(0x02, 0x00)
    pmic_write(0x03, 0x00)
    pmic_write(0x05, 0x00)
    time.sleep(0.5)

def button_watcher():
    button = Pin(POWER_OFF_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    pressed_counter = 0
    while True:
        if button.value() == 0:
            pressed_counter += 1
            if pressed_counter >= 40:
                power_off()
        else:
            pressed_counter = 0
        time.sleep(0.05)

power_on()
_thread.start_new_thread(button_watcher, ())
