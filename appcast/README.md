# The update feed

`appcast.json` is the list of versions that exist. It is the only thing a
`.dmg` install has to ask, because — [`docs/DISTRIBUTION.md`](../docs/DISTRIBUTION.md) —
*"a `.dmg` has no update mechanism. Every new version is a fresh download."*
Without this file a user who installed 0.1.0 stays on 0.1.0 forever, including
through a security fix.

It lives in the repo so that what is published is version-controlled rather than
typed into a dashboard and forgotten.

---

## Deploying it

**Cloudflare Pages**, the whole of it:

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Upload assets**
2. Drag **this folder** in. Name it, **Deploy**.
3. Open `https://<name>.pages.dev/appcast.json` to confirm it serves.
4. That URL becomes `CHITRAGUPTA_UPDATE_FEED` — see *Turning it on* below.

Any static host works. R2 is the better choice if the `.dmg` files will live
beside it: no egress fees, which is what a download costs.

Re-deploying is the same drag. There is nothing to build.

## Turning it on

`CHITRAGUPTA_UPDATE_FEED` ships **empty**, so a build with no feed makes no
request at all and the Version section says so rather than offering a button
that 404s.

* **Development** — `CHITRAGUPTA_UPDATE_FEED=https://…/appcast.json` in `.env`.
* **A shipped `.app`** — a `.env` is **not read**: `config.py::uses_dotenv()`
  returns false when `sys.frozen` is set, deliberately, because a bundle's cwd
  is `/` and a bare `load_dotenv()` would read whatever `~/.env` the user
  happened to have. So the shipped value has to be the **default in
  `config.py`**. That is fine — a feed URL is not a secret.

## Adding a release

Newest first is conventional but not required: `updates._pick` takes the
*highest* version that beats the running one, because an appcast written by hand
will one day be out of order and the user should still be offered the newest
build.

```json
{ "releases": [
  { "version": "0.2.0",
    "url": "https://github.com/…/releases/download/v0.2.0/Chitragupta-0.2.0-arm64.dmg",
    "notes": "What changed, in one line a person reads.",
    "published": "2026-10-13" },
  { "version": "0.1.0", "url": "…", "notes": "First build.",
    "published": "2026-10-06" }
] }
```

| field | |
|---|---|
| `version` | required. Compared numerically, so `0.10.0` beats `0.9.0` — and a `-beta.1` suffix compares as its release, never above it |
| `url` | where to download. Shown as **Get it**, opened through `/api/open-browser`, which accepts `http(s)` only |
| `notes` | one line, shown under the heading |
| `published` | a date, shown as-is |
| `critical` | optional bool. Carried through; nothing reads it yet |

**`version` must stay at or below the shipped `__version__` until a build
actually exists.** The app only reports an update when the feed names something
newer, so the entry currently matching 0.1.0 is a deliberate no-op: the feed is
live and reports nothing.

Junk is skipped rather than fatal — a malformed entry is ignored and the rest of
the list is read.

## What asking it looks like

```
GET https://…/appcast.json?app=0.1.0&os=15.2&arch=arm64
```

Three fields, each because the server needs it to decide *which* release
applies. **No install id, no device id, nothing persistent** — see the module
docstring in [`chitragupta/updates.py`](../chitragupta/updates.py), and
`tests/test_updates.py` pins the list so a fourth field cannot appear quietly.

## Counting users

A static file cannot read those query parameters — **the host's access log
can.** The check runs at most once a day per install, so request volume *is* an
active-install count, broken down by app version and macOS version, with
nothing that identifies anybody.

So the number lives in **Cloudflare → your Pages project → Analytics**, not in
this file. That is the whole answer to *"how many people use this"*, and it is
why the once-a-day interval in `updates.py::CHECK_INTERVAL` is load-bearing
rather than a politeness: lowering it buys nothing and costs the property that
lets the check carry no identifier.

The README's privacy line was rewritten in the same commit that added the
check. If the request ever grows a field, that line changes with it.
