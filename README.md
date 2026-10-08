# AIRSEA Monitor

Experimental monitoring of maritime AIS reports and aircraft observations for situational awareness and data-quality research.

## Purpose

AIRSEA Monitor explores how to collect, normalize and assess maritime and aviation observations without confusing source availability with verified traffic or movements. The prototype combines AIS position reports with aircraft observations from separate providers.

The monitoring pilot is **observational only**. It does not place trades, produce military-intent assessments, or infer confirmed cargo volumes from position data.

## Current state

This public repository is being prepared for a **source-code-only import** from an existing locally running prototype. The implementation is not yet published; no claim of a working installation is made until the reviewed source and tests are committed.

## Data-quality principles

- An active AIS connection is **not** evidence of complete geographic coverage.
- Position reports do not guarantee that both sides of a passage boundary are observed.
- A vessel crossing should be counted only when its consecutive, time-ordered observations support the configured transition rule.
- Ship type may be absent or arrive through a separate static-data message.
- Report all-vessel transitions separately from tanker-confirmed transitions.
- An absence of confirmed crossings is **not** evidence that no vessels passed.
- Flight snapshots from different providers must be labelled with their source and collection time.

## Repository and privacy

Only reviewed, reusable source code, documentation, synthetic examples and tests belong here. Keep credentials, personal information, absolute machine paths, local application settings, AIS/flight raw observations, databases and logs outside Git.

See [SECURITY.md](SECURITY.md) before adding source files.

## Development

The next step is a security-reviewed source import, followed by reproducible tests for zone boundaries, stale observations, message-type handling, and one-crossing-per-vessel accounting. Dependencies, install steps and operating instructions will be documented from the actual checked-in code rather than invented in advance.

## Licensing

No license has been selected yet. Public visibility does not grant a reuse license. A license can be added after the code and its third-party dependencies have been reviewed.
