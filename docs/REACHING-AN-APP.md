# Reaching an app — one route, decided by what the app allows

> Written after a user connected Notion twice and the agent picked the wrong
> one. Companion to [`CONNECTORS.md`](CONNECTORS.md) (how a connector is
> built), [`BROWSER.md`](BROWSER.md) (what the browser may do) and
> [`ACTION-COVERAGE.md`](ACTION-COVERAGE.md) (what an action must carry).

## The rule

**An app is reached one way. Never two.**

Not a preference — a correctness property. When Notion was reachable both as a
built-in connector and as a custom source, the Connectors screen showed both,
and an agent asked to write to a page proposed it down the route the user had
not set up. The card rendered, asked for approval, ran, and failed with
*"Notion is not connected"* in front of a green **CONNECTED** badge.

Nothing was broken. Both halves worked exactly as written. There were simply
two of them with the same name and no rule saying which wins, so the model
picked — and had no way to pick correctly. **No amount of prompt text fixes a
choice the app should never have offered.**

## The three tiers, in order

Try each in turn. The first that can do the job is the route.

### 1 · MCP — the default

The vendor's own server, over OAuth. It **feeds the brain** and **performs
actions**, and it is the route unless it cannot be.

Why it wins where it exists:

* **Setup is one button.** The alternative for Notion was pasting an
  integration secret *and* adding the integration to every page by hand.
* **Capability.** Notion's server publishes 45 tools; the hand-written
  connector had 5.
* **It does not rot.** The vendor owns the schema, so a field rename is their
  problem rather than a bug that surfaces months later as a refusal.

Both halves are already built: `MCPConnector.sync()` harvests every listing
that carries the user's content into the brain, and `mcp_action` performs
writes behind a per-`server:tool` grant.

### 2 · Connector — only where nothing else can reach

A connector exists for one reason: **there is no server to talk to.** Eight of
the fifteen are on-device and always will be —

```
apple_mail · imessage · notes · apple_calendar · apple_health
files · google_fit · telegram
```

There is no MCP server for the Messages database on your Mac. There will not
be one. These are the connectors the product is actually for.

> **Gmail, Calendar and Drive stay first-party today**, and the reason is not
> that MCP could not do it. It is that most of the eighteen jobs are built on
> those exact methods, they are the only connector code that has been
> exercised in anger, and replacing them means rewriting the whole action
> layer against tools that have never run here. That is a real piece of work
> with a real risk, and it should be a decision somebody makes on purpose —
> not a side effect of a rule.
>
> **GitHub was that decision, made on purpose.** Its server publishes 45 tools
> against the four written here. `github_comment` and `github_create_issue`
> are gone; a write is an `mcp_action` against `add_issue_comment` or
> `issue_write`. Nothing was dropped on the way: the grant still names the
> repository (`github:add_issue_comment@acme/api`, narrower than the
> `REPO_RECIPIENT` key it replaces, which covered merging a pull request too),
> and the comment's Undo survived as a **retraction** — GitHub's server
> publishes no delete-comment tool, so the button had to say what actually
> happens. `tests/test_github_is_reached_one_way.py` holds the absences.

### 3 · Browser — when neither can act

Some things can only be done by a person in a web page. The browser is that
last resort, and it is **deliberately not a source**:

* It **does not feed the brain.** A page is somebody else's text, fetched
  once, and quarantined — see [`BROWSER.md`](BROWSER.md).
* But **what an agent does there is recorded**, in the action log and in the
  brain as information: *what was done, where, when*. The evidence of the work
  is ours even when the material is not.

Reading only, today. A write tool joins `permissions.NEVER_UNATTENDED` in the
same commit that adds it.

## How the rule is enforced

Three mechanisms, and the difference between the first two is whether somebody
has already chosen.

| | |
|---|---|
| **Retired** | `Connector.prefer_mcp` names the server that supersedes it. The built-in is not offered at all. `notion` and `linear` declare it |
| **Deduped** | A built-in whose source an MCP server the user has added already reaches is hidden — whatever we think is better, they have chosen |
| **Shadowed** | `CatalogEntry.same_as` names the built-in an entry duplicates. Where that built-in is the route, **Add a connector** does not offer the server. `github`, `notion`, `linear` and `filesystem` declare it |

**Being shown and being the route are two questions.** Conflating them is a bug
this document has now seen from both ends. A *retired* connector that somebody
still has configured stays on the Connectors screen — it is their state — but
it is not the route, so the catalog must keep offering the server that replaces
it. Reading "it is on screen" as "it is the route" hid GitHub's server from the
one user who most needed it, and left the screen looking exactly as it had.

A row for a superseded connector therefore says so, and carries a
**Disconnect** — a retirement the user cannot act on is a retirement in name
only, and until this landed a token-backed connector had no way out at all:
once `ready`, the row offered *Sync* and nothing else.

**A configured connector is never hidden.** Taking away something somebody set
up, because we changed our mind about which route is better, is losing their
state to our decision. That is the one thing `/CLAUDE.md` forbids first.

The three are one decision, not three: `_builtin_is_offered()` in
`api/routes/connectors.py` answers *is this built-in the route to its source
right now*, and both screens read that same answer. Two functions each deciding
half of it is how GitHub came to sit **CONNECTED** on the Connectors screen
while the catalog, two clicks away, offered to connect GitHub.

Which side of a pair wins falls out of the declarations rather than being
chosen per source:

* `prefer_mcp` set, built-in not configured → the **server** is the route.
* Otherwise → the **built-in** is the route, and its catalog entry is not
  offered. Flipping a source to MCP later is one line — set `prefer_mcp` on
  the connector — and both screens change together.

`tests/test_one_way_to_connect.py` holds all three, holds the on-device eight
to never standing down, and asserts the pairing *mechanically*: any catalog
entry whose id is already a connector name must declare `same_as`, so the next
one cannot arrive undeclared.

## What the user sees

The Connectors screen groups by **where the data is**, not by topic:

| | |
|---|---|
| **On this Mac** | `files` · `notes` · `imessage` · `apple_mail` · `apple_calendar` · `apple_health`. `Connector.runs_on_device` declares it |
| **Your accounts** | everything else, whoever wrote the client |

Topic was the old split and it answered a question nobody was asking — every
row already says what it gives you. What no row said is whether anything
leaves the machine, which is the sentence at the top of that screen and the
whole product promise.

Each row then carries a tag naming the route — **Built-in**, **MCP**,
**Custom** — with a legend defining all three. This names a mechanism, which
is normally an internal; it is here deliberately, because *who to chase when
it misbehaves* is a different question from *what it gives you*, and a
built-in is ours to fix while a vendor's server is theirs and can change under
us. The sections stay jargon-free; only the tag names the route.

**Every source carries exactly one tag.** That is this document's rule made
visible: if a source could ever show two, the bug is back.

The grouping used to be a map in `web/connectors.js` keyed by connector name,
and four connectors were missing from it — Slack, Telegram, Apple Health and
Google Fit landed under a heading reading *Custom sources*. A fact the backend
knows does not get a second copy in the frontend.
`tests/test_a_source_says_how_it_is_reached.py` holds it.

## Adding a source

1. **Does the vendor ship an MCP server?** Add it as a custom source. Nothing
   to write.
2. **No server, and it is on this Mac or behind a personal credential?** Write
   a connector — [`CONNECTORS.md`](CONNECTORS.md).
3. **Neither, and the job is only doable in a page?** The browser, read-only,
   and record what was done.

If you find yourself writing a connector for something with a server, stop:
you are building the second route that this document exists to prevent.

**If a connector for it already exists**, you are not adding a source — you are
replacing one. Give the entry `same_as` naming the built-in, and say which wins
by whether that connector declares `prefer_mcp`. An entry without `same_as` is
a second route, and the suite fails on it.
