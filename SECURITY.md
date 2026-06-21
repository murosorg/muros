# Security Policy

MurOS is a network firewall, so security reports are taken seriously.

## Supported versions

MurOS is in beta and is delivered as a rolling Debian package from the signed
apt repository at https://download.muros.org . Security fixes are published as
new package versions; always run the latest release:

    apt update && apt install --only-upgrade muros

Only the most recent published version is supported for security updates.

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than opening a public
issue. Use the security advisory feature on the project repository, or contact
the maintainers, and include enough detail to reproduce the problem.

Vulnerabilities inherited from upstream OPNsense or from bundled third-party
software should also be reported to the relevant upstream project.
