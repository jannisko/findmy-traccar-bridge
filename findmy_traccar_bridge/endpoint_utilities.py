from abc import ABC, abstractmethod
import hashlib

from .db_handling import LocationServer, Location
import requests

from loguru import logger

# Abstract base class for endpoint pushers (e.g. classes that push location data to endpoints) - child classes must be implemented for the distinct endpoints (nextcloud, traccar, ...)
class LocationPusher(ABC):
    """
    Abstract base class for pushing location data to endpoints.

    Child classes must implement the `pushLocation` method for a specific endpoint
    (e.g., Traccar, Nextcloud, etc.).

    Attributes:
        endpointUrl (str): The URL of the endpoint to push locations to.
        keyId (int): The unique identifier of the device/key whose locations will be pushed.
        locationStorage (LocationServer): Storage backend to fetch pending locations from.
        endpointId (int): Computed unique integer ID for the endpoint (0-999,999).
    """
    def __init__(self, endpointUrl: str, keyId: int, locationStorage: LocationServer):
        """
        Initialize a LocationPusher.

        Args:
            endpointUrl: The URL of the endpoint.
            keyId: The device/key identifier.
            locationStorage: The LocationServer instance used to fetch pending locations.
        """
        self.endpointUrl = endpointUrl
        self.keyId = keyId
        self.locationStorage = locationStorage

        # compute unique id between 0-999,999 based on endpoint url
        hashed = hashlib.sha256(endpointUrl.encode()).hexdigest()
        self.endpointId = int(hashed[:16], 16) % 1_000_000

    @abstractmethod
    def pushLocation(self, location: Location) -> bool:
        """
        Push a single location to the endpoint.

        Must be implemented by child classes.

        Args:
            location: The Location object to push.

        Returns:
            True if the location was successfully pushed, False otherwise.
        """
        pass

    def pushPendingLocations(self) -> None:
        """
        Push all locations that have not yet been pushed to this endpoint.

        Fetches pending locations from the LocationServer and calls `pushLocation` on each.
        If successful, marks the location as pushed in the database.
        """

        pending_locations = self.locationStorage.getPendingLocations(self.keyId, self.endpointId)
        logger.debug(f"Found {len(pending_locations)} pending locations for key {self.keyId} to push")

        for location in pending_locations:
            if self.pushLocation(location):
                self.locationStorage.markAsPushed(self.keyId, self.endpointId, location.timestamp)

        logger.debug(f"Finished pushing attempts for key ID '{self.keyId}' and endpoint '{self.endpointUrl}'")


class TraccarLocationPusher(LocationPusher):
    """
    Concrete implementation of LocationPusher for Traccar endpoints.

    Attributes:
        Inherits all attributes from LocationPusher.
    """

    def __init__(self, endpointUrl: str, keyId: str, locationStorage: LocationServer):
        """
        Initialize a TraccarLocationPusher.

        Args:
            endpointUrl: The Traccar server URL.
            keyId: The device/key identifier.
            locationStorage: The LocationServer instance used to fetch pending locations.
        """

        super().__init__(endpointUrl, keyId, locationStorage)

        logger.info(f"Succesfully created TraccarLocationPusher for endpoint '{endpointUrl}' and keyID '{keyId}'")


    def pushLocation(self, location: Location) -> bool:
        """
        Push a single location to a Traccar server. Overrides parent method.

        Args:
            location: The Location object to push.

        Returns:
            True if the location was successfully pushed, False otherwise.
        """

        # create dictionay from Location that can be transformed into the required HTTP query
        payload = {
            "id": location.keyId, 
            "timestamp": location.timestamp,
            "lat": location.lat,
            "lon": location.lon
        }

        try:
            # send request to traccar server
            resp = requests.post("https://" + self.endpointUrl, data=payload)
            resp.raise_for_status()  # will raise an exception if status is 4xx or 5xx
            logger.debug(f"Pushed location {payload}, key ID {self.keyId}, successfully to {self.endpointUrl}")
            return True
        except requests.RequestException as e:
            logger.warning(f"Failed to push location {payload}, key ID {self.keyId}, to {self.endpointUrl}: {e}")
            return False