# Hybrid SaaS control panel + real-time terminal console — design

_Date: 2026-06-22 · Status: approved design, pre-implementation_

## Context and pivot

DataTool was built as an open-source, Apache-2.0, acquisition-optimized autonomous
experimentation controller (see `ARCHITECTURE.md`, `CLAUDE.md`). The owner is
pivoting it to a **paid commercial product**: companies download and run the
controller on their own infra, and watch what it is doing in real time through a
hosted, signed-in web console that the vendor operates.

This pivot intentionally overrides several rules that the original docs marked as
immutable. Those overrides are deliberate and are recorded here so they are not
treated as accidental violations:

- **License:** Apache-2.0 / OSS-first → proprietary, paid. (`CLAUDE.md` license rule,
  `ARCHITECTURE.md §22`.)
- **Dashboard tech:** "no JS frameworks, Jinja2 only" → client-side **vanilla JS**
  (no React/Vue/bundler/node toolchain) is now allowed for the real-time console.
- **Auth:** "API key only, no OAuth/SSO" (`§20` anti-scope) → a real sign-in /
  session system on the hosted side is now in scope.
- **Scope:** the hosted control panel is new product surface beyond the original MVP.

`CLAUDE.md` and `ARCHITECTURE.md` will be updated to record the new direction as part
of the commercial-repackaging work (see Milestone 3 / parallel task) so future
sessions do not fight this design.

## Deployment model: hybrid

Decision: **hybrid**, chosen over pure-SaaS and pure-self-hosted.

The deciding factor is that DataTool is an autonomous agent that **mutates the
customer's production** (ramps flags, promotes, reverts) and needs the customer's
flag/metrics/Postgres keys. Pure SaaS would require customers to let an external
service write to their prod — the hardest possible security/procurement sell, and the
opposite of where the trust contract (the project's core asset) should live. Pure
self-hosted is easiest to trust but hard to monetize, meter, and onboard.

Hybrid resolves the tension and maps onto the existing control-plane / execution-plane
separation:

- **The brain runs in the customer's infra** (the existing `datatool daemon`). It holds
  the keys, touches prod, and clamps every action to the trust contract — all inside the
  customer's security boundary.
- **A hosted control panel that the vendor operates** provides accounts, sign-in, and the
  real-time console. The local agent pushes **read-only telemetry** upward over an
  outbound HTTPS connection. The vendor gets the SaaS account / onboarding / (later)
  billing surface **without ever holding keys that can move the customer's production.**

## Architecture

```
  CUSTOMER INFRA (self-hosted)                 VENDOR INFRA (hosted SaaS)
┌────────────────────────────────┐          ┌───────────────────────────────────┐
│  datatool daemon (the brain)    │          │  cloud control panel (FastAPI)     │
│  - holds flag/metrics keys      │          │  - orgs, users, sessions (auth)    │
│  - touches prod, runs stats     │  HTTPS   │  - enrollment tokens, license tier │
│  - trust contract clamps        │ ───────► │  - ingest endpoint (stores events) │
│                                 │ outbound │  - SSE fan-out to browsers         │
│  NEW: telemetry reporter        │  only    │  - terminal-aesthetic web console  │
│  batches audit/decision/action/ │          │                                    │
│  trust events, POSTs them up    │          │  Browser ◄── SSE ── live console    │
└────────────────────────────────┘          └───────────────────────────────────┘
```

### Load-bearing invariants

1. **Outbound-only from the customer.** The agent *pushes* event batches over HTTPS to
   the panel. The panel never opens an inbound connection into the customer's network.
   This is firewall-friendly and is the property that makes it pass a security review.
2. **Telemetry flows up; control does NOT flow down.** In this milestone the panel is a
   strictly read-only mirror of what the agent did. It cannot issue commands to the agent
   and cannot move the customer's production. This preserves the "brain stays in their
   infra" promise and protects the trust-contract guarantee. Remote control is a
   deliberately deferred, separately-gated future feature — **not** in scope here.
3. **Two separate Postgres databases.** The customer's existing DB is unchanged. The
   hosted panel has its own, multi-tenant DB. No shared schema.
4. **Clean separation, same as the adapter rule.** `cloud/` does not import customer-side
   business logic (`core/`, `control/`, `stats/`). It depends only on a shared, versioned
   **event schema**. An acquirer could run the panel against any agent that speaks the
   schema.

## Repository layout (same repo, two new units)

```
datatool/
├── telemetry/            # NEW — agent-side: reporter + `datatool connect`
│   ├── reporter.py       #   tails event streams, batches, POSTs upward, buffers offline
│   ├── events.py         #   the shared, versioned event schema (Pydantic)
│   └── config.py         #   DATATOOL_CLOUD_URL, DATATOOL_CLOUD_TOKEN
cloud/                    # NEW top-level — the hosted control panel (FastAPI service)
├── app.py                #   FastAPI app factory
├── auth/                 #   sign-up, login, sessions, password hashing
├── accounts/             #   orgs, users, enrollment tokens, license tier
├── ingest/               #   token-authenticated event intake + per-org live channel
├── console/              #   sign-in page + terminal console (Jinja shell + vanilla JS/CSS/SSE)
├── persistence/          #   panel Postgres models + migrations
└── api/                  #   REST + SSE endpoints the console consumes
```

The shared event schema is the contract between the two units. It lives in
`datatool/telemetry/events.py` and is imported by `cloud/ingest/` (the only allowed
cross-unit dependency, and it is data-only).

## Components

| Unit | Lives | Responsibility | Depends on |
|---|---|---|---|
| Telemetry reporter | `datatool/telemetry/` | Tail agent event streams (audit/decision/action/trust), batch, POST to panel with token, retry + buffer when offline; never block the control loop | enrollment token + panel URL |
| Ingest | `cloud/ingest/` | Authenticate token, validate event schema, store, publish to the org's live channel | accounts (token → org) |
| Accounts + auth | `cloud/auth/`, `cloud/accounts/` | Org sign-up, login, session cookies, enrollment-token management, license tier (stored) | panel Postgres |
| Terminal console | `cloud/console/` | Sign-in page + live activity console (SSE), terminal aesthetic — the frontend-design showpiece | ingest channel, auth |
| Onboarding | CLI + first-run | Sign up → token → `datatool connect <token>` → live, in one flow; wizard automates | accounts, reporter |
| Commercial repackaging | repo root + docs | LICENSE → proprietary; README/CONTRIBUTING/CoC reframed; CLAUDE.md/ARCHITECTURE.md updated | — |

Note: this **reverses the OSS community docs committed earlier** (`CONTRIBUTING.md`,
`CODE_OF_CONDUCT.md`, Apache framing in `README.md`). They will be reframed, not
silently deleted; git history is preserved.

## Data flow

### Enrollment (one-time)

1. Customer signs up on the hosted panel and creates an org.
2. Customer generates an **enrollment token** scoped to that org.
3. Customer sets `DATATOOL_CLOUD_URL` and `DATATOOL_CLOUD_TOKEN` in the agent config.
4. Customer runs `datatool connect`, which validates the token against the panel and
   starts the reporter. The onboarding wizard performs steps 2–4 with prompts and a
   browser-open.

### Live streaming

1. The agent takes an action (ramp / promote / revert / guardrail eval) and writes its
   audit row exactly as today.
2. The reporter detects the new events, batches them, and `POST`s to `/ingest` with the
   token as bearer credential.
3. The panel authenticates the token → resolves the org → validates and stores the
   events → publishes them to that org's live channel.
4. Every browser console currently open on that org receives the events over **SSE** and
   appends new lines to the terminal view in real time.

Transport choice: batched HTTPS `POST` upward (firewall-friendly, simple, resilient with
an offline buffer) + browser-facing **SSE** for the live fan-out (read-mostly, no
framework needed, auto-reconnect built into the browser). WebSockets are not needed.

## The terminal console (frontend-design showpiece)

A browser app behind sign-in, styled as a live terminal: monospace, live-appending event
lines with status glyphs (ramp ↑, hold ⏸, promote ✓, revert ⟲, guardrail breach ⚠),
per-experiment filtering, and a connection indicator showing whether the agent is
currently reporting. Built with hand-crafted vanilla JS + CSS consuming SSE — no React,
no bundler, no node toolchain. `frontend-design` is invoked at implementation time to
give it a distinctive, intentional aesthetic rather than a templated default.

## Security and trust

- **Org isolation is a hard gate.** One org must never see another org's events. Treated
  like a stats calibration test: a failing isolation test blocks the build.
- **Token auth** on ingest: bad or revoked tokens are rejected; tokens are org-scoped and
  revocable.
- **Session auth** on the console: standard session-cookie lifecycle, no cross-org leakage.
- **Passwords** hashed with a strong KDF (argon2 or bcrypt). No plaintext, no reversible
  storage.
- **The stats calibration gate stays untouched and must stay green.** None of this work
  touches `stats/`.
- **No control path down to the agent** (see invariant 2).

## Milestones

### Milestone 1 — the live hybrid loop (first spec → build now)

A thin vertical slice proving the entire loop end-to-end, each piece minimal:

- Org sign-up + **single admin login** (session cookie).
- Enrollment token generation + revocation.
- Agent-side telemetry reporter: outbound POST, offline buffer, never blocks the loop.
- Ingest: token auth, schema validation, org-scoped storage + live channel.
- Terminal console streaming live events over SSE behind sign-in.
- Self-serve onboarding for the happy path (`datatool connect` + wizard).

**Deferred from Milestone 1:** teammate invites and roles; payments; license-tier
enforcement beyond a stored value; remote control of the agent.

### Milestone 2 — team + tiers

Multi-user invites and admin/viewer roles; license-tier enforcement (e.g. agent-count
limits); console polish.

### Milestone 3 — payments + repackaging finalization

Stripe (checkout, subscriptions, webhooks); finalize the commercial repackaging
(LICENSE, README, CONTRIBUTING/CoC, CLAUDE.md/ARCHITECTURE.md).

### Parallel task — license/docs repackaging

Independent of the milestones; can land anytime. Flips LICENSE to proprietary and updates
the invariants docs to record this design's overrides.

## Testing

- **Reporter:** offline-buffer + retry behavior; proof it never blocks the control loop;
  correct batching and at-least-once delivery.
- **Ingest:** token auth (reject bad/revoked), schema validation, **org isolation (hard
  gate)**.
- **Auth:** session lifecycle, password hashing, no cross-org leakage.
- **Console:** SSE connect/reconnect, event rendering, per-experiment filtering.
- **Existing suites unchanged:** the full `tests/` suite and the `tests/stats/`
  calibration gate must stay green.

## Open decisions deferred to the implementation plan

- Exact event-schema fields and versioning strategy (will derive from the existing
  audit/decision/action/trust row shapes).
- Panel Postgres schema details (orgs, users, sessions, enrollment_tokens, events).
- Whether ingested events are stored durably in the panel or only relayed + retained for a
  short window — leaning durable with a retention policy, to be confirmed in the plan.
- Reporter cursor/checkpoint mechanism for at-least-once delivery.
