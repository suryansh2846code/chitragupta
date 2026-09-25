"""The whole package's import graph, counting lazy imports as real edges.

`tests/test_models_layering.py` does this for `models/`, which was the worst
knot. This is the same check over everything else, because the same thing had
happened in four more places and for the same reason: a cycle is broken by
moving the import inside a function, which satisfies `ruff`, satisfies the
interpreter, and leaves the dependency exactly where it was — now invisible.

Where it stood on 2026-09-25, before and after:

| cycle | was | now |
|---|---|---|
| `models/` | 14 | **0** |
| `agents/` + `actions` + `routines` | 19 | **5** |
| `agents.approvals` ↔ `agents.outcomes` | 2 | **0** |
| `config` ↔ `log` | 2 | **0** |
| `api/` ↔ `desktop`/`hud` | yes | **0** |
| `brain` · `connectors` · `core` (package level) | yes | **0** |
| `connectors.mcp_*` | 3 | 3 |
| `browser.*` | 3 | 3 |

The three that remain are listed in `KNOWN` with what each is. **The list is a
ceiling, not a licence**: a new cycle fails this test, and a listed one that
grows fails it too. Each is recorded in `docs/ARCHITECTURE.md` §6 with the fix
it is waiting for.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).parent.parent / "chitragupta"

#: Cycles that exist today, each with the reason it was left. Frozen sets so a
#: cycle that *grows* is a failure — that is the direction this rots in.
KNOWN: dict[str, frozenset[str]] = {
    # `browse_click` asks the driver, the driver owns a session, signing in
    # drives a browser. A genuine three-way between a driver, its session and
    # the sign-in flow that uses both.
    "browser": frozenset({"browser.chromium", "browser.session",
                          "browser.signin"}),
    # An MCP server's auth needs its spec, its spec needs its tools, its tools
    # need auth to call them. One subsystem split three ways.
    "mcp": frozenset({"connectors.mcp_auth", "connectors.mcp_source",
                      "connectors.mcp_tools"}),
    # `connector_grants` reads one security-relevant flag off a library
    # template (`unrestricted_connectors`, which only Chief of Staff has), and
    # `library` resolves the "everything" tool marker against `tools`.
    # Deliberately not cut: moving where a template declares that flag is a
    # change to how consent is expressed, which is worth more care than a cycle.
    "agents_tools": frozenset({"agents.browse_tools", "agents.connector_grants",
                               "agents.library", "agents.message_tools",
                               "agents.tools"}),
}


def _graph() -> dict[str, set[str]]:
    """Module → modules it imports, lazy imports included."""
    names: dict[str, pathlib.Path] = {}
    for path in SRC.rglob("*.py"):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names[".".join(parts)] = path

    graph: dict[str, set[str]] = {n: set() for n in names}
    for name, path in names.items():
        package = name.rsplit(".", 1)[0] if "." in name else ""
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            base = package
            for _ in range(max(0, node.level - 1)):
                base = base.rsplit(".", 1)[0] if "." in base else ""
            head = f"{base}.{node.module}".strip(".") if node.module else base
            for alias in node.names:
                full = f"{head}.{alias.name}".strip(".")
                target = full if full in names else (head if head in names else None)
                if target and target != name:
                    graph[name].add(target)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[frozenset[str]]:
    """Tarjan, iterative — the graph is deep enough to blow the stack."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[frozenset[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                nxt = pending.pop()
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(graph[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        out.append(frozenset(component))
    return out


def test_no_new_import_cycles():
    """A cycle not on the list is a new one. Break it, do not list it.

    If this fails, find the edge that runs *upward* — a provider importing the
    catalog, a leaf importing the layer above it, a tool importing the loop it
    runs inside. That edge is the bug; the cycle is only how it shows up.
    """
    found = _cycles(_graph())
    unknown = [sorted(c) for c in found if c not in set(KNOWN.values())]
    assert not unknown, f"new import cycle(s): {unknown}"


@pytest.mark.parametrize("label", sorted(KNOWN))
def test_a_known_cycle_does_not_grow(label):
    """The direction this rots in. A cycle absorbing one more module each time
    somebody needs a fact from it is how `models/` reached fourteen."""
    found = _cycles(_graph())
    matching = [c for c in found if c & KNOWN[label]]
    for cycle in matching:
        extra = sorted(cycle - KNOWN[label])
        assert not extra, f"{label} cycle grew by {extra}"


def test_the_list_does_not_keep_stale_entries():
    """A cycle that has been fixed must leave the list, or the next reader
    believes the codebase is worse than it is."""
    found = set(_cycles(_graph()))
    stale = [label for label, members in KNOWN.items() if members not in found]
    assert not stale, f"these cycles are fixed — delete them from KNOWN: {stale}"


def test_the_floor_of_the_whole_package_stays_a_floor():
    """`log`, `home` and `config` are leaf utilities, and `/CLAUDE.md` says to
    keep them that way. `log` asked `config` for the home directory and `config`
    used `log.suppressed` — a two-module cycle over one path, now `home.py`."""
    graph = _graph()
    assert graph["home"] == set(), f"home must import nothing: {graph['home']}"
    assert "config" not in graph["log"], "log must not reach settings"


def test_errors_and_entitlement_rules_are_still_the_models_floor():
    """Guarded in detail by `test_models_layering.py`; asserted here too so a
    whole-package run says so without reading two files."""
    graph = _graph()
    # `log` and `config` are the package's own leaf utilities and are below
    # everything by definition — depending on them is not reaching upward.
    leaves = {"log", "config", "home", "models.capabilities"}
    assert graph["models.entitlement_rules"] <= leaves
    assert graph["models.errors"] <= leaves, graph["models.errors"]
