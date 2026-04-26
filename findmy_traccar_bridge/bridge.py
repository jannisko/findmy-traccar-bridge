# Standard library
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Union

# Third-party
from findmy import FindMyAccessory, KeyPair, LocationReport
from loguru import logger

from .db_handling import LocationService, MetaDataService, init_db

# Local imports
from .device_utilities import AppleAccountManager, DeviceManager
from .endpoint_utilities import TraccarLocationPusher

logger.remove()
logger.add(sys.stderr, level=os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO"))


data_folder = Path("./data/")
data_folder.mkdir(exist_ok=True)
db_path = data_folder / "db.db"
apple_account_path = data_folder / "account.json"
anisette_libs_path = data_folder / "ani_libs.bin"


def bridge() -> None:
    """
    Main bridge loop.

    Fetches location data from Apple FindMy API and forwards it to the configured
    Traccar server for all devices (Haystack keys and FindMy accessories).
    Uses a database to store locations and track which locations have been pushed.

    Steps performed:
        1. Initialize database session.
        2. Load Haystack keys and FindMy accessories.
        3. Load Apple account login token.
        4. Instantiate Traccar location pushers for each device.
        5. Enter infinite loop:
            a. Wait until the polling interval has elapsed.
            b. Fetch latest location reports from Apple API.
            c. Store new locations in the database.
            d. Push pending locations to Traccar endpoints.

    Notes:
        - Designed to be called via the CLI binary:
            `.venv/bin/findmy-traccar-bridge`
    """

    session = init_db(db_path)

    location_storage = LocationService(session)
    metadata_server = MetaDataService(session)

    # load haystack keys and findmy assessories
    device_manager = DeviceManager()
    device_manager.load_devices()  # throws error if no keys are found

    # load apple account from directory
    apple_account_manager = AppleAccountManager(
        apple_account_path, anisette_libs_path, metadata_server
    )
    apple_account_manager.load_login_token()

    # instanciate one traccar pusher for each key
    traccar_location_pushers: List[TraccarLocationPusher] = []

    sources = [
        (device_manager.get_haystack_keys(), device_manager.generate_haystack_id),
        (device_manager.get_findmy_accessories(), device_manager.generate_findmy_id),
    ]

    traccar_location_pushers.extend(
        TraccarLocationPusher(
            endpoint_url=os.environ.get("BRIDGE_TRACCAR_SERVER", ""),
            key_id=key_id_function(key),
            location_storage=location_storage,
            pushing_interval=10,  # secodns
        )
        for keys, key_id_function in sources
        for key in keys
    )

    logger.info("Successfully created {} traccar pusher", len(traccar_location_pushers))

    #################################
    #### main loooop ################
    #################################

    while True:
        # check if API can be polled savely and poll if yes

        if apple_account_manager.safe_to_poll():
            result = apple_account_manager.execute_api_poll(
                device_manager.get_haystack_keys(),
                device_manager.get_findmy_accessories(),
            )

            if result is None:
                # there was an error with the API request, which has been logged already
                break

            new_location_dict = result

            for key, reports in new_location_dict.items():
                key_id: int = device_manager.generate_key_id(key)

                logger.info(
                    "Received {} locations from device with ID {} from Apples API",
                    len(reports),
                    key_id,
                )

                # add the new locations to the database
                for report in reports:
                    location_storage.add_location(
                        key_id,
                        int(report.timestamp.timestamp()),
                        report.latitude,
                        report.longitude,
                    )

        # after new locations are added to the database, call the objects that oush locations to endpoints

        for traccar_location_pusher in traccar_location_pushers:
            if traccar_location_pusher.ready_to_push():
                traccar_location_pusher.push_pending_locations()

        time.sleep(1)


def init() -> None:
    """
    One-time interactive Apple login procedure.

    Prompts user for email/password and handles two-factor authentication if required.
    Stores the resulting login token for future automated polling.

    Notes:
        - Callable via the CLI binary:
            `.venv/bin/findmy-traccar-bridge-init`
        - Creates `account.json` in the data folder for future use.
    """

    apple_account_manager = AppleAccountManager(apple_account_path, anisette_libs_path)
    apple_account_manager.generate_login_token()
