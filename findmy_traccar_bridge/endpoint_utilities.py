from abc import ABC, abstractmethod
import hashlib

from .db_handling import LocationServer, Location
import requests

from loguru import logger

# Abstract base class for endpoint pushers (e.g. classes that push location data to endpoints) - child classes must be implemented for the distinct endpoints (nextcloud, traccar, ...)
class LocationPusher(ABC):
    """
    Abstract base class for pushing location data to endpoints.

    Child classes must implement the `push_location` method for a specific endpoint
    (e.g., Traccar, Nextcloud, etc.).

    Attributes:
        endpoint_url (str): The URL of the endpoint to push locations to.
        key_id (int): The unique identifier of the device/key whose locations will be pushed.
        location_storage (LocationServer): Storage backend to fetch pending locations from.
        endpoint_id (int): Computed unique integer ID for the endpoint (0-999,999).
    """
    def __init__(self, endpoint_url: str, key_id: int, location_storage: LocationServer):
        """
        Initialize a LocationPusher.

        Args:
            endpoint_url: The URL of the endpoint.
            key_id: The device/key identifier.
            location_storage: The LocationServer instance used to fetch pending locations.
        """
        self.endpoint_url = endpoint_url
        self.key_id = key_id
        self.location_storage = location_storage

        # compute unique id between 0-999,999 based on endpoint url
        hashed = hashlib.sha256(endpoint_url.encode()).hexdigest()
        self.endpoint_id = int(hashed[:16], 16) % 1_000_000

    @abstractmethod
    def push_location(self, location: Location) -> bool:
        """
        Push a single location to the endpoint.

        Must be implemented by child classes.

        Args:
            location: The Location object to push.

        Returns:
            True if the location was successfully pushed, False otherwise.
        """
        pass

    def push_pending_locations(self) -> None:
        """
        Push all locations that have not yet been pushed to this endpoint.

        Fetches pending locations from the LocationServer and calls `push_location` on each.
        If successful, marks the location as pushed in the database.
        """

        pending_locations = self.location_storage.get_pending_locations(self.key_id, self.endpoint_id)
        logger.debug(f"Found {len(pending_locations)} pending locations for key {self.key_id} to push")

        for location in pending_locations:
            if self.push_location(location):
                self.location_storage.mark_as_pushed(self.key_id, self.endpoint_id, location.timestamp)

        logger.debug(f"Finished pushing attempts for key ID '{self.key_id}' and endpoint '{self.endpoint_url}'")


class TraccarLocationPusher(LocationPusher):
    """
    Concrete implementation of LocationPusher for Traccar endpoints.

    Attributes:
        Inherits all attributes from LocationPusher.
    """

    def __init__(self, endpoint_url: str, key_id: str, location_storage: LocationServer):
        """
        Initialize a TraccarLocationPusher.

        Args:
            endpoint_url: The Traccar server URL.
            key_id: The device/key identifier.
            location_storage: The LocationServer instance used to fetch pending locations.
        """

        super().__init__(endpoint_url, key_id, location_storage)

        logger.info(f"Succesfully created TraccarLocationPusher for endpoint '{endpoint_url}' and keyID '{key_id}'")


    def push_location(self, location: Location) -> bool:
        """
        Push a single location to a Traccar server. Overrides parent method.

        Args:
            location: The Location object to push.

        Returns:
            True if the location was successfully pushed, False otherwise.
        """

        # create dictionay from Location that can be transformed into the required HTTP query
        payload = {
            "id": location.key_id, 
            "timestamp": location.timestamp,
            "lat": location.lat,
            "lon": location.lon
        }

        try:
            # send request to traccar server
            resp = requests.post("https://" + self.endpoint_url, data=payload)
            resp.raise_for_status()  # will raise an exception if status is 4xx or 5xx
            logger.debug(f"Pushed location {payload}, key ID {self.key_id}, successfully to {self.endpoint_url}")
            return True
        except requests.RequestException as e:
            logger.warning(f"Failed to push location {payload}, key ID {self.key_id}, to {self.endpoint_url}: {e}")
            return False