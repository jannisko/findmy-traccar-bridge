import datetime
from typing import List, Union, Dict
import json
import os
import sys
import time
from pathlib import Path
from typing import TypedDict

import requests
from findmy import FindMyAccessory, KeyPair
from findmy.reports import (
    AppleAccount,
    LoginState,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
)
from findmy.reports.anisette import LocalAnisetteProvider
from loguru import logger

from .debug_utils import ReplayableReport, save_debug_result, load_debug_result

logger.remove()
logger.add(sys.stderr, level=os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO"))

from .device_utilities import DeviceManager, AppleAccountManager
from .db_handling import LocationServer, MetaDataServer, initDb
from .endpoint_utilities import TraccarLocationPusher

POLLING_INTERVAL = int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60))

data_folder = Path("./data/")
data_folder.mkdir(exist_ok=True)
db_path = data_folder / "db.db"
apple_account_path = data_folder / "account.json"
anisette_libs_path = data_folder / "ani_libs.bin"

def bridge() -> None:
    """
    Main loop fetching location data from the Apple API and forwarding it to a Traccar server.

    Callable via the binary `.venv/bin/findmy-traccar-bridge`
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

    logger.info("Instanciated {} traccar pusher", len(traccarLocationPushers))

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
    One-time interactive login procedure to answer 2fa challenge and generate API token.

    Callable via the binary `.venv/bin/findmy-traccar-bridge-init`
    """

    appleAccountManager = AppleAccountManager(apple_account_path, anisette_libs_path)
    appleAccountManager.generateLoginToken()
