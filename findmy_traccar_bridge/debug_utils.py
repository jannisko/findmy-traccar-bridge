from typing import Any
import datetime
import pickle
from pathlib import Path

class ReplayableReport:
    """
    Minimal representation of a location report for debugging and replaying.
    """
    def __init__(self, timestamp: int, latitude: float, longitude: float, raw: Any = None):
        self.timestamp = timestamp
        self.latitude = latitude
        self.longitude = longitude
        self.raw = raw  # optional string representation of original report

    @classmethod
    def from_report(cls, report):
        """
        Create ReplayableReport from an original report object returned by fetch_location_history.
        """
        # Convert timestamp to int if it is datetime
        ts = getattr(report, "timestamp", None)
        if isinstance(ts, datetime.datetime):
            ts = int(ts.timestamp())

        return cls(
            timestamp=ts,
            latitude=getattr(report, "latitude", None),
            longitude=getattr(report, "longitude", None),
            raw=str(report),
        )

    def __repr__(self):
        return f"<ReplayableReport ts={self.timestamp} lat={self.latitude} lon={self.longitude}>"

def save_debug_result(result: dict, path: Path, keyfunction):
    """
    Convert API result to replayable reports and save to pickle.
    
    keyfunction: a function that converts KeyPair or FindMyAccessory to a unique integer ID
    """
    replayable_result = {}

    for device, reports in result.items():
        device_id = keyfunction(device)  # convert object to int ID
        replayable_result[device_id] = [ReplayableReport.from_report(r) for r in reports]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(replayable_result, f)

    print(f"Saved replayable debug result to {path}")

def load_debug_result(path: Path) -> dict:
    """
    Load previously saved replayable reports (keys are integers).
    """
    import pickle
    with path.open("rb") as f:
        return pickle.load(f)