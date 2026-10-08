# AIRSEA Monitor

**Research-only AIS and aviation observation pilot.** Collects aggregated maritime and aircraft observations to investigate data coverage and coarse changes in traffic. It is not a real-time navigation, defence or trading product.

AIRSEA Monitor is a source-code snapshot of a locally operated, time-limited shadow pilot. It does **not** send trading signals, place orders or issue automatic alerts. It does not establish cargo volume, military intent or the absence of ship movements.

## What it does

- **Maritime AIS:** subscribes to AISStream for a bounded sampling area, validates AIS positions and ship-type messages, and aggregates observation counts.
- **Coarse vessel transitions:** assigns positions to west, intermediate and east zones and counts only qualifying transitions between opposite sides.
- **Aircraft observations:** polls ADSB.lol and OpenSky separately, recording aggregate airborne counts without persisting raw aircraft identifiers or tracks.
- **Data quality:** records source errors, disconnections and source-specific coverage. Ship types arriving after a crossing can improve the tanker-confirmed lower bound.
- **Storage:** uses local SQLite with salted, short-lived per-vessel pseudonyms and daily aggregates. Per-vessel intermediate state is pruned after 15 days.

## Repository layout

| Location | Responsibility |
| --- | --- |
| `App/pilot_core.py` | Geographic zones, AIS parsing, crossing rules, SQLite schema and aggregation |
| `App/collector.py` | AIS websocket acquisition, bounded polling and shadow-pilot lifecycle |
| `App/opensky_oauth.py` | Optional in-memory OpenSky OAuth token handling |
| `App/report.py` | Aggregated status and data-quality reporting |
| `App/run.ps1` | Windows runner expecting credentials pre-provisioned outside the repository |
| `App/import-opensky.ps1` | Import locally supplied OpenSky OAuth JSON into the current Windows user's DPAPI store; requires `-Source` |\n| `App/status.ps1`, `App/stop.ps1`, `App/uninstall.ps1` | Local task inspection and lifecycle utilities |
| `App/pilot-processes.ps1` | Process ownership and orphan checks |
| `tests/test_pilot_core.py` | Synthetic regression scenarios for crossing accounting |
| `scripts/check_publication.py` | Basic tracked-file policy guard; human review remains mandatory |

**Not included:** local databases, runtime logs, keys, credential stores, observation traces and machine-specific task registration. The import helper is published without any credentials. It accepts an explicit local JSON file path and stores its contents using Windows DPAPI; it does not upload those credentials. This repository is not currently a one-command installer.

## Crossing definition and limitations

In the current code, the AIS geographical sampling box covers a broad region. `zone_for()` classifies valid positions into W, M or E using longitude thresholds. The intermediate zone does not count as a crossing on its own.

`process_ais()` counts a direction only after the *same pseudonymous vessel* has observations on both opposite sides **4 minutes to 6 hours apart**, and the qualifying new report has speed over ground in **1–40 knots**. A crossing is deduplicated per vessel, date and direction. Tanker confirmation additionally depends on ship-type codes 80–89.

**Interpretation warning:** Connected AIS uptime and the number of received position messages are not measures of population coverage. If only the west side is observed, zero registered transitions is consistent with insufficient observations; it is not proof of zero traffic. Sparse or delayed reports, ship-type coverage gaps, reception-timestamp assumptions, geographical filtering and UTC day boundaries can bias counts. The current logic does not independently verify physically plausible travel distance between reported positions. Treat transition and tanker totals as observation-dependent lower bounds, not official traffic statistics.

## Development and validation

The Python sources are compatible with Python 3.12+ syntax. The running Windows prototype uses PowerShell 7, `uv`, and the Python `websockets` dependency. The existing local runner expects AISStream credentials stored outside Git; OpenSky OAuth is optional and is also provisioned externally.

Run the source-only regression suite without network access or credentials:

```shell
python -m unittest discover -s tests -v
python scripts/check_publication.py
```

CI compiles the Python sources and runs the synthetic tests on Python 3.12 and 3.13. The publication guard detects common accidental runtime-data files and host identifiers; it is **not** comprehensive credential scanning.

Before production use, add a reproducible installer, explicit configuration validation, robust tests for midnight crossings and stale reports, geographical coverage validation, and physically plausible crossing constraints. Keep live pilot runtime unchanged until changes are tested and deliberately deployed.

## Privacy and security

See [SECURITY.md](SECURITY.md). **Do not commit** credentials, usernames, e-mail addresses, device-specific absolute paths, raw AIS/aircraft observations, SQLite databases or logs. A public source repository must contain no private runtime state.

## License

No reuse license has been selected. Public visibility does not itself grant permission to reuse the code.
