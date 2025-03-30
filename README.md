# findmy-traccar-bridge



A simple script to continuously import [OpenHaystack](https://github.com/seemoo-lab/openhaystack) locations into [Traccar](https://www.traccar.org/).
This project uses the excellent [findmy.py](https://github.com/malmeloo/FindMy.py) project to load the encrypted location data
of your custom tracking beacons from Apple's FindMy network.

![image](https://github.com/user-attachments/assets/6f6b73d3-7cf5-4062-ad7a-13c3fbde2d6b)
![usage_screencast](https://github.com/user-attachments/assets/aa041c66-8490-470f-9abc-8da229c421d4)

## Requirements

- Docker or Python 3.12
- Some OpenHaystack beacons generating data
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
    environment:
      BRIDGE_PRIVATE_KEYS: "<key1>,<key2>,..."
      BRIDGE_TRACCAR_SERVER: "<your traccar base url>:5055"
      BRIDGE_ANISETTE_SERVER: "http://anisette:6969"
  anisette:
    image: dadoum/anisette-v3-server
    volumes:
      - anisette_data:/home/Alcoholic/.config/anisette-v3/lib/
volumes:
  anisette_data:
```

<details>
  <summary>via docker</summary>

  ```shell
  docker build -t findmy-traccar-bridge https://github.com/jannisko/findmy-traccar-bridge.git
  docker network create bridge_net
  docker run -d --name anisette \
  -v ./anisette:/home/Alcoholic/.config/anisette-v3/lib/ \
  --network bridge_net \
  dadoum/anisette-v3-server
  docker run -d --name bridge \
  -v ./:/data \
  --network bridge_net \
  -e BRIDGE_PRIVATE_KEYS="<key1>,<key2>,..." \
  -e BRIDGE_TRACCAR_SERVER="<your traccar base url>" \
  -e BRIDGE_ANISETTE_SERVER="anisette:6969" \
  findmy-traccar-bridge
  ```
</details>

<details>
  <summary>as a python package</summary>

  ```shell
  # you should probably start your own anisette server for this
  export BRIDGE_PRIVATE_KEYS="<key1>,<key2>,..." BRIDGE_TRACCAR_SERVER="<your traccar base url>"
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

- `BRIDGE_PRIVATE_KEYS` - required - comma separated string of base64 encoded private keys of your beacons (e.g. can be generated via instructions from [macless-haystack](https://github.com/dchristl/macless-haystack?tab=readme-ov-file#hardware-setup))
- `BRIDGE_TRACCAR_SERVER` - required - url to your traccar server
- `BRIDGE_ANISETTE_SERVER` - optional (default: `https://ani.sidestore.io`) - url to the anisette server used for login
- `BRIDGE_POLL_INTERVAL` - optional (default: 3600 (60 minutes)) - time to wait between querying the apple API. Too frequent polling might get your account banned.
- `BRIDGE_LOGGING_LEVEL` - optional (default: INFO)

> [!TIP]
> Self-hosting Anisette (and setting `BRIDGE_ANISETTE_SERVER`) is optional, but using the default value may cause issues with authentication. If you are getting repeated errors like `LoginState.REQUIRE_2FA`, this might be the culprit.

## Example

An example compose file running the bridge and Traccar locally can be found in the [testing](./testing) directory:
```shell
git clone https://github.com/jannisko/findmy-traccar-bridge
cd findmy-traccar-bridge/testing
docker compose up -d
docker compose exec bridge .venv/bin/findmy-traccar-bridge-init
```
