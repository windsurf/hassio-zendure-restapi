# Test environment

Home Assistant with this checkout's integration, and a simulated Zendure
battery on a link that drops requests on purpose.

```sh
cd dev
docker compose up -d
```

1. Open <http://localhost:8123> and finish onboarding.
2. Settings → Devices & services → Add integration → **Zendure RestAPI**.
   Host `zendure-sim`, port `80`.
3. Watch both sides:

   ```sh
   docker compose logs -f zendure-sim                      # every request, and every fault
   docker compose logs -f homeassistant | grep zendure     # retries, grace, recovery
   ```

The integration is mounted read-only from `../custom_components/zendure_restapi`.
After changing the code: `docker compose restart homeassistant`.

## The simulator

`simulator/sim.py` serves the zenSDK local API (`/properties/report`,
`/properties/write`, `/rpc`) on top of the integration's own battery model
(`virtual/device.py`): a written limit moves power and SOC the way a real
SolarFlow was measured to.

A share of requests fails the way a battery at the edge of the WiFi does:

| fault      | what happens                                          | default |
|------------|-------------------------------------------------------|---------|
| `drop`     | connection closed without an answer                   | 0.15    |
| `hang`     | no answer for `SIM_HANG_SECONDS` (10), longer than the 8 s timeout | 0.05 |
| `truncate` | the JSON stops halfway                                | 0.05    |

A write whose reply is lost is still applied, as on a real link, so the retry
sends the same value again.

Change the rates while it runs (also reachable on the host, port 8080):

```sh
curl 'http://localhost:8080/sim?drop=0.5&hang=0&truncate=0'   # a bad spell
curl 'http://localhost:8080/sim?drop=0&hang=0&truncate=0'     # a perfect link
curl  http://localhost:8080/sim                               # settings and counts
curl  http://localhost:8080/properties/report                 # what the integration reads
```

Or set them at start: `SIM_DROP_RATE=0.3 docker compose up -d`. Other
variables: `HA_VERSION` (image tag, default `stable`), `SIM_SOC`,
`SIM_CAPACITY_KWH`, `SIM_SERIAL`, `SIM_PORT`, `TZ`.

Throw away Home Assistant's state: `docker compose down && git clean -fdX config`.
