# Authentication, identity and recovery — an analysis

**Status:** analysis, not a decision. Nothing here is implemented.
**Written:** 2026-10-05. **Mode:** INVESTIGATE — no code was changed.

The question that prompted this: *does Chitragupta need an authentication
system, how do we know who our users are and how many, and how does anyone
recover their data?*

The short answer is that **"authentication" names three different systems
here**, they have three different answers, and the expensive mistake is
building them as one.

---

## 1. Three systems, three answers

| | What it is | Needed? |
|---|---|---|
| **A. Local API auth** | A token on `127.0.0.1` so only our page can call our API | **No.** Already solved better |
| **B. Licensing identity** | Who paid, what they may run, until when | **Yes, the moment we charge** |
| **C. Sync identity** | An account holding brain data for backup across devices | **Optional, and the costly one** |

Conflating B with C is the trap. B needs a few hundred bytes about a purchase
and no user data at all. C needs the user's brain on our infrastructure, which
is the one thing [`docs/CONTEXT.md`](CONTEXT.md) §7 says we do not do. Shipping
them together means the cheap, necessary one waits on the expensive,
philosophically hard one.

**And neither B nor C is how recovery gets fixed.** See §6 — recovery is a local
problem with a local fix, and coupling it to an account makes it worse.

---

## 2. What exists today

Verified by reading the code, not the docs.

**No app-level identity of any kind.** `user_id`, `tenant`, `password_hash`,
`bcrypt`, `passlib`, `current_user` — zero hits across `chitragupta/`. Every
JWT reference decodes a *vendor's* token (`models/chatgpt_auth.py`,
`models/xai_auth.py`). None of the **44 SQLite tables** carries an owner column.

**No backend of ours exists.** The `subscription` provider
(`models/registry.py:32`) sounds like a gateway we run; its default is
`http://localhost:8080/v1` — the user's own proxy.

**No telemetry.** Searched for PostHog, Segment, Mixpanel, Amplitude, Sentry,
Datadog, GA. Every hit was an MCP catalog entry (the user's *own* Sentry account
as a data source) or an SVG icon path. The README's *"no cloud copy, no account,
no analytics"* is true as written.

**Local API auth is deliberately absent, and correctly.**
[`api/security.py`](../chitragupta/api/security.py) argues it:

> **Deliberately not a token.** Against a *process* on the machine a token buys
> nothing: it would have to live somewhere every process running as the user can
> read. Against a *browser* it would buy something — but only what the
> `Origin`/`Host` comparison above already buys.

That reasoning stands. The origin guard compares `Origin` to the `Host` it
arrived on, which defeats DNS rebinding and CSRF without a shared secret. **System
A is finished. Do not revisit it.**

### A name collision to know about

`models/entitlements.py` already says "entitlement" and means something else
entirely: which *provider* models a user's third-party plan allows them to run
(Claude Max vs Pro, ChatGPT Plus vs Free). It has nothing to do with whether
they paid *us*. A licensing module must not reuse the word — call it
`licensing` or `plan`, never `entitlements`.

---

## 3. "How do I run an app without an account?"

The app already does, today, and that is not a gap — it is the product working
as designed. Launch it on a fresh Mac with no keys and no network and it opens,
generates agent faces from their ids, and answers through the offline `mock`
provider. CLAUDE.md requires exactly this:

> **Assume nothing is installed and nothing is configured.** First launch, no
> keys, no CLIs, no accounts — it must still open and explain itself.

What makes it work is that **every identity the app needs belongs to someone
else.** Google OAuth for Gmail, a Notion integration secret, the user's own
Claude subscription through their CLI. Chitragupta is the client; the accounts
are the user's, held in their Keychain. There is no "our user" in the data model
because there is no row that needs one — the machine *is* the tenant.

So the real question is not "how does the app run" but **"how does the business
run"**, which is §4.

---

## 4. Finding users, and charging them

This is where the earlier framing was wrong, and the pushback is correct: **you
cannot sell a subscription to an anonymous install.** A subscription is a
promise that expires, and something has to know whether it has. That requires
identity — system B.

### What you can know today: nothing

DMG downloads, and that is all. Downloads count downloads, not installs, not
active users, not retention. You cannot answer "how many people use this", "did
last month's release help", or "how many are on Ollama vs Claude". For a product
decision like *"is the browser feature worth maintaining"*, that is the number
you would want and do not have.

### The honest place to get a user count

[`docs/DISTRIBUTION.md`](DISTRIBUTION.md) §Updates:

> **Updates.** A `.dmg` has no update mechanism. Every new version is a fresh
> download.

That is a real product problem independent of any of this — users are stranded
on whatever version they first installed, and there is no way to ship them a
security fix. **Fixing it solves the counting problem as a side effect.**

An updater (Sparkle is the macOS standard) periodically fetches an appcast feed
from our server to ask "is there a newer version?" That request carries the app
version and the OS version, and nothing else. Count the distinct installs that
ask, and you have **daily and monthly active users, version adoption, and
macOS-version spread** — derived from a feature users actively want, not
telemetry bolted onto the side.

This is the cheapest honest answer and it should come first. Two caveats, both
non-negotiable:

- Users must be able to turn the update check off, and still get the app.
- The moment it ships, the README's *"no analytics"* becomes arguable. Change
  the sentence in the same commit — say plainly that the app checks for updates
  and what that request contains. **Do not ship the ping and keep the
  sentence.**

### Licensing: you probably need less backend than you think

A license key *is* an identity. It does not need a password, a session, an email
confirmation flow, or a `users` table you operate.

**Perpetual licence, zero backend.** Generate a key that encodes the purchase
(email, product, issue date) and sign it with an Ed25519 private key. The app
embeds the public key and verifies offline, forever, on a plane. This is what
Sublime Text and Keyboard Maestro do. Cost: no server, no uptime risk, no
privacy surface. Limit: an offline-verifiable key cannot be revoked or
device-counted, and cannot express "expires if they stop paying".

**Subscription, minimal backend.** A recurring charge needs a periodic online
check, so something must be reachable. But a merchant-of-record platform —
Paddle, Lemon Squeezy, or similar — already issues license keys, validates and
activates them against a device limit, and handles the part that is genuinely
hard for a solo developer selling worldwide: **VAT/GST registration and
remittance across dozens of jurisdictions.** Stripe is excellent for payments
but leaves you as the merchant of record, so the tax burden stays yours. Verify
current terms and fees directly before committing — these change.

The point: **the licensing backend can be a vendor's.** Our side is a few
hundred lines — call validate, cache the verdict with a generous grace period so
a network blip never locks a paying user out of their own local data, and fail
*open* toward the user. A paid app that bricks itself offline is a worse bug than
an unpaid install.

**Mac App Store is effectively ruled out**, worth stating because it would
otherwise be the obvious answer — Apple becomes your account system, handles
subscriptions, tax and family sharing, and you never hold a credential. But the
App Store requires sandboxing, and Chitragupta reads `~/Library/Mail` and the
iMessage `chat.db`, spawns vendor CLIs, and drives a Playwright browser. The
sandbox forbids most of that. The connectors that make the product what it is
are the reason this door is closed.

### What identity to use

Rank by how little you hold:

1. **Licence key alone.** The key is the identity. No email, no password, no
   account page. Recovery = we re-send the key to the purchase email, which the
   payment provider already holds.
2. **Key + email**, for receipts and key recovery. Still no password, still no
   session.
3. **Full account with credentials.** Only if system C ships, because only
   stored user *data* needs an authenticated session in front of it.

Going straight to 3 means operating password resets, session management, and
breach liability to solve a problem that 1 solves. 1Password went from licence
to mandatory account and spent years absorbing the backlash; it is worth knowing
that is the direction users resent, and it is hard to walk back.

---

## 5. "Is there any good app without an account system?"

Yes — it is the normal shape for paid local-first Mac software, and two of them
are near-exact analogs of Chitragupta.

**The two to study, because they are this product's business model already:**

- **Obsidian.** Local-first markdown, free for personal use, **no account
  required to use it at all**. Obsidian *Sync* and *Publish* are paid
  subscriptions that need an account — the account exists only because those
  features hold your data. Core app: no identity. This is precisely the
  A/B/C split in §1, shipped and profitable.
- **Raycast.** Free, no account, fully local. Raycast Pro requires an account
  because it is a hosted AI service. Same split.

**Paid, licence key, no account, for years:** Sublime Text · Keyboard Maestro ·
Little Snitch · BBEdit · Transmit · DaisyDisk · Arq (you bring your own storage)
· Beyond Compare.

**Account-free *sync*, which is the interesting one for §6:**

- **Syncthing** — devices pair by exchanging device IDs and sync peer-to-peer.
  No account, no server, no company holding anything.
- **Cryptomator** — end-to-end encrypted vaults on the user's *own* cloud
  storage. No account with the vendor.
- **Arq** — paid backup software pointed at storage the user owns.

**Local-first with optional paid sync:** Logseq · Zotero · Things 3 (local app,
Things Cloud account only for sync).

**Zero-knowledge accounts**, if system C ever ships: Standard Notes and 1Password
both hold user data while being unable to read it. That is the only account
design compatible with CLAUDE.md's promises, and §7 covers what it costs.

The pattern across all of them: **the account appears exactly when, and only
when, the vendor starts holding the user's data.** Not before. Chitragupta holds
nothing today, which is why it needs no account today — and would need one the
moment it offered to store a brain.

---

## 6. Backup and recovery: real gap, wrong fix

You are right that recovery is broken. You are right that an account would
provide it. It is still the wrong thing to build first, for one reason:

**Coupling recovery to an account means free users have no recovery path.** That
is today's bug with a price tag attached, and today's bug is severe:

`GET /api/brain/export` → `brain.export()` → `store.export_all()` returns
**memories only — 1 of 44 tables.** `/api/brain/canonical/export` is explicitly
*"a projection, never a second source of truth"* (`brain/canonical/export.py`),
so it reads but does not restore.

Lost on a new Mac today, despite the README promising "you own your data":

custom agents and personas · agent avatars · every tool and connector grant ·
action permissions and approval history · all automations, routines and run
history · tasks, reminders, scheduled actions · **all health measurements and
training lifts** · the canonical claims/entities/evidence layer · browser origin
grants · connector sync checkpoints

The measurements loss is the sharpest, because CLAUDE.md says *"readings
accumulate"*. A year of body-weight readings is exactly what a trend needs and
exactly what cannot be reconstructed from any other source.

### The fix is local, and it is small

It is all **one SQLite file plus a Keychain**. A whole-home backup is far less
work than 44 per-table exporters:

1. **Export** — the SQLite file (`VACUUM INTO` for a consistent copy without
   stopping the app), plus a manifest with the schema version. Secrets stay out
   by default; offer them as a deliberate, separately-confirmed opt-in, because
   a backup file that silently contains live OAuth tokens is a new hazard.
2. **Import** — restore, run the existing `run_migrations()` (already
   idempotent and version-aware), re-embed.
3. **One test** that fails if a new table is added without an export path, so
   this cannot rot back to 1-of-44.

That ships with **no account, no server, no subscription, and no privacy
question** — and it is needed regardless of what you decide about §4. Then
"keep the file in iCloud Drive or Dropbox" gives cross-device recovery for free,
using storage the user already pays for and already trusts.

An uninstall story belongs with it: there is currently no uninstaller and no
documentation of where the data lives, so a user who wants their data *gone*
cannot find it. One paragraph in the README naming `~/Library/Chitragupta` and
the Keychain item, plus a "Delete everything" button in the app.

---

## 7. If you do build the sync account (system C)

Only two designs are compatible with *"data on the user's machine; nothing leaves
it except the model call they chose"*:

**Encrypted blob storage, zero-knowledge.** The client encrypts with a key
derived from a passphrase only the user holds; the server stores ciphertext it
cannot read. We hold a blob, a key id, and a size. Honest cost, which must be
said at signup and not buried: **if the user loses the passphrase, the data is
unrecoverable and we cannot help.** That is what zero-knowledge means. Standard
Notes and 1Password both make this trade and both have to explain it constantly.

**Device-to-device, no server.** Syncthing's model — devices pair directly, we
store nothing, there is no account at all. Best fit for the motto, and it does
not cover the case the user actually asked about: a Mac that is *gone*, with no
second device to have synced to.

Either way, system C is where real liability starts: encrypted user data on
infrastructure you operate, breach notification duties, and a GDPR/DPDP posture
the current product simply does not have. Ship B first, learn whether anyone
pays, then decide.

---

## 8. Recommendation

Four phases. Each is independently useful and shippable, and none blocks on the
next.

**Phase 1 — now, no backend.**
- Full local backup/restore (§6). The biggest real gap, zero privacy cost.
- Fix `connectors/google_auth.py:93` — it writes `google_token.json`, a live
  OAuth refresh token for Gmail/Calendar/Drive, with **no `chmod`**, while
  `config.py:204` correctly sets `0600` on `secrets.json`. Same class of secret,
  two protections. Fix it where "we write a credential to disk" is decided, not
  in that one file.
- Uninstall documentation + a "Delete everything" button.
- Disclose cloud recall at the moment of choosing: switching to a cloud provider
  or starting enrichment on one should say once what that sends.
  `_LOCALITY` already holds everything needed.

**Phase 2 — the update channel.** Sparkle + an appcast. Fixes stranded users,
gives you a security-fix delivery path, and yields anonymous DAU/MAU and version
adoption as a side effect. Opt-out, and amend the README's "no analytics" line in
the same commit.

**Phase 3 — licensing, when you charge.** Licence key as identity. Perpetual
keys verify offline with an embedded public key and need no server at all; a
subscription uses a merchant-of-record platform's validation API so the backend
is theirs, not yours. Cache the verdict with a long grace period and fail open.
Name the module `licensing`, never `entitlements`.

**Phase 4 — sync account, only if demanded and paid for.** Zero-knowledge or
nothing.

### On "can't we build the account backend now?"

You can, and I would not. Not because it is hard — a licence table and a Stripe
webhook is a weekend — but because **the backend is the cheap part and the
commitment is the expensive part.** Running auth means uptime obligations, breach
liability, and a privacy story that currently needs no defending because there is
nothing to defend. You would also be building it before knowing which plan people
buy, how many devices one licence should cover, or whether sync is what they'd pay
for — and every one of those answers changes the schema.

Phase 1 is strictly larger value for strictly less risk, and it is the one your
users are hurt by *today*. Phase 2 is what tells you whether there is a user base
to monetise at all. Build those, then write Phase 3 against real numbers rather
than guesses.

---

## 9. Open decisions — yours, not the code's

1. **Perpetual licence or subscription?** Perpetual can ship with no server.
   Subscription needs an always-reachable validator and a grace-period policy.
2. **Free tier?** If yes, where is the line — connector count, agent count,
   automations? This is the schema's first real constraint.
3. **Is sync a product or a feature?** Obsidian charges separately for it. If it
   is bundled, system C blocks launch; if separate, it never does.
4. **Does the update check default on?** Affects the README's privacy claim and
   therefore the brand.
5. **Device limit per licence?** Any number above one requires server-side
   activation tracking, which rules out the zero-backend option.

---

## See also

- [`api/security.py`](../chitragupta/api/security.py) — why local API auth is
  deliberately absent
- [`docs/CONTEXT.md`](CONTEXT.md) §7 — the privacy principle this must not break
- [`docs/DISTRIBUTION.md`](DISTRIBUTION.md) — the shipping channel and the
  missing updater
- [`models/entitlements.py`](../chitragupta/models/entitlements.py) — the
  existing, unrelated meaning of "entitlement"
