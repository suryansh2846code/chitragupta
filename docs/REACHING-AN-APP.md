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

> **Gmail, Calendar, Drive and GitHub stay first-party today**, and the reason
> is not that MCP could not do it. It is that seventeen of the eighteen jobs
> are built on those exact methods, they are the only connector code that has
> been exercised in anger, and replacing them means rewriting the whole action
> layer against tools that have never run here. That is a real piece of work
> with a real risk, and it should be a decision somebody makes on purpose —
> not a side effect of a rule.

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

Two mechanisms, and the difference between them is whether somebody has
already chosen.

| | |
|---|---|
| **Retired** | `Connector.prefer_mcp` names the server that supersedes it. The built-in is not offered at all. `notion` and `linear` declare it |
| **Deduped** | A built-in whose name matches an MCP server the user has added is hidden — whatever we think is better, they have chosen |

**A configured connector is never hidden.** Taking away something somebody set
up, because we changed our mind about which route is better, is losing their
state to our decision. That is the one thing `/CLAUDE.md` forbids first.

`tests/test_one_way_to_connect.py` holds both, and holds the on-device eight
to never standing down.

## Adding a source

1. **Does the vendor ship an MCP server?** Add it as a custom source. Nothing
   to write.
2. **No server, and it is on this Mac or behind a personal credential?** Write
   a connector — [`CONNECTORS.md`](CONNECTORS.md).
3. **Neither, and the job is only doable in a page?** The browser, read-only,
   and record what was done.

If you find yourself writing a connector for something with a server, stop:
you are building the second route that this document exists to prevent.
