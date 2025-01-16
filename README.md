
## Configuration

The script can be configured via the following environment variables:

- `BRIDGE_PRIVATE_KEYS` - required - comma separated string of base64 encoded private keys of your beacons (e.g. can be generated via instructions from [macless-haystack](https://github.com/dchristl/macless-haystack?tab=readme-ov-file#hardware-setup))
- `BRIDGE_TRACCAR_SERVER` - required - url to your traccar server
- `BRIDGE_ANISETTE_SERVER` - optional (default: `https://ani.sidestore.io`) - url to the anisette server used for login
- `BRIDGE_POLL_INTERVAL` - optional (default: 3600 (60 minutes)) - time to wait between querying the apple API. Too frequent polling might get your account banned.
- `BRIDGE_LOGGING_LEVEL` - optional (default: INFO)
