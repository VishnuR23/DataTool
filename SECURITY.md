# Security policy

## Supported versions

DataTool is in early development (`0.x`). Only the latest `main` receives security
fixes; there are no long-term support branches yet.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/VishnuR23/DataTool/security/advisories/new)
("Report a vulnerability" on the Security tab). If that is unavailable, contact the
maintainer at [@VishnuR23](https://github.com/VishnuR23).

Please include:

- a description of the issue and its impact,
- steps to reproduce (a minimal example or failing test is ideal),
- affected version or commit, and
- any suggested remediation.

We aim to acknowledge reports within a few days and will coordinate a fix and
disclosure timeline with you. Because DataTool acts autonomously on live
experiments within a trust contract, we take reports affecting the contract, the
audit log, or the decision path especially seriously.

## Scope notes

DataTool runs the operator's own infrastructure and uses the operator's own API
keys (for the optional assistant and LLM variant generation). It stores no
credentials of its own beyond what the operator configures. Reports about
credential handling, the append-only audit log, or trust-contract clamping are in
scope. General hardening suggestions are welcome as regular issues.
