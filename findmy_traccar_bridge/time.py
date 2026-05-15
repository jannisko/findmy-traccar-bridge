import time
from datetime import datetime


# wrapper for time functions which can be switched out for testing
class Clock:

    def now(self) -> datetime:
        return datetime.now()

    def sleep(self, seconds: float):
        time.sleep(seconds)
