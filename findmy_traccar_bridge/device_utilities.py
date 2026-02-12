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
import getpass

from .db_handling import MetaDataServer

class AppleAccountManager:
    """
    Manages authentication and polling of the Apple Find My API.

    Handles login token generation, token loading, API polling rate
    limiting, and execution of location history fetch requests.
    """

    def __init__(self, accountPath: Path, anisetteLibsPath: Path, metaDataServer: MetaDataServer = None):
        """
        Initialize the AppleAccountManager.

        Args:
            accountPath: Path to the stored Apple account login token JSON file.
            anisetteLibsPath: Path to the Anisette libraries required for authentication.
            metaDataServer: Optional MetaDataServer used to persist polling metadata.
        """

        self.accountPath = accountPath
        self.anisetteLibsPath = anisetteLibsPath
        self.appleAccount = None

        self.metaDataServer = metaDataServer

        self.pollingInterval = int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60)) # defaults to 60 * 60 seconds = 60 min

    def generateLoginToken(self):
        """
        Interactively authenticate with Apple and generate a login token.

        Prompts the user for email and password, handles optional 2FA,
        and stores the resulting authenticated session in the data folder.
        """
        logger.debug("Initiating AplleAccount login attempt.")

        email = input("email?  > ")
        password = getpass.getpass("passwd? > ")

        self.appleAccount = AppleAccount(LocalAnisetteProvider(libs_path=self.anisetteLibsPath))
        state = self.appleAccount.login(email, password)

        if state == LoginState.REQUIRE_2FA:
            methods = self.appleAccount.get_2fa_methods()

            for i, method in enumerate(methods):
                if isinstance(method, TrustedDeviceSecondFactorMethod):
                    print(f"{i} - Trusted Device")
                elif isinstance(method, SmsSecondFactorMethod):
                    print(f"{i} - SMS ({method.phone_number})")

            ind = int(input("Method? > "))

            method = methods[ind]
            method.request()
            code = input("Code? > ")

            method.submit(code)

        self.appleAccount.to_json(self.accountPath)

        logger.info("AppleAccount logged in successfully. Login token stored at '{}'",
                    self.accountPath)

        
    def loadLoginToken(self):
        """
        Load an existing Apple login token from the data folder.

        Blocks until the token file exists. Once available,
        the AppleAccount instance is restored from that token file.
        """

        firstAttempt = True
        while not self.accountPath.is_file():
            
            if firstAttempt:
                logger.info(
                "AppleAccount login token file not found at '{}'. You must first generate it interactively via "
                "`docker compose exec bridge .venv/bin/findmy-traccar-bridge-init`",
                str(self.accountPath),
                )

                firstAttempt = False

            time.sleep(1)
        
        # load account
        self.appleAccount = AppleAccount.from_json(self.accountPath)
        
        logger.info(
            "Successfully loaded Apple account token with uid {}...",
            self.appleAccount._asyncacc._uid[:4]
        )
    
    def blockUntilNextPoll(self):
        """
        Block execution until the configured polling interval has elapsed.

        Uses the stored metadata value `last_api_poll_time` to ensure
        that Apple API rate limits are respected.
        """

        lastApiPollTime = int(self.metaDataServer.getMetaData(name = 'last_api_poll_time', default = "0"))
        timeSinceLastPoll = int(datetime.datetime.now().timestamp()) - lastApiPollTime #time in seconds since last poll

        timeToNextPoll = self.pollingInterval - timeSinceLastPoll

        logger.info("Loop will be blocked for {}s (until {} UTC) to avoid Apple API rate limitation violation.",
                    timeToNextPoll,
                    (datetime.datetime.now() + datetime.timedelta(seconds=timeToNextPoll)).isoformat(timespec="seconds"))
        
        while self.pollingInterval > timeSinceLastPoll:
            time.sleep(1) # sleep for 10 seconds so that SIGTERM stops the process
            timeSinceLastPoll = int(datetime.datetime.now().timestamp()) - lastApiPollTime #time in seconds since last poll
        
        logger.debug("Loop will be continued.")
        return

    def executeApiPoll(
        self,
        haystackKeys: List[KeyPair],
        findmyAccessories: List[FindMyAccessory],
    ) -> Dict[Union[KeyPair, FindMyAccessory], list] | None:
        """
        Fetch location history from the Apple Find My API.

        Args:
            haystackKeys: List of Haystack KeyPair objects.
            findmyAccessories: List of FindMyAccessory objects.

        Returns:
            A dictionary mapping each device (KeyPair or FindMyAccessory)
            to its list of location reports. Returns None if the API call fails.

        Notes:
            The timestamp of the poll is stored in metadata regardless of success or failure.
        """
        try:
            result = self.appleAccount.fetch_location_history([*haystackKeys, *findmyAccessories]) #TODO REMOVE TO TEST API POLLS
            # result = dict()
            logger.info(
                "AppleAccountManager.executeApiPoll: API Polled successfully. Next Poll in {}s ({} UTC).",
                self.pollingInterval,
                (datetime.datetime.now() + datetime.timedelta(seconds=self.pollingInterval)).isoformat(timespec="seconds"),
            )
            return result
        except Exception as e:
            # The api call could encounter any number of issues. For now we just catch them indiscriminantly.
            # Over time, specific errors could be singled out if custom handling makes sense for them.
            logger.error(f"Unhandled exeception while polling FindMy API: {e}")
            return None
        finally:
            self.metaDataServer.setMetaData(name = 'last_api_poll_time', value = str(int(datetime.datetime.now().timestamp())))

class DeviceManager:
    """
    Responsible for loading and managing Haystack keys and Find My accessories.

    Devices can be configured either via environment variables
    (for Haystack keys) or via mounted .plist files (for Find My accessories).
    """

    def __init__(self): 
        """
        Initialize the DeviceManager.

        Sets up internal device storage lists.
        """
    
        self.defaultPlistDir = "/bridge/plists"
        self.haystackKeys: List[KeyPair] = []
        self.findmyKeys: List[FindMyAccessory] = []

    def loadDevices(self) -> None:
        """
        Load all configured devices.

        Loads Haystack keys from environment variables and Find My
        accessories from plist files. Raises a ValueError if no
        devices are configured.
        """

        self.haystackKeys = self.loadHaystackKeys()
        self.findmyKeys = self.loadFindmyKeys()

        totalNumDevices = self.getNumHaystacks() + self.getNumFindmys()
        if (totalNumDevices) == 0:
            raise ValueError(
                "No tracking devices configured. Either set BRIDGE_PRIVATE_KEYS environment variable or mount a directory with .plist files to /bridge/plists"
            )
        else:
            logger.info(
                "Loaded {} key(s) in total",
                totalNumDevices,
            )

    def getHaystackKeys(self) -> List[KeyPair]:
        """
        Return all loaded Haystack keys.

        Returns:
            List of KeyPair objects.
        """
        return self.haystackKeys
    
    def getFindmyAsseccories(self) -> List[FindMyAccessory]:
        """
        Return all loaded Find My accessories.

        Returns:
            List of FindMyAccessory objects.
        """

        return self.findmyKeys

    def getNumHaystacks(self) -> int:
        """
        Return the number of loaded Haystack keys.
        """

        return len(self.haystackKeys)
    
    def getNumFindmys(self) -> int:
        """
        Return the number of loaded Find My accessories.
        """

        return len(self.findmyKeys)
    
    def loadHaystackKeys(self) -> List[KeyPair]:
        """
        Load Haystack KeyPairs from the environment variable `BRIDGE_PRIVATE_KEYS`.

        The variable may contain multiple base64-encoded keys separated by commas.

        Returns:
            List of KeyPair objects.
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

        The directory is defined by the `BRIDGE_PLIST_PATH`
        environment variable (default: /bridge/plists).

        Returns:
            List of successfully loaded FindMyAccessory objects.

        Notes:
            Invalid or unreadable plist files are skipped and logged.
        """

        #this function is mainly copied from the commit from Felix Bouleau

        #fetch directory
        plistPath = Path(os.environ.get("BRIDGE_PLIST_PATH", self.defaultPlistDir))

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
        """
        Generate a numeric device ID for a Haystack key.

        Args:
            key: KeyPair instance.

        Returns:
            Integer ID derived from the hashed advertising key.
        """

        return int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000

    def generateFindmyId(self, accessory: FindMyAccessory) -> int:
        """
        Generate a numeric device ID for a Find My accessory.

        Args:
            accessory: FindMyAccessory instance.

        Returns:
            Integer ID derived from the accessory identifier.
        """

        return int.from_bytes(accessory.identifier.encode()) % 1_000_000,

    def generateKeyId(self, key: KeyPair | FindMyAccessory) -> int:
        """
        Generate a numeric device ID for either supported key type.

        Args:
            key: KeyPair or FindMyAccessory instance.

        Returns:
            Integer device ID.

        Raises:
            TypeError: If the provided object type is unsupported.
        """

        if isinstance(key, FindMyAccessory):
            return self.generateFindmyId(key)
        if isinstance(key, KeyPair):
            return self.generateHaystackId(key)