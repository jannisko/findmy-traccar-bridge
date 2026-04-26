import datetime
import hashlib
from abc import ABC, abstractmethod

import requests
from loguru import logger

from .db_handling import Location, LocationService


# Abstract base class for endpoint pushers (e.g. classes that push location data to endpoints) - child classes must be implemented for the distinct endpoints (nextcloud, traccar, ...)
class LocationPusher(ABC):
    """
    Abstract base class for pushing location data to endpoints.

    Child classes must implement the `push_location` method for a specific endpoint
    (e.g., Traccar, Nextcloud, etc.).

    Attributes:
        endpoint_url (str): The URL of the endpoint to push locations to.
        key_id (int): The unique identifier of the device/key whose locations will be pushed.
        location_storage (LocationService): Storage backend to fetch pending locations from.
        endpoint_id (int): Computed unique integer ID for the endpoint (0-999,999).
    """

    def __init__(
        self,
        endpoint_url: str,
        key_id: int,
        location_storage: LocationService,
        pushing_interval: int,
    ):
        """
        Initialize a LocationPusher.

        Args:
            endpoint_url: The URL of the endpoint.
            key_id: The device/key identifier.
            location_storage: The LocationService instance used to fetch pending locations.
            pushing_interval: interval between pushing attempts in seconds
        """
        self.endpoint_url = endpoint_url
        self.key_id = key_id
        self.location_storage = location_storage
        self.pushing_interval = pushing_interval
        self.last_push_time = 0
        self.healthy = True

        # compute unique id between 0-999,999 based on endpoint url
        hashed = hashlib.sha256(endpoint_url.encode()).hexdigest()
        self.endpoint_id = int(hashed[:16], 16) % 1_000_000

        if self.endpoint_url == "":
            logger.error(
                "Location pusher requires a valid url specified by BRIDGE_TRACCAR_SERVER. Location Pusher will remain inactiive."
            )
            self.healthy = False

        if not self.endpoint_url.startswith("http"):
            logger.error(
                "BRIDGE_TRACCAR_SERVER = '{}' is invalid. It must be an URL starting with 'http' or 'https'. Location Pusher will remain inactiive.",
                self.endpoint_url,
            )
            self.healthy = False

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

    def ready_to_push(self) -> bool:
        """
        checks wheather the pushing interval has allready ellapsed since the last push and returns true if so, false otherwise

        """
        time_since_last_poll = (
            int(datetime.datetime.now().timestamp()) - self.last_push_time
        )  # time in seconds since last push
        return self.pushing_interval < time_since_last_poll

    def push_pending_locations(self) -> None:
        """
        Push all locations that have not yet been pushed to this endpoint.

        Fetches pending locations from the LocationService and calls `push_location` on each.
        If successful, marks the location as pushed in the database.
        """
        if not self.healthy:
            logger.debug("Pusher is unhealthy. No location pushing will be attempted.")
            return

        pending_locations = self.location_storage.get_pending_locations(
            self.key_id, self.endpoint_id
        )
        logger.debug(
            f"Found {len(pending_locations)} pending locations for key {self.key_id} to push"
        )

        for location in pending_locations:
            if self.push_location(location):
                self.location_storage.mark_as_pushed(
                    self.key_id, self.endpoint_id, location.timestamp
                )

        logger.debug(
            f"Finished pushing attempts for key ID '{self.key_id}' and endpoint '{self.endpoint_url}'"
        )

        self.last_push_time = int(datetime.datetime.now().timestamp())


class TraccarLocationPusher(LocationPusher):
    """
    Concrete implementation of LocationPusher for Traccar endpoints.

    Attributes:
        Inherits all attributes from LocationPusher.
    """

    def __init__(
        self,
        endpoint_url: str,
        key_id: int,
        location_storage: LocationService,
        pushing_interval: int,
    ):
        """
        Initialize a TraccarLocationPusher.

        Args:
            endpoint_url: The Traccar server URL.
            key_id: The device/key identifier.
            location_storage: The LocationService instance used to fetch pending locations.
        """

        super().__init__(endpoint_url, key_id, location_storage, pushing_interval)

        logger.info(
            f"Succesfully created TraccarLocationPusher for endpoint '{endpoint_url}' and keyID '{key_id}'"
        )

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
            "lon": location.lon,
        }

        try:
            # send request to traccar server
            resp = requests.post(self.endpoint_url, data=payload)
            resp.raise_for_status()  # will raise an exception if status is 4xx or 5xx
            logger.debug(
                f"Pushed location {payload}, key ID {self.key_id}, successfully to {self.endpoint_url}"
            )
            return True

        except requests.RequestException as e:
            if isinstance(e, requests.Timeout):
                reason = "connection timeout"

            elif isinstance(e, requests.ConnectionError):
                reason = "connection error"

            elif isinstance(e, requests.HTTPError):
                status = e.response.status_code

                if 400 <= status < 500:
                    reason = f"client error {status}"
                elif 500 <= status < 600:
                    reason = f"server error {status}"
                else:
                    reason = f"http error {status}"

            else:
                reason = "unexpected request error"

            logger.warning(
                "Failed to push location {}, key ID {}, to {} because of {}. "
                "Is the key ID already claimed in the traccar UI? "
                "Reupload will be attempted in {} seconds.",
                payload,
                self.key_id,
                self.endpoint_url,
                reason,
                self.pushing_interval,
            )

            return False
