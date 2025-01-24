import datetime
import json
from pathlib import Path
from typing import TypedDict

from findmy.reports import (
    AppleAccount,
    LoginState,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
)

import logging
from findmy import KeyPair
from findmy.reports import RemoteAnisetteProvider
import requests
import os
import time
import getpass


ANISETTE_SERVER = os.environ.get("BRIDGE_ANISETTE_SERVER", "https://ani.sidestore.io")

POLLING_INTERVAL = int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60))

logging.basicConfig(
    level=logging.getLevelName(os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO").upper())
)

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


def bridge() -> None:
    """
    Main loop fetching location data from the Apple API and forwarding it to a Traccar server.

    Callable via the binary `.venv/bin/findmy-traccar-bridge`
    """
    if (private_keys_raw := os.environ.get("BRIDGE_PRIVATE_KEYS")) is None:
        raise ValueError("env variable BRIDGE_PRIVATE_KEYS must be set")

    private_keys = private_keys_raw.split(",")

    TRACCAR_SERVER = os.environ["BRIDGE_TRACCAR_SERVER"]

    logging.info("Target Traccar server: %s", TRACCAR_SERVER)

    if not acc_store.is_file():
        logging.info(
            "Login token file not found at '%s'. You must first generate it interactively via "
            "`docker compose exec bridge .venv/bin/findmy-traccar-bridge-init`",
            str(acc_store),
        )
        while not acc_store.is_file():
            time.sleep(1)

    with acc_store.open() as f:
        acc.restore(json.load(f))

    logging.info(
        "Successfully loaded Apple account token with uid %s...", acc._asyncacc._uid[:4]
    )

    keys = [KeyPair.from_b64(key) for key in private_keys]

    logging.info(
        "Successfully parsed private keys for %s device%s",
        len(keys),
        "" if len(keys) == 1 else "s",
    )

    persistent_data: PersistentData = json.loads(persistent_data_store.read_text())
    last_traccar_push_timestamp = 0  # not super important, so not persistent

    logging.info(
        "Next Apple API polling in %s seconds (%s UTC)",
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

            result = acc.fetch_last_reports(keys)
            persistent_data["last_apple_api_call"] = int(
                datetime.datetime.now().timestamp()
            )
            commit(persistent_data)

            for key, reports in result.items():
                # traccar expects unique int ids for each device
                traccar_id = int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000

                logging.info(
                    "Received %s locations from device:%s (%s...) from Apple",
                    len(reports),
                    traccar_id,
                    key.hashed_adv_key_b64[:8],
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
                logging.info(
                    "Queued up %s locations from device:%s (%...) for upload (deduplicated)",
                    len(deduplicated_locations),
                    traccar_id,
                    key.hashed_adv_key_b64[:8],
                )

            logging.info(
                "Next Apple API polling in %s seconds (%s UTC)",
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
                logging.info(
                    "Uploading %s locations to traccar (%s)",
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
                        logging.warning(
                            "Upload (%s, %s) failed with unexpected code %s",
                            location["id"],
                            location["timestamp"],
                            resp.status_code,
                        )
                        logging.debug("API returned %s", resp.text)
                    # device id has not been claimed yet in the traccar UI. remember to retry
                    failed_upload_locations.append(location)

            unique_failed_devices = {
                location["id"] for location in failed_upload_locations
            }
            if len(unique_failed_devices) > 0:
                logging.warning(
                    "Failed to upload locations for devices %s. They might need to be claimed in the traccar UI first. "
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
