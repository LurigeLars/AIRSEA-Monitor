# AIS intake diagnostics (schema-v2 compatible)

**Purpose:** distinguish missing AIS frames from frames received but not accepted by the shadow pilot. This is an **observability** feature, not a vessel-traffic estimator or a trading signal.

## Counters

Daily, UTC-keyed `ais_intake_day` contains only aggregate integers:

| Field | Meaning |
| --- | --- |
| `frames_received` | Websocket frames received **after** successful AISStream subscription confirmation. Does not count subscription handshake, failed websocket connects, or frames lost upstream. |
| `accepted_positions` | Position-message frames accepted into the pilot's existing AIS state machine. Not unique vessels and not geographical coverage. |
| `accepted_static` | Static ship-type frames accepted by the state machine. |
| `rejected_bad_json` | Frame could not be decoded as JSON. |
| `rejected_non_object` | JSON was not an object. |
| `rejected_unsupported` | Unsupported or missing AIS `MessageType`. |
| `rejected_structure` | Invalid object/message/metadata structure. |
| `rejected_identifier` | Missing or malformed nine-digit source vessel identifier (not stored). |
| `rejected_position` | Missing, nonsensical or non-finite latitude, longitude or speed. |
| `rejected_outside_box` | Decoded position is outside the configured monitoring region. |
| `rejected_duplicate_stale` | Otherwise parseable frame rejected by the current chronological state machine. |
| `processing_errors` | An **unexpected internal exception** processing a decoded frame; separate from source-data rejections. |

Per day, the accounting invariant is:

```text
frames_received = accepted_positions + accepted_static
                + sum(rejected_*) + processing_errors
```

The report displays `accounting=OK` or `INCONSISTENT`. It also lists only nonzero rejection categories to keep output compact.

## Privacy and limitations

- **No raw AIS frames, MMSIs, tracks, coordinates, source errors, credentials, exception messages or original provider responses are written to this diagnostics table.** Existing, time-limited pseudonymized ship-state and aggregate counters are unchanged.
- The new counters begin at `ais_intake_tracking_since`. **Historical rejection counts are unknown**, even on a day containing older accepted positions.
- `ais_position_messages` and per-zone v2 statistics remain historical **accepted** counters. They are **not** interchangeable with newly counted raw websocket frames.
- `frames_received = 0` could reflect missing upstream coverage, a disconnected subscription, or simply no upstream frames; it does **not** prove that no ships were present.
- A frame rejected for insufficient chronological state is not proof of a provider error. An unsupported type could reflect the user's subscription filter or a provider control message.
- Future support for alternate upstream AIS sources requires separate licensing, coverage analysis and normalization. Do not scrape public vessel map services without their permission.

## Controlled release

The original v2 AIS schema version remains **2**. Adding the diagnostics table is a fully additive database extension; old v2 code can read its existing tables, enabling rollback.

The standalone release script is `scripts/deploy-ais-intake-windows.ps1 -Apply`. It:

1. Requires an existing single-runner AIS v2 Windows installation and a **clean** checkout of this repository's `main` branch.
2. Runs synthetic tests and the existing read-only SQLite migration rehearsal.
3. Stores a local copy of the v2 application and an online SQLite backup.
4. Stops/disables the specified pilot task and verified descendant processes, then takes a final consistent SQLite snapshot.
5. Deploys `ais_diagnostics.py`, `collector.py`, `pilot_core.py` and `report.py`, and installs diagnostics on the **stopped** local SQLite database.
6. Restarts the task and checks for exactly one runner, rolling back the application and database on a detected failure.

A power failure during the change window may require **manual** recovery. Local backups are intentionally retained. No user data is uploaded. **Do not run the original v1→v2 release script again.**

## Acceptance checks

- CI: Windows PowerShell syntax checks, Python 3.12/3.13 synthetic suites, and privacy guard.
- On host: an isolated migration rehearsal against a read-only backup; verify the 16 pre-existing daily counters are preserved.
- After rollout: check one runner, `ais_intake_tracking_since`, received/accepted/rejected/processing-errors accounting, and eventually at least a full day of nontrivial W/M/E coverage.
- No change to instrument/market decisions and **no automated trade notifications**.

If the initial intake is almost entirely empty, this still cannot distinguish upstream AIS reception limits from absence of physical traffic. Compare against an authorized independent source and documented geographic reception coverage before attributing causes.
