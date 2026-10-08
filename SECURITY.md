# Security and responsible publication

This repository is public. Treat every commit, pull request, issue, and CI log as permanently accessible.

## Never commit

- AIS, aviation-provider, GitHub or other API keys, passwords, session cookies, bearer tokens, credential files, or encrypted credential exports.
- Personal email addresses, local usernames, home directories, drive letters, absolute workstation paths or other host-specific configuration.
- Runtime databases, journal/WAL files, application logs, screenshots, flight/vessel tracks, exact observation datasets or personally identifiable data.
- Production endpoints or configuration that expose private infrastructure.
- Raw machine status reports or command output containing secrets.

## Before publication

1. Copy only explicitly approved source files into a clean staging checkout; do not stage whole runtime directories.
2. Review the **contents** of every staged file for secrets, absolute paths and operational data. Automated secret scans are helpful but not sufficient.
3. Verify all sample values are synthetic and all API access is configured through environment variables or local credential storage.
4. Run `git status --short` and `git diff --cached --stat`, then review the full staged diff.
5. Check the entire commit history for secrets before pushing. `.gitignore` does not protect files already tracked or committed.
6. If any material is exposed, rotate credentials and remediate repository history; deleting a file in a later commit is insufficient.

## Vulnerability reporting

Do not paste credentials or sensitive production data into a public GitHub issue. Use a private disclosure channel agreed with the maintainer.

## Monitoring safeguards

The project should minimize retention of raw location data, enforce credential-scoped access where needed, and clearly distinguish a valid observation from missing data or an unverified classification. An AIS or aircraft-source outage must be reported as incomplete coverage, not zero traffic.
