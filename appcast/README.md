# The update feed

`appcast.json` is the list of versions that exist. It is the only thing a
`.dmg` install has to ask, because — [`docs/DISTRIBUTION.md`](../docs/DISTRIBUTION.md) —
*"a `.dmg` has no update mechanism. Every new version is a fresh download."*
Without this file a user who installed 0.1.0 stays on 0.1.0 forever, including
through a security fix.

It lives in the repo so that what is published is version-controlled rather than
typed into a dashboard and forgotten.

---

## Where it is deployed

**Live at <https://chitragupta-bf7.pages.dev/appcast.json>**, on Cloudflare
Pages, and that URL is the **default** in `config.py`.

A default rather than an environment variable, because a shipped `.app` reads no
`.env`: `config.py::uses_dotenv()` returns false when `sys.frozen` is set, and
deliberately so — a bundle's cwd is `/`, and a bare `load_dotenv()` would read
whatever `~/.env` the user happened to have lying around. Anything not defaulted
in `config.py` therefore never reaches a real build. A feed URL is not a secret,
so this costs nothing.

`CHITRAGUPTA_UPDATE_FEED` still overrides it — point a dev build somewhere else,
or set it to `""` to turn the check off for a build entirely, at which point
`updates.py` makes no request at all and the Version section says so rather than
offering a button that 404s.

## Re-deploying

Cloudflare dashboard → **Workers & Pages** → the `chitragupta-bf7` project →
**Create deployment** → drag this folder in. There is nothing to build.

Note that the site has **no `index.html`** — only `appcast.json` is published,
on purpose, because a Pages project serves a folder's whole contents and
anything else in here would become a public URL. So the site **root 404s**, and
that is correct: `/appcast.json` is the only thing meant to exist.

R2 is the better host if the `.dmg` files ever live beside the feed: no egress
fees, which is what a download actually costs.

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
