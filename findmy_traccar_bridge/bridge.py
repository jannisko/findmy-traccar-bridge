import datetime
import getpass
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
    RemoteAnisetteProvider,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
)
from loguru import logger

logger.remove()
logger.add(sys.stderr, level=os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO"))

ANISETTE_SERVER = os.environ.get("BRIDGE_ANISETTE_SERVER", "https://ani.sidestore.io")

POLLING_INTERVAL = int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60))


data_folder = Path("./data/")
data_folder.mkdir(exist_ok=True)
persistent_data_store = data_folder / "persistent_data.json"
acc_store = data_folder / "account.json"
acc = AppleAccount(RemoteAnisetteProvider(ANISETTE_SERVER))


class Location(TypedDict):
    id: int
    timestamp: int
    lat: float
    lon: float


class PersistentData(TypedDict):
    # rejected locations by traccar (id has not been claimed by a user), will keep retrying to upload these
    pending_locations: list[Location]
    # recently uploaded locations used for deduplication
    uploaded_locations: list[Location]
    # unix timestamp
    last_apple_api_call: int


if not persistent_data_store.is_file():
    persistent_data_store.write_text(
        json.dumps(
            PersistentData(
                pending_locations=[],
                uploaded_locations=[],
                last_apple_api_call=0,
            )
        )
    )


def commit(persistent_data: PersistentData) -> None:
    persistent_data_store.write_text(json.dumps(persistent_data))


def load_airtags_from_directory(directory_path: str | None) -> list[FindMyAccessory]:
    """
    Load all FindMyAccessory objects from .plist files in the specified directory.

    Args:
        directory_path: Path to the directory containing .plist files

    Returns:
        List of loaded FindMyAccessory objects
    """
    if not directory_path:
        return []

    airtags = []
    dir_path = Path(directory_path)

    if not dir_path.exists():
        # Only log as error if it's not the default path
        if directory_path != "/bridge/plists":
            logger.error("Plist directory does not exist: {}", directory_path)
        return []

    if not dir_path.is_dir():
        logger.error("Plist path exists but is not a directory: {}", directory_path)
        return []

    plist_files = list(dir_path.glob("*.plist"))

    for plist_path in plist_files:
        try:
            with plist_path.open("rb") as f:
                airtags.append(FindMyAccessory.from_plist(f))
        except Exception as e:
            logger.error("Failed to load plist file {}: {}", plist_path, str(e))

    return airtags


def bridge() -> None:
    """
    Main loop fetching location data from the Apple API and forwarding it to a Traccar server.

    Callable via the binary `.venv/bin/findmy-traccar-bridge`
    """

    private_keys = [
        k for k in (os.environ.get("BRIDGE_PRIVATE_KEYS") or "").split(",") if k
    ]

    # Default plist directory location
    default_plist_dir = "/bridge/plists"

    # Custom plist directory can override the default
    plist_dir = os.environ.get("BRIDGE_PLIST_DIR", default_plist_dir)

    haystack_keys = [KeyPair.from_b64(key) for key in private_keys]
    real_airtags = load_airtags_from_directory(plist_dir)

    if not private_keys and not real_airtags:
        raise ValueError(
            "No tracking devices configured. Either set BRIDGE_PRIVATE_KEYS environment variable "
            "or mount a directory with .plist files to /bridge/plists"
        )

    TRACCAR_SERVER = os.environ["BRIDGE_TRACCAR_SERVER"]

    logger.info("Target Traccar server: {}", TRACCAR_SERVER)

    if not acc_store.is_file():
        logger.info(
            "Login token file not found at '{}'. You must first generate it interactively via "
            "`docker compose exec bridge .venv/bin/findmy-traccar-bridge-init`",
            str(acc_store),
        )
        while not acc_store.is_file():
            time.sleep(1)

    with acc_store.open() as f:
        acc.restore(json.load(f))

    logger.info(
        "Successfully loaded Apple account token with uid {}...", acc._asyncacc._uid[:4]
    )

    haystack_keys = [KeyPair.from_b64(key) for key in private_keys]

    logger.info(
        "Configured {} device{}:",
        len(haystack_keys) + len(real_airtags),
        "" if len(real_airtags) == 1 else "s",
    )
    for key in haystack_keys:
        logger.info(
            "   Haystack device\t| Private key: {}[...]\t\t|\tTraccar ID {}",
            key.hashed_adv_key_b64[:16],
            int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000,
        )
    for airtag in real_airtags:
        logger.info(
            "   FindMy device\t\t| plist identifier: {}[...]\t|\tTraccar ID {}",
            airtag.identifier[:16],
            int.from_bytes(airtag.identifier.encode()) % 1_000_000,
        )

    persistent_data: PersistentData = json.loads(persistent_data_store.read_text())
    last_traccar_push_timestamp = 0  # not super important, so not persistent

    logger.info(
        "Next Apple API polling in {} seconds ({} UTC)",
        time_until_next := max(
            0,
            int(
                -(
                    datetime.datetime.now().timestamp()
                    - persistent_data["last_apple_api_call"]
                    - POLLING_INTERVAL
                )
            ),
        ),
        (
            datetime.datetime.now() + datetime.timedelta(seconds=time_until_next)
        ).isoformat(timespec="seconds"),
    )

    while True:
        # avoid calling the API too often, otherwise the account might be banned
        # also makes sure to respect the interval if the process just restarted (e.g. in a bootloop)
        time_until_next_apple_polling = -(
            datetime.datetime.now().timestamp()
            - persistent_data["last_apple_api_call"]
            - POLLING_INTERVAL
        )
        time_until_next_traccar_push = -(
            datetime.datetime.now().timestamp() - last_traccar_push_timestamp - 30
        )

        if time_until_next_apple_polling > 0 and time_until_next_traccar_push > 0:
            # sleep short durations so that SIGTERM stops the container
            time.sleep(1)
        elif time_until_next_apple_polling <= 0:
            already_uploaded = {
                (location["id"], location["timestamp"])
                for location in persistent_data["uploaded_locations"]
            }
            already_pending = {
                (location["id"], location["timestamp"])
                for location in persistent_data["pending_locations"]
            }

            result = acc.fetch_last_reports(haystack_keys)
            for airtag in real_airtags:
                result[airtag.identifier] = acc.fetch_last_reports(airtag)

            persistent_data["last_apple_api_call"] = int(
                datetime.datetime.now().timestamp()
            )
            commit(persistent_data)

            for key, reports in result.items():
                # Traccar expects unique int ids for each device. How we get it depends on the accessory type.
                if isinstance(key, str):
                    # The result set belongs to a "real" FindMy accessory, so we will get many Keypairs
                    # back due to key rotation. Let's identify using the exported `identifier`
                    traccar_id = int.from_bytes(key.encode()) % 1_000_000
                    shorthand = key
                else:
                    # The result set belongs to a Haystack accessory, so we the keypair is stable. We
                    # will use that as an identifier.
                    traccar_id = int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000
                    shorthand = key.hashed_adv_key_b64[:8]

                logger.info(
                    "Received {} locations from device:{} ({}...) from Apple",
                    len(reports),
                    traccar_id,
                    shorthand,
                )

                transformed_reports = [
                    Location(
                        id=traccar_id,
                        lat=report.latitude,
                        lon=report.longitude,
                        timestamp=int(report.timestamp.timestamp()),
                    )
                    for report in reports
                ]

                # queue up new locations received from API without duplicating any
                persistent_data["pending_locations"].extend(
                    deduplicated_locations := [
                        location
                        for location in transformed_reports
                        if (location["id"], location["timestamp"])
                        not in already_uploaded
                        and (location["id"], location["timestamp"])
                        not in already_pending
                    ]
                )
                logger.info(
                    "Queued up {} locations from device:{} ({}...) for upload (deduplicated)",
                    len(deduplicated_locations),
                    traccar_id,
                    shorthand,
                )

            logger.info(
                "Next Apple API polling in {} seconds ({} UTC)",
                int(
                    -(
                        datetime.datetime.now().timestamp()
                        - persistent_data["last_apple_api_call"]
                        - POLLING_INTERVAL
                    )
                ),
                datetime.datetime.fromtimestamp(
                    persistent_data["last_apple_api_call"] + POLLING_INTERVAL
                ).isoformat(timespec="seconds"),
            )

            commit(persistent_data)

        elif time_until_next_traccar_push <= 0:
            if (count_locations := len(persistent_data["pending_locations"])) > 0:
                logger.info(
                    "Uploading {} locations to traccar ({})",
                    count_locations,
                    TRACCAR_SERVER,
                )

            failed_upload_locations = []

            for location in persistent_data["pending_locations"]:
                resp = requests.post(
                    TRACCAR_SERVER,
                    data=location,
                )

                if resp.status_code == 200:
                    persistent_data["uploaded_locations"].append(location)
                else:
                    if resp.status_code != 400:
                        logger.warning(
                            "Upload ({}, {}) failed with unexpected code {}",
                            location["id"],
                            location["timestamp"],
                            resp.status_code,
                        )
                        logger.debug("API returned {}", resp.text)
                    # device id has not been claimed yet in the traccar UI. remember to retry
                    failed_upload_locations.append(location)

            unique_failed_devices = {
                location["id"] for location in failed_upload_locations
            }
            if len(unique_failed_devices) > 0:
                logger.warning(
                    "Failed to upload locations for devices {}. They might need to be claimed in the traccar UI first. "
                    "Reupload will be attempted.",
                    unique_failed_devices,
                )

            persistent_data["pending_locations"] = failed_upload_locations

            last_traccar_push_timestamp = datetime.datetime.now().timestamp()

            commit(persistent_data)


def init() -> None:
    """
    One-time interactive login procedure to answer 2fa challenge and generate API token.

    Callable via the binary `.venv/bin/findmy-traccar-bridge-init`
    """
    email = input("email?  > ")
    password = getpass.getpass("passwd? > ")

    state = acc.login(email, password)

    if state == LoginState.REQUIRE_2FA:
        methods = acc.get_2fa_methods()

        for i, method in enumerate(methods):
            if isinstance(method, TrustedDeviceSecondFactorMethod):
                print(f"{i} - Trusted Device")
            elif isinstance(method, SmsSecondFactorMethod):
                print(f"{i} - SMS ({method.phone_number})")

        ind = int(input("Method? > "))

        method = methods[ind]
        method.request()
        code = getpass.getpass("Code? > ")

        method.submit(code)

    with acc_store.open("w+") as f:
        json.dump(acc.export(), f)
