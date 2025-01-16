import json
from pathlib import Path

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

TRACCAR_SERVER = os.environ["BRIDGE_TRACCAR_SERVER"]

logging.basicConfig(
    level=logging.getLevelName(os.environ.get("BRIDGE_LOGGING_LEVEL", "INFO").upper())
)

acc_store = Path("/data/account.json")
acc = AppleAccount(RemoteAnisetteProvider(ANISETTE_SERVER))


def bridge() -> None:
    if (private_keys_raw := os.environ.get("BRIDGE_PRIVATE_KEYS")) is None:
        raise ValueError("env variable BRIDGE_PRIVATE_KEYS must be set")

    private_keys = private_keys_raw.split(",")

    while not acc_store.is_file():
        time.sleep(2)

    with acc_store.open() as f:
        acc.restore(json.load(f))

    keys = [KeyPair.from_b64(key) for key in private_keys]

    while True:
        result = acc.fetch_last_reports(keys)
        for key, reports in result.items():
            # traccar expects unique int ids for each device
            traccar_id = int.from_bytes(key.hashed_adv_key_bytes) % 1_000_000

            logging.info(
                "Sending %s locations from id:%s (%s) to traccar",
                len(reports),
                traccar_id,
                key.hashed_adv_key_b64,
            )
            for report in sorted(reports):
                requests.post(
                    TRACCAR_SERVER,
                    data={
                        "id": traccar_id,
                        "lat": report.latitude,
                        "lon": report.longitude,
                        "timestamp": report.timestamp.strftime("%s"),
                    },
                )

        time.sleep(int(os.environ.get("BRIDGE_POLL_INTERVAL", 60 * 60)))


def init() -> None:
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
