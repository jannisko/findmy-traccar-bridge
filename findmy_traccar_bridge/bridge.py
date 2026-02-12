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
from .db_handling import LocationServer, MetaDataServer, initDb
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

    session = initDb(db_path)

    locationStorage = LocationServer(session)
    metaDataServer = MetaDataServer(session)

    #load haystack keys and findmy assessories
    deviceManager = DeviceManager()
    deviceManager.loadDevices() #throws error if no keys are found

    # load apple account from directory
    appleAccountManager = AppleAccountManager(apple_account_path, anisette_libs_path, metaDataServer)
    appleAccountManager.loadLoginToken()

    # instanciate one traccar pusher for each key
    traccarLocationPushers: List[TraccarLocationPusher] = []

    for key in deviceManager.getHaystackKeys():
        traccarLocationPushers.append(TraccarLocationPusher(
                                        endpointUrl = os.environ["BRIDGE_TRACCAR_SERVER"],
                                        keyId = deviceManager.generateHaystackId(key),
                                        locationStorage = locationStorage
                                    )
                                )
    
    for key in deviceManager.getFindmyAsseccories():
        traccarLocationPushers.append(TraccarLocationPusher(
                                        endpointUrl = os.environ["BRIDGE_TRACCAR_SERVER"],
                                        keyId = deviceManager.generateFindmyId(key),
                                        locationStorage = locationStorage
                                    )
                                )

    logger.info("Successfully created {} traccar pusher", len(traccarLocationPushers))

    while True:

        # let  the account manager block the process until the polling intervall is over
        appleAccountManager.blockUntilNextPoll()

        newLocationDict: Dict[Union[KeyPair, FindMyAccessory], list]= appleAccountManager.executeApiPoll(deviceManager.getHaystackKeys(), deviceManager.getFindmyAsseccories())
        # save_debug_result(newLocationDict, data_folder / "debug_locations.pkl", deviceManager.generateKeyId)
        newLocationDict = load_debug_result(data_folder / "debug_locations.pkl") #TODO REMOVE TO TEST API POLLS

        for key, reports in newLocationDict.items():
            keyId: int = key # deviceManager.generateKeyId(key)
            
            logger.info(
                    "Received {} locations from device:{} from Apples API",
                    len(reports),
                    keyId,
                )

            # add the new locations to the database
            for report in reports:
                locationStorage.addLocation(keyId,
                                            report.timestamp,
                                            report.latitude,
                                            report.longitude)


        # after new locations are added to the database, call the objects that oush locations to endpoints

        for traccarLocationPusher in traccarLocationPushers:
            traccarLocationPusher.pushPendingLocations()


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

    appleAccountManager = AppleAccountManager(apple_account_path, anisette_libs_path)
    appleAccountManager.generateLoginToken()
