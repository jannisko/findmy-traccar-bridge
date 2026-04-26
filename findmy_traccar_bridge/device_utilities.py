import datetime
import getpass
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, TypeGuard, Union

from findmy import FindMyAccessory, KeyPair, LocationReport
from findmy.reports import (
    AppleAccount,
    LoginState,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
)
from findmy.reports.anisette import LocalAnisetteProvider
from loguru import logger

from .db_handling import MetaDataService


class AppleAccountManager:
    """
    Manages authentication and polling of the Apple Find My API.

    Handles login token generation, token loading, API polling rate
    limiting, and execution of location history fetch requests.
    """

    def __init__(
        self,
        account_path: Path,
        anisette_libs_path: Path,
        metadata_server: MetaDataService | None = None,
    ):
        """
        Initialize the AppleAccountManager.

        Args:
            account_path: Path to the stored Apple account login token JSON file.
            anisette_libs_path: Path to the Anisette libraries required for authentication.
            metadata_server: Optional MetaDataService used to persist polling metadata.
        """

        self.account_path = account_path
        self.anisette_libs_path = anisette_libs_path
        self.apple_account = None
        self.display_poll_information = True

        self.metadata_server = metadata_server

        self.polling_interval = int(
            os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60)
        )  # defaults to 60 * 60 seconds = 60 min

    def generate_login_token(self):
        """
        Interactively authenticate with Apple and generate a login token.

        Prompts the user for email and password, handles optional 2FA,
        and stores the resulting authenticated session in the data folder.
        """
        logger.debug("Initiating AppleAccount login attempt.")

        email = input("email?  > ")
        password = getpass.getpass("passwd? > ")

        self.apple_account = AppleAccount(
            LocalAnisetteProvider(libs_path=self.anisette_libs_path)
        )
        state = self.apple_account.login(email, password)

        if state == LoginState.REQUIRE_2FA:
            methods = self.apple_account.get_2fa_methods()

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

        self.apple_account.to_json(self.account_path)

        logger.info(
            "AppleAccount logged in successfully. Login token stored at '{}'",
            self.account_path,
        )

    def load_login_token(self):
        """
        Load an existing Apple login token from the data folder.

        Blocks until the token file exists. Once available,
        the AppleAccount instance is restored from that token file.
        """

        firstAttempt = True
        while not self.account_path.is_file():
            if firstAttempt:
                logger.info(
                    "AppleAccount login token file not found at '{}'. You must first generate it interactively via "
                    "`docker compose exec bridge .venv/bin/findmy-traccar-bridge-init`",
                    str(self.account_path),
                )

                firstAttempt = False

            time.sleep(1)

        # load account
        self.apple_account = AppleAccount.from_json(self.account_path)

        logger.info(
            "Successfully loaded Apple account token with uid {}...",
            self.apple_account._asyncacc._uid[:4],
        )

    def safe_to_poll(self) -> bool:
        """
        checks wheather the polling interval has allready ellapsed since the last api poll and returns true if so, false otherwise

        Uses the stored metadata value `last_api_poll_time` to ensure
        that Apple API rate limits are respected.
        """
        assert self.metadata_server is not None, (
            "Tried to access metadata, but MetaDataService was not given to AppleAccountManager"
        )
        last_api_poll_time = int(
            self.metadata_server.get_metadata(name="last_api_poll_time", default="0")
        )
        time_since_last_poll = (
            int(datetime.datetime.now().timestamp()) - last_api_poll_time
        )  # time in seconds since last poll

        if self.polling_interval > time_since_last_poll:
            if self.display_poll_information:
                time_to_next_poll = self.polling_interval - time_since_last_poll
                logger.info(
                    "Next API poll in {}s (at {} UTC) to avoid Apple API rate limitation violation.",
                    time_to_next_poll,
                    (
                        datetime.datetime.now()
                        + datetime.timedelta(seconds=time_to_next_poll)
                    ).isoformat(timespec="seconds"),
                )
                self.display_poll_information = False
            return False

        self.display_poll_information = True
        return True

    def execute_api_poll(
        self,
        haystack_keys: List[KeyPair],
        findmy_accessories: List[FindMyAccessory],
    ) -> Dict[Union[KeyPair, FindMyAccessory], list[LocationReport]] | None:
        """
        Fetch location history from the Apple Find My API.

        Args:
            haystack_keys: List of Haystack KeyPair objects.
            findmy_accessories: List of FindMyAccessory objects.

        Returns:
            A dictionary mapping each device (KeyPair or FindMyAccessory)
            to its list of location reports. Returns None if the API call fails.

        Notes:
            The timestamp of the poll is stored in metadata regardless of success or failure.
        """
        try:
            assert self.apple_account is not None, (
                "Tried polling API before initializing account"
            )
            result = self.apple_account.fetch_location_history(
                [*haystack_keys, *findmy_accessories]
            )

            logger.info(
                "AppleAccountManager.execute_api_poll: API Polled successfully. Next Poll in {}s ({} UTC).",
                self.polling_interval,
                (
                    datetime.datetime.now()
                    + datetime.timedelta(seconds=self.polling_interval)
                ).isoformat(timespec="seconds"),
            )

            def narrow_dict_key_type(
                d: dict[Any, list[LocationReport]],
            ) -> TypeGuard[dict[KeyPair | FindMyAccessory, list[LocationReport]]]:
                return all(
                    isinstance(key, KeyPair) or isinstance(key, FindMyAccessory)
                    for key in d
                )

            assert narrow_dict_key_type(result)
            return result
        except Exception as e:
            # The api call could encounter any number of issues. For now we just catch them indiscriminantly.
            # Over time, specific errors could be singled out if custom handling makes sense for them.
            logger.error(f"Unhandled exeception while polling FindMy API: {e}")
            return None
        finally:
            assert self.metadata_server is not None, (
                "Tried to set metadata, but MetaDataService was not given to AppleAccountManager"
            )
            self.metadata_server.set_metadata(
                name="last_api_poll_time",
                value=str(int(datetime.datetime.now().timestamp())),
            )


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

        self.default_plist_dir = "/bridge/plists"
        self.haystack_keys: List[KeyPair] = []
        self.findmy_keys: List[FindMyAccessory] = []

    def load_devices(self) -> None:
        """
        Load all configured devices.

        Loads Haystack keys from environment variables and Find My
        accessories from plist files. Raises a ValueError if no
        devices are configured.
        """

        self.haystack_keys = self.load_haystack_keys()
        self.findmy_keys = self.load_findmy_keys()

        totalNumDevices = self.get_num_haystacks() + self.get_num_findmys()
        if (totalNumDevices) == 0:
            logger.error(
                "No tracking devices configured. Either set BRIDGE_PRIVATE_KEYS environment variable or mount a directory with .plist files to /bridge/plists. Program will now be terminated."
            )
            sys.exit()
        else:
            logger.info(
                "Loaded {} key(s) in total",
                totalNumDevices,
            )

    def get_haystack_keys(self) -> List[KeyPair]:
        """
        Return all loaded Haystack keys.

        Returns:
            List of KeyPair objects.
        """
        return self.haystack_keys

    def get_findmy_accessories(self) -> List[FindMyAccessory]:
        """
        Return all loaded Find My accessories.

        Returns:
            List of FindMyAccessory objects.
        """

        return self.findmy_keys

    def get_num_haystacks(self) -> int:
        """
        Return the number of loaded Haystack keys.
        """

        return len(self.haystack_keys)

    def get_num_findmys(self) -> int:
        """
        Return the number of loaded Find My accessories.
        """

        return len(self.findmy_keys)

    def load_haystack_keys(self) -> List[KeyPair]:
        """
        Load Haystack KeyPairs from the environment variable `BRIDGE_PRIVATE_KEYS`.

        The variable may contain multiple base64-encoded keys separated by commas.

        Returns:
            List of KeyPair objects.
        """

        # load keystring (evenatually several keys comma separated)
        keysStr = os.environ.get("BRIDGE_PRIVATE_KEYS", "")
        keysList = [k for k in keysStr.split(",") if k]

        # generate haystack KeyPairs
        haystack_keys = list()

        for key in keysList:
            haystack_keys.append(KeyPair.from_b64(key))

        return haystack_keys

    def load_findmy_keys(self) -> List[FindMyAccessory]:
        """
        Load FindMyAccessory objects from .plist files.

        The directory is defined by the `BRIDGE_PLIST_PATH`
        environment variable (default: /bridge/plists).

        Returns:
            List of successfully loaded FindMyAccessory objects.

        Notes:
            Invalid or unreadable plist files are skipped and logged.
        """

        # this function is mainly copied from the commit from Felix Bouleau

        # fetch directory
        plist_path = Path(os.environ.get("BRIDGE_PLIST_PATH", self.default_plist_dir))

        if not plist_path.exists():
            logger.info(
                "Plist directory does not exist: {}. No FindMy Accessories loaded",
                plist_path,
            )
            return []

        if not plist_path.is_dir():
            logger.error("Plist path exists but is not a directory: {}", plist_path)
            return []

        # load files from directory
        plist_files = list(plist_path.glob("*.plist"))
        findmy_keys: List[FindMyAccessory] = []

        for plist_path in plist_files:
            try:
                with plist_path.open("rb") as f:
                    findmy_keys.append(FindMyAccessory.from_plist(f))
            except Exception as e:
                logger.error("Failed to load plist file {}: {}", plist_path, str(e))

        return findmy_keys

    def generate_haystack_id(self, key: KeyPair) -> int:
        """
        Generate a numeric device ID for a Haystack key.

        Args:
            key: KeyPair instance.

        Returns:
            Integer ID derived from the hashed advertising key.
        """

        return int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000

    def generate_findmy_id(self, accessory: FindMyAccessory) -> int:
        """
        Generate a numeric device ID for a Find My accessory.

        Args:
            accessory: FindMyAccessory instance.

        Returns:
            Integer ID derived from the accessory identifier.
        """
        assert accessory.identifier is not None, (
            "Accessory didn't include identifier to derive id from"
        )
        return int.from_bytes(accessory.identifier.encode()) % 1_000_000

    def generate_key_id(self, key: KeyPair | FindMyAccessory) -> int:
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
            return self.generate_findmy_id(key)
        if isinstance(key, KeyPair):
            return self.generate_haystack_id(key)
