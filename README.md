# findmy-traccar-bridge



A simple script to continuously import [OpenHaystack](https://github.com/seemoo-lab/openhaystack) locations into [Traccar](https://www.traccar.org/).
This project uses the excellent [findmy.py](https://github.com/malmeloo/FindMy.py) project to load the encrypted location data
of your custom tracking beacons from Apple's FindMy network.

![image](https://github.com/user-attachments/assets/6f6b73d3-7cf5-4062-ad7a-13c3fbde2d6b)
![usage_screencast](https://github.com/user-attachments/assets/aa041c66-8490-470f-9abc-8da229c421d4)

## Requirements

- Docker or Python 3.12
- Some OpenHaystack beacons generating data, or [decrypted plist files](https://github.com/malmeloo/FindMy.py/issues/31) for real Airtags
  - e.g. an [esp32](https://github.com/dchristl/macless-haystack/blob/main/firmware/ESP32/README.md) or [NRF51](https://github.com/dchristl/macless-haystack/blob/main/firmware/nrf5x/README.md)
  - I recommend following the instructions `2. Hardware setup` from [macless-haystack](https://github.com/dchristl/macless-haystack?tab=readme-ov-file#setup). This is also where you will generate the private key for later.
- Access to an Apple account with 2FA enabled
> [!IMPORTANT]
> Using Apple's internal API like this may get your account banned, depending on how "trustworthy" Apple deems your account.
> In general, one query every 30 minutes seems to be safe, even for new throwaway accounts (this project querys once per hour by default).
> Some anecdotes from others:
> [[1]](https://github.com/dchristl/macless-haystack/pull/30#issuecomment-1858816159)
> [[2]](https://news.ycombinator.com/item?id=42480693)
> [[3]](https://news.ycombinator.com/item?id=42482047)

## Usage
Run the bridge via `docker compose`:
```yml
services:
  bridge:
    build: https://github.com/jannisko/findmy-traccar-bridge.git
    volumes:
      - ./:/bridge/data
      # Optional: Mount a directory with plist files for AirTags
      - /path/to/your/plists:/bridge/plists
    environment:
      # For OpenHaystack beacons, specify their private keys
      BRIDGE_PRIVATE_KEYS: "<key1>,<key2>,..."
      # Optional: Override the default directory for plist files
      # BRIDGE_PLIST_DIR: "/some/other/path"
      BRIDGE_TRACCAR_SERVER: "<your traccar base url>:5055"
```

<details>
  <summary>via docker</summary>

  ```shell
  docker build -t findmy-traccar-bridge https://github.com/jannisko/findmy-traccar-bridge.git
  docker run -d --name bridge \
  -v ./:/data \
  # Optional: Mount directory with plist files for AirTags
  -v /path/to/your/plists:/bridge/plists \
  -e BRIDGE_PRIVATE_KEYS="<key1>,<key2>,..." \
  -e BRIDGE_TRACCAR_URL="<your traccar base url>" \
  findmy-traccar-bridge
  ```
</details>

<details>
  <summary>as a python package</summary>

  ```shell
  # Set up environment variables
  export BRIDGE_PRIVATE_KEYS="<key1>,<key2>,..." BRIDGE_TRACCAR_SERVER="<your traccar base url>"
  # If you want to use AirTags through plist files, they'll be detected automatically in /bridge/plists
  # Optionally you can override the plist directory:
  # export BRIDGE_PLIST_DIR="/path/to/your/plists"
  
  # Run the bridge
  uvx --from=git+https://github.com/jannisko/findmy-traccar-bridge findmy-traccar-bridge
  ```
</details>

## Initialization

To query the internal Apple FindMy API you will need to interactively log into your Apple account with a 2FA challenge
when initially setting up the containers. Until this is done, the bridge container will stay idle.

```shell
docker compose exec bridge .venv/bin/findmy-traccar-bridge-init
```

<details>
  <summary>via docker</summary>

  ```shell
  docker exec -it bridge .venv/bin/findmy-traccar-bridge-init
  ```
</details>
<details>
  <summary>as a python package</summary>

  ```shell
  uvx --from=git+https://github.com/jannisko/findmy-traccar-bridge findmy-traccar-bridge-init
  ```
</details>

## Configuration

The script can be configured via the following environment variables:

- `BRIDGE_PRIVATE_KEYS` - comma separated string of base64 encoded private keys of your OpenHaystack beacons (e.g. can be generated via instructions from [macless-haystack](https://github.com/dchristl/macless-haystack?tab=readme-ov-file#hardware-setup))
- `BRIDGE_PLIST_DIR` - (optional) override the default directory path for [decrypted plist files](https://github.com/malmeloo/FindMy.py/issues/31). By default, the app will look for .plist files in `/bridge/plists`. Only set this if you need to use a different location.
- `BRIDGE_TRACCAR_SERVER` - required - url to your traccar server
- `BRIDGE_ANISETTE_SERVER` - optional (default: `https://ani.sidestore.io`) - url to the anisette server used for login
- `BRIDGE_POLL_INTERVAL` - optional (default: 3600 (60 minutes)) - time to wait between querying the apple API. Too frequent polling might get your account banned.
- `BRIDGE_LOGGING_LEVEL` - optional (default: INFO)

## Example

An example compose file running the bridge and Traccar locally can be found in the [testing](./testing) directory:
```shell
git clone https://github.com/jannisko/findmy-traccar-bridge
cd findmy-traccar-bridge/testing
docker compose up -d
docker compose exec bridge .venv/bin/findmy-traccar-bridge-init
```
