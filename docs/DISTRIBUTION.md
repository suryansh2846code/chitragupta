# Shipping Chitragupta as a `.dmg`

> How a Mac you have never touched ends up running this app, and what each step
> exists to prevent. Written after building it: the numbers below are measured
> on an Apple Silicon Mac, not estimated.

## Are we going in the right direction?

Partly. `scripts/build-macos-app.sh` was the right instinct at the wrong
altitude. It writes a real `Chitragupta.app` with a real `Info.plist` — but its
launcher is three lines that `cd` into the source checkout and run
`.venv/bin/chitragupta app`. There is no Python inside that bundle. Copy it to
another Mac and it fails immediately, because the thing it points at does not
exist there.

That script is still useful, and it has been relabelled rather than replaced:
it is the fastest way to get an icon in `~/Applications` while developing.

The distributable path is `scripts/build-dmg.sh`, which is four steps. Each one
is load-bearing, and skipping any of them produces something that looks finished
and is not.

```
  1. Bundle    PyInstaller → interpreter + deps + web assets inside the .app
  2. Sign      Developer ID Application + Hardened Runtime + entitlements
  3. Notarise  upload to Apple, get a ticket back
  4. Staple    attach the ticket so first launch works offline
```

## Why each step

**1. Bundle.** The user has no Python, no `uv`, and no checkout. PyInstaller
puts a CPython and every dependency in `Chitragupta.app/Contents/Frameworks`, and
`chitragupta/web` in `Contents/Resources` where `api/assets.py::WEB` finds it.

The interesting decision is what to leave out. `sentence-transformers` pulls
torch, and the environment used to build this one has it installed: **1.1 GB of
venv for a feature that is off by default**, since the default embedder is the
pure-Python `hash` one. The spec excludes it, and the difference is the whole
download:

| | size |
|---|---|
| `Chitragupta.app` (torch excluded) | **321 MB** |
| `Chitragupta-0.1.0-arm64.dmg` (compressed) | **122 MB** |
| of which Playwright | **130 MB** (116 MB arm64 node + 14 MB driver JS) |
| the same bundle with torch | ~2 GB |

A user who wants local embeddings installs the extra into a source checkout.
That is the right trade for a download page.

### Playwright is in the bundle, and that was not a decision anybody made

The browser feature arrived with `docs/BROWSER.md` saying the `.dmg` "does not
yet know about Playwright" and framing the runtime Chromium download as what
keeps the image small. Measured on the first build after the merge, that is no
longer true: **the app went 189 MB → 321 MB and the image 75 MB → 122 MB**,
because `browser/driver.py` spells `from playwright.sync_api import ...`
statically inside a function and PyInstaller's bytecode analysis follows those.

It works — the bundled driver answers `Version 1.63.0` and `/api/browser/status`
reports `drivable: true` — so a `.dmg` user really can use the browser. The user
pays **twice**: 122 MB to download the app, then ~150 MB of Chromium on first
use.

**Decided 2026-09-17: keep it** (DECISIONS.md → P1). A feature that only exists
for people with a git clone is not a feature of the product. `playwright` is now
named in the spec's `HIDDEN` list. It was
reaching the bundle by accident of static analysis, and one refactor of that
import line would have dropped 130 MB of driver with a green build, a running
app, and a browser that fails only after the user downloads Chromium.

**2. Sign.** Notarisation refuses anything not built with the Hardened Runtime,
and the Hardened Runtime kills CPython on launch unless three exceptions are
granted. They are in `packaging/entitlements.plist` with the reason for each;
the short version is that CPython maps writable-executable memory, and the
bundle loads `.so` files that were not signed by us.

Signing is **inside-out**: every nested `.dylib` and `.so` first, then the
bundle. One pass over the `.app` leaves the nested binaries unsigned and Apple
rejects the entire upload for any one of them.

**3. Notarise.** Without a ticket, Gatekeeper tells the user the app *"cannot be
opened because Apple cannot check it for malicious software"*.

> **The way through changed, and got worse.** It used to be right-click → Open.
> **Apple removed that bypass in macOS 15**, so on any current Mac the only
> route is: open the app and let it be blocked, then go to
> **System Settings → Privacy & Security**, scroll to the "Chitragupta was
> blocked" line, click **Open Anyway**, and open the app again. Anything still
> telling a tester to right-click strands them on a dialog whose only button is
> *Done*. This file and `scripts/build-dmg.sh` both said it until 2026-09-16.

For this product that is not a minor rough edge — it is precisely the
instruction `CLAUDE.md` says never to give:

> **Never ask the user to open a terminal.** … If a path cannot succeed, say
> why, in the place the user is looking.

An app whose very first interaction is a scary dialog and a workaround has
already failed the standard the rest of the codebase is held to. **Notarisation
is not optional for a shipped Chitragupta.**

**4. Staple.** The ticket is fetchable online, so an unstapled build usually
works — until someone installs it on a plane. Stapling attaches it to the `.dmg`.

## What you need

| | |
|---|---|
| Apple Developer Program | **$99/yr** — there is no free path to notarisation |
| A *Developer ID Application* certificate | in the login keychain |
| A notarytool profile | `xcrun notarytool store-credentials chitragupta-notary --apple-id you@example.com --team-id TEAMID --password <app-specific-password>` |

An **app-specific password** is generated at appleid.apple.com, not your Apple
ID password.

## Running it

```bash
uv pip install pyinstaller
./.venv/bin/python packaging/make-icon.py   # once, or after changing the art
./scripts/build-dmg.sh              # signed + notarised + stapled
./scripts/build-dmg.sh --unsigned   # a real .dmg, but Gatekeeper will warn
```

The output is named for the architecture it was built on —
`Chitragupta-0.1.0-arm64.dmg` — because that is the only machine it runs on and
two identically-named images on a download page is not a mistake you can take
back.

An `--unsigned` image also carries a **READ ME FIRST.txt** next to the app, with
the Open Anyway steps. The dialog is unavoidable without a certificate; meeting
it with no explanation is not.

The script preflights everything it needs and tells you exactly which command
fixes what is missing, rather than failing halfway through a five-minute build.

### The smoke test is part of the build

After bundling and before signing, the script launches the bundled binary, waits
for it to write `~/Library/Chitragupta/.port`, and requests `/api/sync/status`.
A PyInstaller bundle that is missing a hidden import looks perfectly well-formed
and dies on launch; this catches that in fifteen seconds instead of in a user's
Downloads folder. It is the same reason the frontend has executed harnesses
rather than `node --check`.

## macOS permissions this app actually needs

These are the ones that will surprise you, because nothing in the build catches
them — they only appear on someone else's Mac.

**Full Disk Access** — `connectors/imessage.py` reads
`~/Library/Messages/chat.db` and `connectors/apple_mail.py` reads
`~/Library/Mail`. macOS protects both. A signed, notarised app still gets
`PermissionError` until the user grants Full Disk Access in
System Settings → Privacy & Security. This cannot be requested by a prompt; the
user has to do it by hand.

**Built, as of 2026-09-16.** `connectors/permissions.py` owns the sentence and
the pane URL; Messages, Apple Mail and Apple Calendar all raise it and set
`fix = "full_disk_access"`, which `/api/connectors` passes through so the row can
render an **Open Settings** button. Previously all three told the user to grant
access to *"your terminal/app"* — a thing a shipped `.app` does not have.

The pane is opened by `desktop.py::_AppBridge.open_privacy_settings`, **not** by
`/api/open-browser`. That endpoint accepts `http(s)` only, deliberately, because
handing the system opener an arbitrary scheme reaches any URL handler any
installed app registered. The bridge method takes **no argument** — the URL is a
module constant — so the guard did not have to move. The button is withheld in a
browser tab, where there is no bridge behind it; the sentence still says where
to go by hand.

**Automation / Apple Events** — anything driving Notes or Calendar through
AppleScript triggers a consent prompt, which needs
`NSAppleEventsUsageDescription` in `Info.plist`. It is set in the spec; the
string it contains is the entire explanation the user gets, so it says what
Chitragupta does with the data and that it stays on the machine.

## Known gaps

* **Architecture — decided: Apple Silicon only.** This builds for the machine it
  runs on. Intel is **not supported** and there is no plan to add it; a
  universal2 build is not worth carrying for it. The image is named
  `…-arm64.dmg` and `READ ME FIRST.txt` says so, because the failure on an Intel
  Mac is otherwise indistinguishable from a broken download.
* **Updates.** A `.dmg` has no update mechanism, so a new version is a fresh
  download. The **in-app check** now exists (`chitragupta/updates.py`, the
  Version section on the Model screen): it asks a feed once a day, names a
  newer release, and links to it. It does **not** install anything — replacing
  a signed, notarised app needs Sparkle, an EdDSA key and changes to
  `build-dmg.sh`, and a half-built auto-updater is worse than a link.
  The feed is **live** at <https://chitragupta-bf7.pages.dev/appcast.json> and
  is the default in `config.py` — a default rather than an env var, because a
  frozen `.app` reads no `.env` and so never sees one. `CHITRAGUPTA_UPDATE_FEED`
  overrides it; `""` turns the check off for a build entirely, at which point no
  request is made at all and the screen says so rather than offering a dead
  button. The file itself is in [`appcast/`](../appcast/README.md), in the repo
  so that what is published is version-controlled.
  The check is also the only answer this app has to *"how many people use
  this"* — once a day per install with **no identifier**, so request volume is
  an active-install count and nothing can profile anybody. That is the whole
  reason the interval exists; see `updates.py` before changing it, and
  [`docs/AUTH-ANALYSIS.md`](AUTH-ANALYSIS.md) §4 for why it matters.
* **The bundled Google OAuth client.** `chitragupta/data/google_client.json`
  ships inside the `.app`. That is a deliberate decision recorded in
  `.gitignore` (an installed-app client, which Google treats as
  non-confidential — the security is PKCE plus the loopback redirect). Shipping
  it in a public download is a larger audience than shipping it in a repo, so
  re-confirm that decision before publishing, and know how to rotate it.
* ~~**Icon.**~~ **Closed 2026-09-16.** `packaging/make-icon.py` draws
  `icon.icns` from the tokens in `docs/DESIGN-BRIEF.md` — night sky, the
  sidebar's diamond mark, one gold pole star — with the detail dropping in tiers
  so 16px stays a readable silhouette rather than a grey blob.

  The icon had in fact *never* shipped: the spec tested
  `Path("packaging/icon.icns")` while PyInstaller runs from inside `packaging/`,
  so it looked for `packaging/packaging/icon.icns` and silently fell back to
  PyInstaller's generic `icon-windowed.icns`. Adding artwork alone would have
  changed nothing. The spec is now anchored on `SPECPATH`, and `build-dmg.sh`
  asserts `CFBundleIconFile` on the built plist — the failure looked exactly
  like success, so it needed a check rather than a fix.

## What was verified, and what was not

Built and run on this machine (re-verified 2026-09-17, browser included): the
bundle, the smoke test, and the unsigned `.dmg` — 321 MB app, 122 MB image, the
app launches,
serves its API and answers `/api/sync/status`. Also checked on the built
artefact rather than assumed:

```bash
/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" dist/Chitragupta.app/Contents/Info.plist
# icon.icns          (was icon-windowed.icns — PyInstaller's generic default)
codesign --verify --deep --strict dist/Chitragupta.app
# valid on disk · satisfies its Designated Requirement
```

That second one now runs inside `--unsigned` builds too. PyInstaller ad-hoc
signs on Apple Silicon, and a *broken* ad-hoc signature does not produce the
"unverified developer" dialog — it produces **"Chitragupta is damaged and can't be
opened"**, which no Open Anyway sequence rescues. Skipping the signing block
used to skip this check with it, so the build could not tell "will warn" from
"will not open".

**Not verified: signing, notarisation and stapling.** No Developer ID
certificate exists on this machine (`security find-identity -v -p codesigning`
→ *0 valid identities found*), so steps 2-4 have never been executed. They are
written from Apple's documented process and the script preflights for them, but
the first person to run it with a real certificate should expect to debug it.
