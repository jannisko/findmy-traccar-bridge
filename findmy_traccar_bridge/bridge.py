from typing import List, Union, Dict
import os
import sys
from pathlib import Path

from findmy import FindMyAccessory, KeyPair
from loguru import logger

from .debug_utils import save_debug_result, load_debug_result

logger.remove()
logger.add(sys.stderr, level=os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO"))

from .device_utilities import DeviceManager, AppleAccountManager
from .db_handling import LocationServer, MetaDataServer, init_db
from .endpoint_utilities import TraccarLocationPusher

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

    location_storage = LocationServer(session)
    metadata_server = MetaDataServer(session)

    #load haystack keys and findmy assessories
    device_manager = DeviceManager()
    device_manager.load_devices() #throws error if no keys are found

    # load apple account from directory
    apple_account_manager = AppleAccountManager(apple_account_path, anisette_libs_path, metadata_server)
    apple_account_manager.load_login_token()

    # instanciate one traccar pusher for each key
    traccar_location_pushers: List[TraccarLocationPusher] = []

    for key in device_manager.get_haystack_keys():
        traccar_location_pushers.append(TraccarLocationPusher(
                                        endpoint_url = os.environ["BRIDGE_TRACCAR_SERVER"],
                                        key_id = device_manager.generate_haystack_id(key),
                                        location_storage = location_storage
                                    )
                                )
    
    for key in device_manager.get_findmy_accessories():
        traccar_location_pushers.append(TraccarLocationPusher(
                                        endpoint_url = os.environ["BRIDGE_TRACCAR_SERVER"],
                                        key_id = device_manager.generate_findmy_id(key),
                                        location_storage = location_storage
                                    )
                                )

    logger.info("Successfully created {} traccar pusher", len(traccar_location_pushers))

    while True:

        # let  the account manager block the process until the polling intervall is over
        apple_account_manager.block_until_next_poll()

        new_location_dict: Dict[Union[KeyPair, FindMyAccessory], list]= apple_account_manager.execute_api_poll(device_manager.get_haystack_keys(), device_manager.get_findmy_accessories())
        # save_debug_result(new_location_dict, data_folder / "debug_locations.pkl", device_manager.generate_key_id)
        # new_location_dict = load_debug_result(data_folder / "debug_locations.pkl") #TODO REMOVE TO TEST API POLLS

        for key, reports in new_location_dict.items():
            key_id: int = device_manager.generate_key_id(key)
            
            logger.info(
                    "Received {} locations from device:{} from Apples API",
                    len(reports),
                    key_id,
                )

            # add the new locations to the database
            for report in reports:
                location_storage.add_location(key_id,
                                            int(report.timestamp.timestamp()),
                                            report.latitude,
                                            report.longitude)


        # after new locations are added to the database, call the objects that oush locations to endpoints

        for traccar_location_pusher in traccar_location_pushers:
            traccar_location_pusher.push_pending_locations()


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
