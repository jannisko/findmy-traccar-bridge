import time
from datetime import datetime


# wrapper for time functions which can be switched out for testing
class Clock:

    @staticmethod
    def now() -> datetime:
        return datetime.now()

    @staticmethod
    def sleep(seconds: float):
        time.sleep(seconds)
