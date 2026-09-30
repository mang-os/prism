import time

from prism.errors import PrismError


class Deadline:
    def __init__(self, milliseconds: float):
        self.started = time.monotonic()
        self.expires = self.started + milliseconds / 1000

    def check(self):
        if time.monotonic() >= self.expires:
            raise PrismError("DEADLINE_EXCEEDED", "Query budget exhausted", 504)

    def remaining_ms(self):
        return max(0.0, (self.expires - time.monotonic()) * 1000)

    def elapsed_ms(self):
        return (time.monotonic() - self.started) * 1000
