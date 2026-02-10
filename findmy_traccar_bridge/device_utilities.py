from pathlib import Path
from typing import List, Union, Dict
import os
import time
import datetime
from pathlib import Path

from findmy import FindMyAccessory, KeyPair
from findmy.reports import (
    AppleAccount,
    LoginState,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
)
from findmy.reports.anisette import LocalAnisetteProvider
from loguru import logger

from .db_handling import MetaDataServer

class AppleAccountManager:
    def __init__(self, accountPath: Path, anisetteLibsPath: Path, metaDataServer: MetaDataServer):
        self.accountPath = accountPath
        self.anisetteLibsPath = anisetteLibsPath
        self.appleAccount = None

        self.metaDataServer = metaDataServer

        self.pollingInterval = int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60)) # defaults to 60 * 60 seconds = 60 min

    def load_account(self):
        
        firstAttempt = True
        while not self.accountPath.is_file():
            
            if firstAttempt:
                logger.info(
                "Login token file not found at '{}'. You must first generate it interactively via "
                "`docker compose exec bridge .venv/bin/findmy-traccar-bridge-init`",
                str(self.accountPath),
                )

                firstAttempt = False

            time.sleep(1)
        
        # load account
        self.appleAccount = AppleAccount.from_json(self.accountPath, anisetteLibsPath=self.anisetteLibsPath)
        
        logger.info(
            "Successfully loaded Apple account token with uid {}...",
            self.appleAccount._asyncacc._uid[:4]
        )
    
    def blockUntilNextPoll(self):
        lastApiPollTime = self.metaDataServer.getMetaData(name = 'last_api_poll_time')
        timeSinceLastPoll = int(datetime.datetime.now().timestamp()) - lastApiPollTime #time in seconds since last poll

        while self.pollingInterval > timeSinceLastPoll:
            time.sleep(1) # sleep for 10 seconds so that SIGTERM stops the process
        
        return

    def executeApiPoll(self, haystackKeys: List[KeyPair], findmyAccessories: List[FindMyAccessory]) -> Dict[Union[KeyPair, FindMyAccessory], list]:
        try:
            result = self.appleAccount.fetch_location_history([*haystackKeys, *findmyAccessories])
            logger.info(
                "AppleAccountManager.executeApiPoll: API Polled successfully. Next Poll in {}s ({} UTC).",
                self.pollingInterval,
                (datetime.datetime.now() + datetime.timedelta(seconds=self.pollingInterval)).isoformat(timespec="seconds"),
            )
        except Exception as e:
            # The api call could encounter any number of issues. For now we just catch them indiscriminantly.
            # Over time, specific errors could be singled out if custom handling makes sense for them.
            logger.error(f"Unhandled exeception while polling FindMy API: {e}")
            return None
        finally:
            self.metaDataServer.setMetaData(name = 'last_api_poll_time', value = str(int(datetime.datetime.now().timestamp())))

class DeviceManager:
    """
    Responsible for loading and managing Haystack keys and FindMy accessories.
    """

    def __init__(self): 

        # haystackKeys_env: str = "BRIDGE_PRIVATE_KEYS",
        # plistPath_env: str = "BRIDGE_plistPath",
    
        self.defaultPlistDir = "/bridge/plists"
        self.haystackKeys: List[KeyPair] = []
        self.findmyKeys: List[FindMyAccessory] = []

    def loadDevices(self) -> None:
        self.haystackKeys = self.loadHaystackKeys()
        self.findmyKeys = self.loadFindmyKeys()

        totalNumDevices = self.getNumHaystacks() + self.getNumFindmys()
        if (totalNumDevices) == 0:
            raise ValueError(
                "No tracking devices configured. Either set BRIDGE_PRIVATE_KEYS environment variable "
                "or mount a directory with .plist files to /bridge/plists"
            )
        else:
            logger.info(
                "Loaded {} key(s) in total",
                totalNumDevices,
            )

    def getHaystackKeys(self) -> List[KeyPair]:
        return self.haystackKeys
    def getFindmyAsseccories(self) -> List[FindMyAccessory]:
        return self.findmyKeys

    def getNumHaystacks(self) -> int:
        return len(self.haystackKeys)
    
    def getNumFindmys(self) -> int:
        return len(self.findmyKeys)
    
    def loadHaystackKeys(self) -> List[KeyPair]:
        """
        Load KeyPairs from the environment variable.
        """

        # load keystring (evenatually several keys comma separated)
        keysStr = os.environ.get("BRIDGE_PRIVATE_KEYS", "")
        keysList = [k for k in keysStr.split(",") if k]

        #generate haystack KeyPairs
        haystackKeys = list()

        for key in keysList:
            haystackKeys.append(KeyPair.from_b64(key))

        return haystackKeys

    def loadFindmyKeys(self) -> List[FindMyAccessory]:
        """
        Load FindMyAccessory objects from .plist files.
        """

        #this function is mainly copied from the commit from Felix Bouleau

        #fetch directory
        plistPath = Path(os.environ.get("BRIDGE_plistPath", "/bridge/plists"))

        if not plistPath.exists():
            logger.info("Plist directory does not exist: {}. No FindMy Accessories loaded", plistPath)
            return []

        if not plistPath.is_dir():
            logger.error("Plist path exists but is not a directory: {}", plistPath)
            return []

        # load files from directory
        plist_files = list(plistPath.glob("*.plist"))
        findmyKeys: List[FindMyAccessory] = []

        for plist_path in plist_files:
            try:
                with plist_path.open("rb") as f:
                    findmyKeys.append(FindMyAccessory.from_plist(f))
            except Exception as e:
                logger.error("Failed to load plist file {}: {}", plist_path, str(e))
        
        return findmyKeys

    def generateHaystackId(self, key: KeyPair) -> int:
        return int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000

    def generateFindmyId(self, accessory: FindMyAccessory) -> int:
        return int.from_bytes(accessory.identifier.encode()) % 1_000_000,

    def generateKeyId(self, key: KeyPair | FindMyAccessory) -> int:
        if isinstance(key, FindMyAccessory):
            return self.generateFindmyId(key)
        if isinstance(key, KeyPair):
            return self.generateHaystackId(key)