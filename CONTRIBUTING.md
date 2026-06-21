Contributing to MurOS
=====================

Thanks for considering a pull request or issue report. Below are a few hints
and tips to make them as effective as possible.

MurOS is a fork of OPNsense ported to Debian 13. When you report a problem or
propose a change, keep in mind that the difference between MurOS and OPNsense is
the Linux platform: nftables instead of pf, systemd instead of rc, iproute2
instead of ifconfig, apt instead of pkg. Problems specific to that platform work
belong here; problems present in upstream OPNsense are usually better reported
upstream.

Issue reports
-------------

Issue reports can be bug reports or feature requests. Please search the open and
closed issues before adding a new one.

When creating bug reports, please provide the following:

* The MurOS version where the bug appeared (and the last version where it did
  not, if known)
* The exact URL of the GUI page involved (if any)
* A list of steps to replicate the bug
* Relevant output from journald or the firewall log when applicable

Issue templates can help with getting this just right.

The issue categories are as follows:

* support: community help figuring out setup or code problems, including triage
* cleanup: cosmetic changes or non-operational bugs (display issues, etc.)
* bug: identified operational bug (core features, etc.)
* feature: behavioural changes, additions and missing options
* help wanted: a contributor is missing to carry out the work
* upstream: the problem exists in OPNsense or in included third-party software
* port: a subsystem still carries FreeBSD assumptions and needs Linux work

Responding to issues is voluntary for all participants. As a general rule,
closed tickets will not be responded to. And above all: stay kind and open.

Pull requests
-------------

When creating a pull request, please heed the following:

* Base your code on the latest `main` branch to avoid manual merges
* Code review by the team may occur to help you shape your proposal
* Test your proposal operationally to catch mistakes and avoid merge delay
* Pull requests must adhere to 2-Clause BSD licensing
* Explain the problem and your proposed solution
* Keep the whole repository, including code comments, in English
* If applicable, cite the issue number(s) in your description, for example
  `Fixes: #1234`, `Closes: #1234`, or `Ref: #1234`.
* Read [README.md](./README.md) to learn how to build and check your code
