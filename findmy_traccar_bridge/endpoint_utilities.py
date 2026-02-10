from abc import ABC, abstractmethod
import hashlib

from .db_handling import LocationStorage, Location
import requests

from loguru import logger

# Abstract base class for endpoint pushers (e.g. classes that push location data to endpoints) - child classes must be implemented for the distinct endpoints (nextcloud, traccar, ...)
class LocationPusher(ABC):
    def __init__(self, endpointUrl: str, keyId: int, locationStorage: LocationStorage):
        self.endpointUrl = endpointUrl
        self.keyId = keyId
        self.locationStorage = locationStorage

        # compute unique id between 0-999,999 based on endpoint url
        hashed = hashlib.sha256(endpointUrl.encode()).hexdigest()
        self.endpointId = int(hashed[:16], 16) % 1_000_000

    @abstractmethod
    def pushLocation(self, location: Location) -> bool:
        """
        Push a single location to the endpoint. Must return True on success, False otherwise.
        """
        pass

    def pushPendingLocations(self) -> None:
        """
        Push all locations that have not been pushed yet to the endpoint.
        """
        pending_locations = self.locationStorage.getPendingLocations(self.keyId, self.endpointId)
        logger.debug(f"Found {len(pending_locations)} pending locations for key {self.keyId} to push")

        for location in pending_locations:
            if self.pushLocation(location):
                self.locationStorage.markAsPushed(self.keyId, self.endpointId, location.timestamp)

        logger.debug(f"Finished pushing attempts for key ID '{self.keyId}' to endpoint '{self.endpointUrl}'")


class TraccarLocationPusher(LocationPusher):

    def __init__(self, endpointUrl: str, keyId: str, locationStorage: LocationStorage):
        super().__init__(endpointUrl, keyId, locationStorage)

        logger.info(f"Created TraccarLocationPusher for endpoint '{endpointUrl}'")


    #override parent class method
    def pushLocation(self, location: Location) -> bool:

        # create dictionay from Location that can be transformed into the required HTTP query
        payload = {
            "id": location.keyId,     # or "id": location.keyId if the API expects it
            "timestamp": location.timestamp,
            "lat": location.lat,
            "lon": location.lon
        }

        try:
            # send request to traccar server
            resp = requests.post(self.endpointUrl, json=payload, timeout=5)
            resp.raise_for_status()  # will raise an exception if status is 4xx or 5xx
            logger.debug(f"TraccarLocationPusher.pushLocation: Pushed location {payload}, key ID {self.keyId}, successfully to {self.endpointUrl}")
            return True
        except requests.RequestException as e:
            logger.warning(f"Failed to push location {payload}, key ID {self.keyId}, to {self.endpointUrl}: {e}")
            return False