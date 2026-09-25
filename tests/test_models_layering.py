"""`models/` is a DAG, and stays one.

`COMPLEXITY_AUDIT.md` C.1 called this "the worst structural knot in the repo":
fourteen modules — every provider, plus discovery, entitlements, errors and the
registry — formed **one strongly-connected component**. None of them could be
read, tested or reasoned about without the other thirteen.

`ruff` was clean throughout and every import resolved, because **every one of
those edges had been pushed inside a function body**. That is what makes this
worth a test rather than a lint rule: a lazy import is a real dependency that no
tool sees, and the fix is undone by one `from .entitlements import ...` typed
inside a method, in a file where it looks entirely reasonable.

What broke it, and the rule each move established:

* `errors` reached up into `discovery` to name alternative models in one
  sentence. **`errors` is the bottom of the package** — every provider imports
  it at module level — so the catalog registers a supplier instead.
* `base` and `streaming` were a pair around one dataclass. **`StreamEvent` is a
  protocol type**, so it sits in `base` with `ChatResult` and `ToolCall`.
* `chatgpt_auth` imported `registry` to clear caches after a sign-in. **An auth
  module says "the credentials changed" to a leaf**; whoever holds derived state
  has already registered a clearer.
* `entitlements` did two jobs. **Policy is pure and lives at the bottom**
  (`entitlement_rules`), so a provider re-checking a model before sending it —
  which it must, a stored id being a request — does not drag the catalog up
  behind it. **Detection is a layer of its own** (`connection_state`), so
  `discovery` can decorate a row without needing the module that picks a model.
* `accounts` needed `find_claude` and `claude_code` needed `detect_claude_account`.
  **Detection sits below the provider that uses it**: `claude_cli`.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

MODELS = pathlib.Path(__file__).parent.parent / "chitragupta" / "models"

#: Modules nothing in this package may import — the floor of the layering.
LEAVES = ("errors", "entitlement_rules")


def _edges() -> dict[str, set[str]]:
    """Every intra-package edge, lazy imports counted as the real ones they are."""
    names = {p.stem for p in MODELS.glob("*.py")} - {"__init__"}
    graph: dict[str, set[str]] = {n: set() for n in names}
    for path in MODELS.glob("*.py"):
        if path.stem == "__init__":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                target = node.module.split(".")[0]
                if target in names and target != path.stem:
                    graph[path.stem].add(target)
    return graph


def _components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan. Returns only the components with more than one member."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                found.append(sorted(comp))

    for node in sorted(graph):
        if node not in index:
            strong(node)
    return found


def test_there_are_no_import_cycles():
    """The whole point. Was one component of fourteen; must stay zero.

    If this fails, read the component it names and find the edge that runs
    *upward* — a provider importing the catalog, or the catalog importing the
    module that chooses a model. That edge is the bug, not this test.
    """
    cycles = _components(_edges())
    assert not cycles, (
        "import cycle(s) in models/: "
        + "; ".join(" · ".join(c) for c in cycles))


@pytest.mark.parametrize("floor", LEAVES)
def test_the_floor_can_only_reach_other_leaves(floor):
    """`errors` is imported at module level by nearly every provider, and
    `entitlement_rules` is pure policy. Either one reaching something with
    dependencies of its own puts the package back in a knot, and it will look
    entirely reasonable at the call site — `errors` imported `discovery` to
    decorate one sentence.

    Not "imports nothing": `errors` asks `capabilities` for a provider's display
    name, and `capabilities` depends on nothing at all. Depending on a leaf is
    what a floor is allowed to do; the rule is that it cannot reach *upward*.
    """
    graph = _edges()
    seen: set[str] = set()
    frontier = list(graph[floor])
    while frontier:
        node = frontier.pop()
        if node in seen:
            continue
        seen.add(node)
        frontier.extend(graph.get(node, ()))
    offenders = {n for n in seen if graph.get(n)}
    assert not offenders, (
        f"{floor} reaches {sorted(offenders)}, which have dependencies of their "
        "own — the floor may only touch modules that touch nothing")


def test_entitlement_rules_is_pure():
    """It decides whether a plan may run a model, from its arguments alone. The
    moment it reads a credential or probes a CLI, providers cannot use it and
    the edges they need come back."""
    src = (MODELS / "entitlement_rules.py").read_text()
    for forbidden in ("import httpx", "subprocess", "get_settings",
                      "get_connection", "_saved_key"):
        assert forbidden not in src, f"entitlement_rules touches {forbidden}"


def test_find_claude_has_exactly_one_home():
    """It lives below both the provider that runs the CLI and the module that
    detects the account, which is what stopped those two importing each other.
    Two definitions would mean a test can patch the one nobody calls."""
    defs = [p.name for p in MODELS.glob("*.py")
            if "def find_claude(" in p.read_text()]
    assert defs == ["claude_cli.py"], defs


def test_stream_event_is_a_protocol_type_not_a_wire_type():
    """It is what `LLMProvider.stream` yields, so it belongs beside
    `ChatResult`. In `streaming` it made `base` import the wire parsers to
    implement its own default."""
    assert "class StreamEvent" in (MODELS / "base.py").read_text()
    assert "class StreamEvent" not in (MODELS / "streaming.py").read_text()
    from chitragupta.models.base import StreamEvent as FromBase
    from chitragupta.models.streaming import StreamEvent as FromStreaming
    assert FromBase is FromStreaming, "the re-export must not be a second class"


def test_the_layering_is_not_held_together_by_lazy_imports():
    """A note for whoever reads a failure above: the count is allowed to move,
    but the cycles are not. Lazy imports were how fourteen modules stayed
    mutually dependent while `ruff` reported nothing at all."""
    lazy = 0
    for path in MODELS.glob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            lazy += sum(1 for n in ast.walk(fn)
                        if isinstance(n, ast.ImportFrom) and n.level == 1)
    # A ceiling, not a target. 90 at the time the cycles were broken; the
    # number is allowed to move with the code, the cycles above are not. It is
    # here because lazy imports are how fourteen modules stayed mutually
    # dependent while `ruff` reported nothing at all — a sharp rise means the
    # habit is back, whether or not it has closed a loop yet.
    assert lazy <= 100, f"{lazy} lazy intra-package imports — was 90 here"
