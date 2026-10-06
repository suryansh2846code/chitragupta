# Accounts, identity and recovery — design

**Status:** **Phase 1 is built** (§8) — the local encrypted archive, restore,
the native file picker, and automatic backups. **Google sign-in is built** too,
and needed no server after all: §2's constraint is Apple's, not Google's, and
RFC 8252's loopback+PKCE flow runs entirely on-device (`chitragupta/account/`).
**Apple and Microsoft are not built**, and Apple still cannot be without a
verified domain. Neither is anything that *enforces* a plan: local identity
tells you who signed in, and a server is what would make that binding.

**Decided** (2026-10-06): account creation is the **first onboarding screen
with no skip** (§0 — this reverses the skippable position below), **one Mac per
licence** (recorded, not enforceable without a server), local backup **free**
and hosted backup **paid**, and **no money yet** — ship, count users, price
later.
**Written:** 2026-10-05, Phase 1 landed 2026-10-06. **Mode:** DESIGN.
**Prerequisite reading:** [`AUTH-ANALYSIS.md`](AUTH-ANALYSIS.md) — why this is
three systems, not one.

Goal: a user signs in with **Google, Apple or Microsoft**, their custom agents
and brain can be recovered onto a new Mac, and the local-first promise survives.

This document is the contract. CLAUDE.md requires it before the code, because
this spans the desktop app, a new backend, and a cryptosystem, and *"two halves
that each pass their own tests can still be wrong about each other."*

---

## 0. One conflict to resolve before anything is built

CLAUDE.md, *This is a product, not a developer tool*:

> **Assume nothing is installed and nothing is configured.** First launch, no
> keys, no CLIs, no accounts — it must still open and explain itself.

A **mandatory** login screen breaks that invariant, and with it the offline
promise — an app that cannot open on a plane is not local-first. The rest of
this design therefore assumes:

> **Making an account is the first screen of onboarding, and it has no skip.**
> Decided 2026-10-06, reversing the skippable position this section argued for.
> The account unlocks cloud backup and cross-machine recovery; **no local
> feature ever checks for a session**, which is the half of the original design
> that survives intact and is enforced by a test that walks the source.

The invariant in `/CLAUDE.md` was edited in the same commit, as a change of this
kind requires. What was traded away is stated plainly rather than buried: **a
first run with no network cannot get past screen one.** What was kept is that
the screen can never *trap* anybody — a build with no OAuth client, or an
unreachable account endpoint, passes the user through, because a gate whose key
does not exist is a bricked app rather than a strict one.

That gives you the full account system you asked for and keeps the invariant.
**If you want sign-in to be mandatory instead, that is your call to make** — but
it is a deliberate product change, so it needs a CLAUDE.md edit in the same
commit, and §12 lists what else shifts. Everything below works either way; only
the gate at the door moves.

---

## 1. What connects to what

Your question — *what is connected with our app and what is not*. The single
most important answer in this document:

> **Signing in with Google is NOT connecting Gmail. They are separate OAuth
> clients, separate consents, separate tokens, and separate revocation.**

Verified in the code: `connectors/google_auth.py:26` requests
`gmail.readonly`, `gmail.send`, `gmail.modify`, `drive.readonly`, `drive.file`,
`calendar.readonly`, `calendar.events` — and **no `openid`, `email` or
`profile`**. There is no identity scope in the connector token at all, so
identity login cannot piggyback on it and must not try.

| | Identity login (new) | Connector (exists) |
|---|---|---|
| **Scopes** | `openid email profile` only | `gmail.*`, `drive.*`, `calendar.*` |
| **Purpose** | who you are, for billing + backup | reading your mail/files |
| **Token** | our session token, Keychain | `google_token.json` in home |
| **OAuth client** | a new, separate client id | the existing client |
| **Consent screen** | "Chitragupta wants your name and email" | "…wants to read your mail" |
| **Revoke one** | the other keeps working | the other keeps working |

Three reasons this separation is non-negotiable, each already a rule here:

- **Asking for inbox access at signup kills the product.** A first-run consent
  screen reading *"Chitragupta wants to read, send and delete your mail"* is
  the thing a new user quits over. CLAUDE.md: *"Never show a control that
  cannot work"* — and a consent nobody grants is worse.
- **"Detection is not consent."** An identity token proving the user *has* a
  Google account must never mark Gmail connected.
- **"Credentials are independent: removing a key must not sign the user out."**
  The inverse holds too — signing out must not disconnect their mail.

**Microsoft identity does not create an Outlook connector.** It opens the door
to one later (same Graph API, additional scopes, separate consent), but that is
a different project and out of scope here.

**Apple gives identity only.** There is no Apple data connector behind it —
iMessage, Apple Mail, Apple Calendar and Apple Health are all read locally
off-device-files today and need no account at all. Signing in with Apple changes
nothing about them.

### What the account system touches in this codebase

| Module | Change |
|---|---|
| `api/security.py` | **none.** The origin guard stays exactly as is |
| `account/` (new package) | identity, session, crypto, archive, restore |
| `api/routes/account.py` (new) | the local endpoints the UI calls |
| `tests/api_surface.json` | every new endpoint, **same commit** |
| `config.py` | Keychain: session token, device key, wrapped master key |
| `web/account.js` (new) + onboarding | the sign-in and restore screens |
| `hud.py`, `desktop.py` | reuse the existing sign-in card for the browser hop |
| `models/entitlements.py` | **do not touch.** Name collision — see below |

**The account token is not an API token.** It authenticates us to *our server*.
It must never be checked by the local origin guard; `api/security.py`'s
reasoning about why a local token buys nothing still holds.

**Name collision.** `models/entitlements.py` already means "which *provider*
models this user's third-party plan allows" (Claude Max vs Pro). It has nothing
to do with whether they paid us. Call the new one `account/plan.py` or
`licensing` — **never `entitlements`**.

---

## 2. The hard constraint that shapes the login flow

**Sign in with Apple forbids loopback redirect URIs.** Apple requires
registered `https://` Return URLs on a domain you have verified; `127.0.0.1`
and `localhost` are rejected. Google and Microsoft both permit loopback for
native apps — which is why `google_auth.py:92` can call
`run_local_server(port=0)` today.

So Apple alone forces a **server-mediated** flow. Rather than run two different
architectures, all three providers go through the same one. This is a
constraint, not a preference — do not plan around it.

Consequence: **the backend is required for login on day one.** There is no
zero-backend version of "sign in with Apple". (A licence *key* needs no server;
social login does. If you want identity before you want a server, licence keys
are the cheaper door — see AUTH-ANALYSIS §4.)

### The flow

```
 app                     system browser        auth.chitragupta.app        IdP
  │                                                     │
  │ 1. generate verifier + state, start loopback listener
  │────── open https://…/start?provider=apple&state&challenge&port ──────▶│
  │                                             │── 2. OAuth + PKCE ────▶│
  │                                             │◀── 3. code ────────────│
  │                                             │── 4. exchange (secret)▶│
  │                                             │◀── 5. id_token ────────│
  │                       6. verify sig/iss/aud/exp/nonce against JWKS
  │                       7. upsert account, mint one-time handoff code
  │◀── 8. redirect http://127.0.0.1:PORT/account/callback?code=…&state ──│
  │                                                     │
  │────── 9. POST /v1/auth/exchange {code, verifier} ───▶│
  │◀───── 10. session token + refresh token ────────────│
  │ 11. → macOS Keychain
```

Why a handoff **code** at step 8 rather than the token itself: a URL lands in
browser history, shell logs and crash reports. The code is single-use,
60-second TTL, and worthless without the PKCE verifier that never left the app.

**Step 1's port is the problem to watch.** The app's port is dynamic
(`desktop.py` picks one and binds it with `SO_REUSEADDR` — and
[`DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md) records that *"the webview origin is
user state"*, so it must not be disturbed). The callback listener must use the
app's **already-running** port and add a route, not open a second one. Every
loopback port the server will redirect to must be pre-registered with each IdP,
so register a fixed range (e.g. 8787 and a handful of fallbacks) and have the
app bind one of those, or terminate the redirect on the server and have the app
poll for the handoff code — the existing HUD already polls, which makes the
second option cheaper and port-independent. **Decide this before writing the
flow; it is the piece most likely to break under `--dev`.**

Reuse the existing sign-in card (`hud.py`, `web/signin_hud.html`,
`SIGNIN_TIMEOUT_SECONDS = 180`). It already handles "the user left for the
browser", offers **Cancel**, and never shows a dead spinner — all of which this
flow needs and none of which should be rewritten.

### Verifying the ID token (the part you must not get wrong)

For each provider, fetch and cache the JWKS, then check **all** of: signature,
`iss`, `aud` (your client id), `exp`/`iat`, and the `nonce` you generated. A
library that only decodes the payload — the way
`models/chatgpt_auth.py:101` deliberately does for *display* — is not
verification. That function's docstring says "unverified" for a reason; do not
reuse it here.

| | Stable id | Notes |
|---|---|---|
| Google | `sub` | `email` can change; check `email_verified` |
| Apple | `sub` | see below — two sharp edges |
| Microsoft | `tid` + `oid` | use `/common` for personal + work accounts |

**Two Apple edges that have burned everyone:**

1. **Name and email arrive only on the FIRST authorization.** Subsequent
   sign-ins return `sub` and nothing else. If you fail to persist them on that
   first callback, you cannot get them again — the user must revoke the app in
   their Apple ID settings. Persist on first contact, in the same transaction
   as the account row.
2. **Private relay.** Apple may return `…@privaterelay.appleid.com`. Mail to it
   forwards only while you are a registered sender. It is useless as a
   cross-provider identifier.

### Account linking — the security rule

**Never auto-link accounts by email address.** If a user signs in with Google
as `alice@example.com` and later with Microsoft as `alice@example.com`, those
are two accounts until *she*, while already signed in, explicitly links the
second. Auto-linking on email is an account-takeover vector: an IdP that does
not verify email ownership lets an attacker register the victim's address and
inherit their account. Apple's private relay makes email matching meaningless
anyway. The identity key is `(provider, subject)` — never email.

The cost is a real support case: *"I signed in with Microsoft and my agents are
gone."* Handle it in the UI, not by weakening the rule — on sign-in to an
account with no backup, show which providers *do* have data and offer to link.

---

## 3. What leaves the machine, and what never does

The core of your question. Four tiers, and the first one is absolute.

### Tier 0 — never leaves, no exception, not even encrypted

| | Why |
|---|---|
| Connector OAuth tokens (`google_token.json`) | a breach of our server would reach every user's Gmail, Drive and Calendar |
| Connector secrets in Keychain (`NOTION_TOKEN`, GitHub, Linear, Slack, Telegram) | same |
| Model provider API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) | the user's money |
| Connected-site browser cookies | a live session on sites the user signed into |
| The master key, the passphrase, the recovery code | the whole design rests on this |

**This is a deliberate, costly choice and it must be surfaced, not hidden.**
After restoring on a new Mac the user re-authorizes each connector — one OAuth
tap each — and re-enters provider keys. §5 step 6 makes that a checklist rather
than a surprise. The alternative is holding credentials that reach into
hundreds of users' inboxes, which is a liability no backup feature is worth.

### Tier 1 — our server, plaintext, and this is the complete list

```
account      : id, created_at, last_seen_at
identity     : account_id, provider, subject, email, email_verified, linked_at
plan         : account_id, status, plan, current_period_end, processor_customer_id
device       : id, account_id, label, os_version, app_version, activated_at, last_seen_at
backup_meta  : account_id, generation, bytes, schema_version, kdf_params,
               wrap_version, created_at
```

That is all of it. Deliberately **not** collected, as a rule with teeth:

> memory counts · entity or agent names · persona text · the connector list ·
> which model providers are configured · per-provider usage · prompts or
> completions · measurement values · automation names

Because **the shape of the metadata is itself personal.** "This account has the
Telegram and Apple Health connectors, 14 agents and 60k memories" describes a
person's life, and once collected it is subpoenable, breachable and tempting to
build a dashboard on. The `device.label` is the one judgement call — it is
user-typed and user-visible, needed so a device list means something.

### Tier 2 — our server, ciphertext we cannot read (opt-in, paid)

The brain archive: the SQLite database minus Tier 0, encrypted on-device. We
hold bytes, a size and a generation number. Contents: all 44 tables —
memories, custom agents, personas, avatars, tool and connector grants, action
permissions, automations, routines, tasks, reminders, **measurements and
lifts**, the canonical claims/entities/evidence layer, browser origin grants.

### Tier 3 — stays local, by choice, because it is derived

Embeddings and vector caches (re-computed on restore — the existing export
already omits them), the enrichment queue, logs, the saved webview port.

---

## 4. Recovery: the cryptographic design

**The central problem: social login gives you authentication, not a key.**
"Sign in with Google" means Google vouches for you. It does not produce a secret
only you know, so it cannot, by itself, protect data from us. Zero-knowledge
backup needs a secret the server never sees, and OAuth cannot supply one.

So identity and encryption are separate, and the user holds one secret.

### Key hierarchy

```
passphrase ──scrypt(N=2^17,r=8,p=1,32B salt)──┐
                                              ├─ wraps ─▶ MK ─ wraps ─▶ DEK ─▶ archive
recovery code ──HKDF-SHA256────────────────────┘           (random 256-bit, on-device)
device key (macOS Keychain, this Mac only) ────┘
```

- **MK** — master key, 256-bit, generated on-device when backup is enabled.
  **Never transmitted.**
- **DEK** — a fresh data key per backup generation, wrapped by MK. Lets you
  rotate or expire a generation without re-encrypting history.
- Server stores the archive plus **wrapped copies of MK** — one per unlock
  method. It can unwrap none of them.
- The **device wrap** lives in the Keychain only, never server-side: it is what
  makes the same Mac stop asking.

**Primitives.** `hashlib.scrypt` is in the Python stdlib and is memory-hard, so
no Argon2 dependency is needed. AES-256-GCM (or XChaCha20-Poly1305) needs
`cryptography` — **one** new base dependency, which matters for a 122 MB `.dmg`
(`DISTRIBUTION.md`). Chunk the archive and authenticate each chunk plus a
manifest hash over the whole, so a truncated download fails loudly rather than
restoring half a brain. Store `kdf_params` server-side so parameters can be
raised later without orphaning old backups.

### Unlock paths

| Situation | Path |
|---|---|
| Same Mac, Keychain intact | silent |
| New Mac, remembers passphrase | scrypt → unwrap |
| New Mac, lost passphrase | recovery code → unwrap |
| Neither | **unrecoverable. We cannot help.** |

That last row is what zero-knowledge *means*, and it is the one thing users do
not expect. So:

- The recovery code is generated, displayed **once**, and backup cannot be
  enabled until the user has copied or saved it.
- The warning is a sentence in plain language next to a checkbox, not buried in
  terms: *"If you lose both your passphrase and your recovery code, your backup
  cannot be opened by anyone, including us."*
- Re-display is impossible by construction; offer **rotate** instead (new code,
  re-wrap MK, invalidate old).

### Backup is append-generation, never a mirror

**A mirror destroys the backup it is meant to be.** `POST /api/brain/reset`
wipes the brain locally; a mirroring sync would faithfully propagate that and
the user's only copy is gone. Ransomware, a corrupted file or a mis-tap does
the same.

So: each backup writes a **new generation**, and generations are retained (a
rolling window — 30 days or 10 generations, whichever is more generous).
Restore defaults to the newest and lets the user pick an older one. The storage
cost is real and bounded; the alternative is a backup that cannot survive the
accident it exists for.

### Backup is not sync — hold this line

v1 is **disaster recovery and machine migration**: one archive, restore onto a
fresh Mac. It is explicitly **not** live multi-device sync. Two Macs editing one
brain is a merge problem (per-row causality, CRDTs, conflict UI) that is a
separate product and an order of magnitude more work.

v1 behaviour with two Macs: one device is the backup writer; a second signing in
is offered **restore**, and is warned before it becomes the writer. Generations
mean a wrong answer loses nothing permanently. Say "Backup" in the UI, never
"Sync", or every user will assume the harder thing.

---

## 5. The restore flow, end to end

What a user does on a brand-new Mac after a disaster. This is the acceptance
test for the whole project.

1. Install the `.dmg`, launch. First-run screen offers **Restore from backup**.
2. Sign in (Google / Apple / Microsoft) → identity, which finds the account.
3. Fetch `backup_meta`. Show generations with dates and sizes; user picks one.
4. Prompt for **passphrase or recovery code**. Derive, unwrap MK, unwrap DEK.
5. Download chunks, verify each AEAD tag and the manifest hash, decrypt,
   decompress.
6. Write the SQLite file, run the existing idempotent `run_migrations()`,
   re-embed in the background with progress (CLAUDE.md: *"Long work is a
   background job with progress that survives a refresh"*).
7. **Reconnect checklist** — the honest cost of Tier 0. One screen listing every
   connector the restored brain references, each with a Connect button, plus any
   model provider keys to re-enter. This is the step that must not feel like a
   failure: the agents, memories, permissions and measurements are all back; the
   doors to other people's services need re-opening, by design.
8. Done. The user's agents, personas, tasks and a year of measurements are
   there.

Step 7 is where this design is honest instead of magical, and it is worth
writing the copy for carefully.

---

## 6. The server

Small on purpose. Eight endpoints.

```
POST   /v1/auth/start            → IdP redirect (server holds client secrets)
POST   /v1/auth/exchange         → {handoff code, PKCE verifier} → session + refresh
POST   /v1/auth/refresh          → rotate session token
GET    /v1/account               → plan, status, identities, devices
POST   /v1/devices               → activate this device (enforces the limit)
DELETE /v1/devices/{id}          → deactivate
PUT    /v1/backup                → upload a generation (chunked, resumable)
GET    /v1/backup/{generation}   → download
GET    /v1/backup                → list generations
POST   /v1/billing/webhook       → processor → plan status
```

- **Stack:** FastAPI + Postgres matches the skills already in this repo.
  Object storage for blobs — Cloudflare R2 has no egress fees, which for a
  *restore* feature (rare, large downloads) is the difference between tolerable
  and painful.
- **Session tokens:** short-lived access (~1h) + rotating refresh, so a stolen
  access token expires. **Refresh must tolerate long offline periods** — a
  user back from three weeks away must not lose local access, because
  local access never depended on the token in the first place.
- **Billing:** a merchant-of-record processor (Paddle / Lemon Squeezy) handles
  worldwide VAT/GST, which is the genuinely hard part of selling solo
  internationally. Verify current terms directly.
- **Rate-limit `/v1/auth/*` and `/v1/backup`** per account and per IP.
- **Log no brain content, ever.** Not in request bodies, not in error traces.
  Tier 1 is the exhaustive list of what may be written down.

---

## 7. Offline behaviour — the invariant that keeps this local-first

Non-negotiable rules for the client:

- **No local feature ever checks for a session.** Chat, recall, agents,
  automations, connectors and the browser all work signed out, offline,
  forever.
- **The plan verdict is cached in the Keychain with a long grace period** (14–30
  days) and **fails open**. A paid user on a plane keeps their paid features. A
  product that bricks itself when the network drops is a worse bug than an
  unpaid install.
- **Backups queue when offline** and upload when there is a network, with
  progress that survives a refresh.
- **Sign-out is local-only** — it drops our session token and the device wrap.
  It must not touch connector credentials, provider keys, or a single row of
  the brain. This is the existing independence rule applied to the new system.

---

## 8. Phasing

Each phase is independently shippable. **Phase 1 ships before any server
exists** and is the one users are hurt by today.

**Phase 1 — local archive, no account, no backend. ✅ SHIPPED.**
The format, Tier 0 stripping, `VACUUM INTO` snapshotting, encryption, restore +
`run_migrations()`, the reconnect checklist, a native file picker, and a timer
that writes one without being asked. **Everything hard about the data is solved
here**, and solved without a privacy surface — Phase 3 only adds transport.

Two things learnt building it, both worth carrying into Phase 3:

* **The wraps are not secrets.** Storing the master key wrapped under the
  passphrase is what lets an *unattended* backup still be openable by that
  passphrase without it existing on disk. The same fact is what will let a
  hosted backup be written on a timer.
* **It is eleven SQLite files, not one** — the reason the old export missed
  nine of them. Anything that reasons about "the database" is wrong here.

**Phase 2 — identity.** The server, the three IdPs, PKCE, JWKS verification,
session tokens, the device list, account linking. No data uploaded yet.
Shipping this alone gets you a real user count and a mailing list.

**Phase 3 — hosted backup.** Chunked upload, generations and retention, the
restore picker. Phase 1 already produced the bytes.

**Phase 4 — plans and billing.** The processor, the webhook, device limits, the
cached grace period.

Phase 1 before Phase 2 is the whole argument of this document: the recovery gap
is real today, and it does not need an account to fix.

---

## 9. Threat model — what this does and does not stop

| Threat | Covered? |
|---|---|
| Lost / stolen / dead Mac | **Yes** — that is the point |
| Our server breached | **Yes** for Tier 2 (ciphertext, no keys). Tier 1 leaks the email + plan |
| Us reading user brains | **Yes** — by construction, not by policy |
| Rogue employee | **Yes** for brain content; Tier 1 is readable |
| Account takeover via IdP compromise | **Partly** — they reach Tier 1 and the ciphertext, but **not the plaintext**: the passphrase is not in the IdP. This is the main reason to split identity from encryption |
| Local malware as the user | **No.** It can read the brain, the Keychain and the live app. Out of scope for any backup design |
| User loses passphrase + recovery code | **No, by design.** The honest cost of zero-knowledge |
| Subpoena for content | **Cannot comply** for Tier 2. Can for Tier 1 |

That IdP-compromise row is worth re-reading: it is precisely why "just use
Google login to unlock the backup" is the wrong design, and why the extra
passphrase step — which users will complain about — is load-bearing.

---

## 10. Legal and policy, which now actually apply

Holding user data, even ciphertext, starts obligations the current product does
not have. None are hard; all are embarrassing to retrofit.

- A **privacy policy** stating Tier 1 exhaustively and that Tier 2 is
  unreadable to us. The tier tables above are the source text.
- **GDPR / India DPDP**: export (the archive *is* the export) and delete
  (purge Tier 1 + all generations; it must actually delete, with a stated
  window).
- **Breach notification** — have the runbook before you need it.
- **Sub-processors** disclosed: hosting, object storage, payment processor.
- **Apple Developer Program** is already paid ($99/yr, per
  `DISTRIBUTION.md`), which Sign in with Apple requires.
- The **README's privacy claims change** the moment this ships. *"No cloud copy,
  no account, no analytics"* becomes conditional. Rewrite it honestly in the
  same release — a privacy-branded product caught overstating its privacy loses
  more than the feature gained.

---

## 11. Tests this must ship with

CLAUDE.md: a bug fix ships with a test you watched fail. For new work the
equivalent is a test per invariant above, asserting **the shape, not the
instance**.

- Every new endpoint added to `tests/api_surface.json`, same commit.
- **A Tier 0 leak test**: construct a brain with connector tokens and provider
  keys, build an archive, assert none of those bytes appear in it. Parametrised
  over every Tier 0 item, so a connector added later is covered automatically.
- **A Tier 1 schema test**: the server's tables contain no column outside the
  §3 list. Fails when someone adds `memory_count`.
- **Round-trip**: 44 tables in, archive, restore, 44 tables out, byte-identical
  where it should be. Plus the companion test that **fails when a new table is
  added without an export path**, so this cannot rot back to 1-of-44.
- **Offline**: with the network stubbed to fail, every local feature works and
  no code path blocks on a session.
- **Signing out** leaves connector credentials and the brain untouched.
- **ID token verification** rejects: bad signature, wrong `aud`, wrong `iss`,
  expired, replayed `nonce`. Table-driven over all three providers.
- **No auto-link**: two identities with the same verified email stay two
  accounts.
- **Apple first-authorization**: name/email persisted on first callback,
  survives a second sign-in that omits them.
- **Wrong passphrase** fails cleanly; a truncated or tampered chunk fails
  loudly, never half-restores.
- **Reset does not propagate**: wipe locally, back up, assert prior generations
  are still restorable.

---

## 12. Decisions needed from you

Numbered because each one changes the schema or the build order.

1. **Is sign-in mandatory or skippable?** §0. Mandatory needs a CLAUDE.md edit
   and gives up the offline-first promise. Recommend skippable.
2. **Phase 1 first, or identity first?** Recommend Phase 1 — it fixes the real
   gap with no server.
3. **Device limit?** Any number above 1 requires server-side activation
   tracking, which is already in the §6 plan but must be stated for pricing.
4. **Is backup free or paid?** If free, storage is a fixed cost per install
   forever. If paid, free users still need Phase 1's local archive — which is
   another argument for building it first.
5. **Retention window** for generations — 30 days / 10 generations is the
   proposal. Drives storage cost.
6. **Passphrase in addition to social login — accept the friction?** It is what
   makes the zero-knowledge claim true (§9). The alternative is server-held
   keys, which contradicts the motto. Recommend keeping it, with the recovery
   code as the escape hatch.
7. **Loopback callback or server-side polling?** §2. Polling reuses the existing
   HUD and dodges the dynamic-port problem; recommend it.
8. **One new dependency (`cryptography`) acceptable** in the base install, given
   the 122 MB `.dmg`?

---

## See also

- [`AUTH-ANALYSIS.md`](AUTH-ANALYSIS.md) — the three systems, prior art, and why
  recovery is not an account problem
- [`api/security.py`](../chitragupta/api/security.py) — why the local API has no
  token, and why this does not change that
- [`DESKTOP-SIGNIN.md`](DESKTOP-SIGNIN.md) — the sign-in card to reuse
- [`DISTRIBUTION.md`](DISTRIBUTION.md) — signing, notarisation, the missing
  updater
- [`CONTEXT.md`](CONTEXT.md) §7 — the privacy principle this must not break
