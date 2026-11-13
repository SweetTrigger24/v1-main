"""Phần cứng chỉ do monitor quản lý, không khởi tạo GPIO trong dashboard."""
import math
import threading
import time
from contextlib import ExitStack

import common as config


class PiHardware:
    def __init__(self):
        import RPi.GPIO as GPIO
        import adafruit_dht
        import board
        from gpiozero import LED, Button
        self.GPIO = GPIO
        self._resources = ExitStack()
        self.state = "XANH"
        self.mute_until = 0
        try:
            self.dht = adafruit_dht.DHT22(getattr(board, f"D{config.PIN_DHT}"), use_pulseio=False)
            self._resources.callback(self.dht.exit)
            self.leds = []
            for pin in (config.PIN_LED_GREEN, config.PIN_LED_YELLOW, config.PIN_LED_RED):
                led = LED(pin)
                self._resources.callback(led.close)
                self.leds.append(led)
            self.button = Button(config.PIN_BUTTON, pull_up=True, bounce_time=.1)
            self._resources.callback(self.button.close)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(config.PIN_BUZZER, GPIO.OUT)
            self._resources.callback(GPIO.cleanup, config.PIN_BUZZER)
            self.pwm = GPIO.PWM(config.PIN_BUZZER, config.BUZZER_FREQ)
            self._resources.callback(self.pwm.stop)
            self.pwm.start(0)
            self.stop = threading.Event()
            self.thread = threading.Thread(target=self._buzzer_worker, daemon=True)
            self.thread.start()
            self._resources.callback(self._stop_buzzer)
            self.button.when_pressed = self.mute
        except BaseException:
            self.close()
            raise

    def mute(self):
        self.mute_until = time.monotonic() + config.MUTE_SEC

    def _buzzer_worker(self):
        # Một thread duy nhất, tự bật lại sau 5 phút dù cảm biến chưa đến lần đọc mới.
        on = False
        while not self.stop.is_set():
            on = not on
            sound = on and self.state == "DO" and time.monotonic() >= self.mute_until
            self.pwm.ChangeDutyCycle(config.BUZZER_DUTY if sound else 0)
            self.stop.wait(.2)
        self.pwm.ChangeDutyCycle(0)

    def set_outputs(self, state):
        self.state = state
        for led, active in zip(self.leds, (state == "XANH", state in {"VANG", "ERROR"}, state == "DO")):
            led.value = active

    def read(self, stop):
        for _ in range(5):
            if stop.is_set():
                return None
            try:
                t, h = self.dht.temperature, self.dht.humidity
                if (t is not None and h is not None and math.isfinite(t) and math.isfinite(h)
                        and -40 <= t <= 80 and 0 <= h <= 100):
                    return float(t), float(h)
            except RuntimeError:
                pass
            if stop.wait(2):
                return None
        return None

    def _stop_buzzer(self):
        self.stop.set()
        self.thread.join(timeout=2)

    def close(self):
        self._resources.close()


class DemoHardware:
    def read(self, stop):
        minute = config.now_local().hour * 60 + config.now_local().minute
        phase = minute / 1440 * math.tau
        return 27 + 4 * math.sin(phase), 60 - 15 * math.sin(phase)

    def set_outputs(self, state):
        pass

    def close(self):
        pass
