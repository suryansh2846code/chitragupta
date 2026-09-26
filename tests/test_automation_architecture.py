"""The eleven things that must stay true, checked rather than asserted in prose.

Every one of these is a property the automation engine was built to have and
that a later change could quietly remove while every behavioural test stayed
green — because the failure mode is not "it stops working", it is "there are now
two of them, and one of them does not ask permission".

Read as source-level checks on purpose. A behavioural test proves the path taken
today; these prove there is no *other* path, which is a different claim and the
only one that survives somebody adding a shortcut in six months.

`test_import_layering.py` owns the whole package's cycle check; the last test
here is the automation package's slice of it, kept next to its neighbours so the
list reads as one set of rules.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

SRC = pathlib.Path(__file__).parent.parent / "chitragupta"
AUTOMATION = SRC / "automation"


def source(module: pathlib.Path) -> str:
    return module.read_text()


def automation_files() -> list[pathlib.Path]:
    return sorted(p for p in AUTOMATION.glob("*.py") if p.name != "__init__.py")


def names_imported(path: pathlib.Path) -> set[str]:
    """Every module this file imports, lazy ones included.

    Counting a function-level import as a real edge is the whole point: a cycle
    is usually "broken" by moving the import inside a function, which satisfies
    the interpreter and leaves the dependency exactly where it was.
    """
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * (node.level or 0)
            out.add(prefix + (node.module or ""))
    return out


# ── 1. one action chokepoint ───────────────────────────────────────────────

def test_actions_is_the_only_place_an_action_is_performed():
    """`actions.run_now` is where an action becomes an effect.

    The automation package does not import it, does not call a handler, and does
    not reach `REGISTRY` — it receives `perform` on `Deps`, which `engine.py`
    wires to the one chokepoint. A second call site is a second set of rules
    about what happens before and after an action.
    """
    offenders = []
    for path in automation_files():
        if path.name == "engine.py":
            continue                      # the composition root, checked below
        text = source(path)
        for marker in ("run_now(", "actions.REGISTRY", "spec.handler",
                       ".handler("):
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
        # `from ..actions import parse_actions` is allowed and is the reason
        # this is a list rather than a ban on the module: a parser reads a
        # model's reply and causes nothing. A handler is what may not be
        # reached.
        for line in text.splitlines():
            if line.strip().startswith("from ..actions import"):
                imported = line.split("import", 1)[1]
                for name in imported.split(","):
                    if name.strip() not in ("parse_actions", "REGISTRY"):
                        offenders.append(f"{path.name}: imports {name.strip()}")
    assert offenders == [], f"an action is performed outside `actions`: {offenders}"


def test_the_engine_reaches_actions_only_to_wire_them():
    """`engine.py` may name `actions`, because something has to. What it takes
    is the chokepoint and the registry's *declarations* — never a handler."""
    text = source(AUTOMATION / "engine.py")
    assert "run_now" in text, "the composition root stopped using the chokepoint"
    for forbidden in ("spec.handler(", ".handler(", "_send_email(", "_create_task("):
        assert forbidden not in text, f"engine.py calls a handler directly: {forbidden}"


# ── 2 & 3. permissions are authoritative, and nothing here widens them ─────

def test_the_permission_check_is_never_reimplemented_in_the_automation_core():
    """One permission system. A module here deciding for itself whether an
    action is allowed is a second one with its own bugs and its own blind
    spots, and it is the failure this architecture exists to prevent."""
    for path in automation_files():
        if path.name == "engine.py":
            continue
        text = source(path)
        assert "from ..agents.permissions import" not in text, (
            f"{path.name} reaches into the permission system directly")
        assert "permissions.check" not in text.split('"""')[-1], (
            f"{path.name} calls the gate itself instead of using `Deps.gate`")


def test_the_gate_is_asked_before_every_action_and_its_answer_is_not_reread():
    """The executor asks once and acts on the answer. A `not verdict.allowed`
    that is later re-evaluated against something else would be a widening."""
    text = source(AUTOMATION / "executor.py")
    body = text.split("def _run_action")[1].split("\n    def ")[0]
    assert "self.deps.gate(" in body
    # The only thing allowed to let a refused action through is `pre_approved`,
    # which is a recorded human decision about this exact content.
    allowances = [line for line in body.splitlines()
                  if "not verdict.allowed" in line]
    assert len(allowances) == 2, (
        "the number of ways a refusal can be survived changed; there must be "
        "exactly two — block it, or the pre-approved case")
    assert "pre_approved" in body


def test_pre_approval_is_written_in_exactly_one_place():
    """`pre_approved` is the one flag that lets a refused action run. It is set
    from a `scheduled_actions` row the user confirmed, and nowhere else."""
    setters = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            if "pre_approved=True" in line or "pre_approved = True" in line:
                setters.append(f"{path.relative_to(SRC)}: {line.strip()}")
    assert len(setters) == 1, f"pre_approved is set in {len(setters)} places: {setters}"
    assert "engine.py" in setters[0]


def test_an_automation_cannot_run_an_action_without_going_through_approvals():
    """A blocked action queues and waits. The only exits from
    `WAITING_FOR_APPROVAL` are the human's answer, a timeout, or a cancel."""
    from chitragupta.core.automation_store import _ALLOWED, RunState

    exits = _ALLOWED[RunState.WAITING_FOR_APPROVAL]
    assert RunState.EXECUTING in exits, "an approved action could never proceed"
    assert RunState.COMPLETED not in exits, (
        "a run waiting on a human could complete without them")
    assert RunState.VERIFYING not in exits, (
        "a run could verify an action it never performed")


# ── 4. the scheduler does not execute ──────────────────────────────────────

def test_the_scheduler_does_not_perform_actions_itself():
    """It used to: a due reminder was a `desktop_notify` call and a due
    scheduled action was a bare `run_now`, neither recorded anywhere. Both are
    runs now, and the scheduler's job is to say "go"."""
    text = source(SRC / "scheduler.py")
    fire = text.split("def _fire_reminders")[1].split("\n    def ")[0]
    for forbidden in ("run_now(", "desktop_notify(", "get_reminders(",
                      "get_scheduled("):
        assert forbidden not in fire, (
            f"the scheduler does the work itself again: {forbidden}")
    assert "fire_due()" in fire and "tick()" in fire


def test_there_is_one_canonical_path_for_a_scheduled_thing():
    """`engine.run_once` builds the run; `fire_due` is the only caller that
    turns a due row into one. Two builders would mean two sets of defaults."""
    text = source(AUTOMATION / "engine.py")
    assert text.count("def run_once(") == 1
    callers = [p.name for p in SRC.rglob("*.py")
               if "run_once(" in p.read_text() and p.name != "engine.py"]
    assert callers == [], f"something else starts scheduled runs: {callers}"


def test_reminders_did_not_become_a_second_scheduler():
    """`reminders.py` stores what the user asked for. It does not decide when
    anything happens, and it does not notify."""
    text = source(SRC / "reminders.py")
    for forbidden in ("desktop_notify", "while True", "Thread(", "sleep("):
        assert forbidden not in text, f"reminders.py started scheduling: {forbidden}"


def test_nothing_in_the_app_runs_a_routine_outside_the_engine():
    """A routine is a row; running one goes through the engine.

    `routines.run_routine` — the original executor, with its own approval call,
    its own notification and no run record — is still in the file and is now
    **called by nothing in `chitragupta/`**. That is the state this pins: not
    that the function is gone, but that no production path reaches it. Wiring
    it back up would give the app two execution engines with two sets of rules,
    which is the failure this rule is named after.

    It is dead code and should be deleted along with the three tests in
    `test_unattended_actions.py` that still drive it; that is recorded as a
    remaining limitation rather than done here, because those three are
    injection-resistance tests and moving them is its own change.
    """
    callers = []
    for path in SRC.rglob("*.py"):
        if path.name == "routines.py":
            continue
        for line in path.read_text().splitlines():
            if "run_routine(" in line and not line.strip().startswith(("#", "*")):
                callers.append(f"{path.relative_to(SRC)}: {line.strip()}")
    assert callers == [], f"the old routine executor is live again: {callers}"


def test_the_scheduler_reaches_routines_only_through_the_engine():
    """The path that used to call it. `_fire_reminders` asks the engine; the
    routine sweep it replaced is gone."""
    text = source(SRC / "scheduler.py")
    assert "run_routine" not in text
    assert "routines.sweep" not in text


# ── 5. webhooks enter through the one pipeline ─────────────────────────────

def test_a_webhook_becomes_an_event_and_then_stops_being_special():
    """`webhooks.receive` authenticates and normalises. Everything after is the
    path a connector sync takes: the ledger, the router, the executor."""
    text = source(AUTOMATION / "webhooks.py")
    assert "def receive(" in text
    for forbidden in ("Executor", "router.route", "create_run("):
        assert forbidden not in text, (
            f"the webhook module executes rather than normalising: {forbidden}")


def test_the_webhook_route_does_not_decide_anything():
    from chitragupta.api.routes import automations

    route = inspect.getsource(automations.receive_webhook)
    assert "engine.ingest" in route
    for forbidden in ("Executor", "router.route", "create_run(", "permissions"):
        assert forbidden not in route, f"the route decides: {forbidden}"


def test_provider_specific_webhook_logic_stays_in_the_verifiers():
    """GitHub's header names and Stripe's timestamp scheme are facts about those
    two companies. `receive` must not learn either.

    Read off the AST with the docstring dropped: the prose may say "GitHub's
    `X-Hub-Signature-256`", because explaining the rule needs an example. The
    code may not.
    """
    tree = ast.parse((AUTOMATION / "webhooks.py").read_text())
    body = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "receive")
    statements = body.body[1:] if (
        body.body and isinstance(body.body[0], ast.Expr)
        and isinstance(getattr(body.body[0], "value", None), ast.Constant)
    ) else body.body

    literals = []
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                literals.append(getattr(node, "id", "")
                                or getattr(node, "attr", ""))

    for vendor in ("X-Hub-Signature", "Stripe-Signature", "github", "stripe"):
        assert not any(vendor.lower() in str(x).lower() for x in literals), (
            f"`receive` names a provider ({vendor}); that belongs to a verifier")


# ── 6. MCP goes through the same gate ──────────────────────────────────────

def test_an_mcp_tool_is_an_action_like_any_other():
    """`mcp_action` is in the registry, has a risk tier, and is judged by the
    same `permissions.check`. A connector tool reached through its own path
    would be an action the allow-lists never see."""
    from chitragupta.actions import REGISTRY
    from chitragupta.agents import permissions

    assert "mcp_action" in REGISTRY
    verdict = permissions.check("mcp_action", {
        "server_id": "github", "tool": "delete_repository", "arguments": {}})
    assert verdict.allowed is False, (
        "an irreversible connector tool ran without anybody being asked")


def test_the_automation_package_never_calls_a_connector_tool_itself():
    for path in automation_files():
        text = source(path)
        for forbidden in ("mcp_tools", "call_tool(", "from ..connectors"):
            assert forbidden not in text, (
                f"{path.name} reaches a connector directly: {forbidden}")


# ── 7. no connector names in the core ──────────────────────────────────────

CORE = ("triggers.py", "router.py", "conditions.py", "context.py",
        "executor.py", "webhooks.py")


@pytest.mark.parametrize("name", CORE)
def test_no_connector_is_named_in_the_automation_core(name):
    """A Gmail message, a GitHub push and a file change are all `Event`s. The
    mapping is a dictionary in `sources.py`; anything else is a branch that has
    to be edited for every new connector.

    Walks the AST rather than the text, so a docstring may use an example and
    code may not — which is what makes the rule teachable instead of silent.
    """
    from chitragupta.automation.sources import EVENT_KINDS

    tree = ast.parse((AUTOMATION / name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A docstring is the one place an example is allowed.
            continue
        if isinstance(node, (ast.Name, ast.Attribute)):
            label = getattr(node, "id", "") or getattr(node, "attr", "")
            assert label not in EVENT_KINDS, (
                f"{name} names the connector {label!r}")


def test_the_mapping_is_data_and_lives_in_one_file():
    from chitragupta.automation import sources

    assert isinstance(sources.EVENT_KINDS, dict)
    holders = [p.name for p in automation_files()
               if "EVENT_KINDS" in p.read_text() and p.name != "sources.py"]
    assert holders == [], f"the connector mapping is duplicated in {holders}"


# ── 8. the dependency direction ────────────────────────────────────────────

def test_automation_never_imports_upward():
    """It sits above `agents/` and below `api/`. Importing an API module, the
    desktop window or the scheduler would make the engine depend on the process
    that happens to be running it."""
    upward = ("..api", "..desktop", "..hud", "..scheduler", "chitragupta.api",
              "chitragupta.desktop", "chitragupta.hud", "chitragupta.scheduler")
    for path in automation_files():
        imported = names_imported(path)
        for name in imported:
            assert not any(name.startswith(u) for u in upward), (
                f"{path.name} imports upward: {name}")


def test_agents_never_imports_the_automation_package():
    """The other direction of the same rule. An automation drives an agent turn;
    an agent knowing about automations would close the loop."""
    offenders = []
    for path in (SRC / "agents").rglob("*.py"):
        for name in names_imported(path):
            if "automation" in name and "automation_store" not in name:
                offenders.append(f"{path.name}: {name}")
    assert offenders == [], f"agents/ imports the engine: {offenders}"


def test_the_durable_store_is_shared_by_both_layers_and_imports_neither():
    """`core/automation_store.py` is where the rows live precisely because two
    layers need them. It must stay a leaf."""
    imported = names_imported(SRC / "core" / "automation_store.py")
    for name in imported:
        assert "automation." not in name.replace("automation_store", ""), (
            f"the store imports the engine: {name}")
        assert not name.startswith("..agents"), f"the store imports agents: {name}"


# ── 9. no new cycles ───────────────────────────────────────────────────────

def test_the_automation_package_has_no_import_cycles():
    """Its own slice of `test_import_layering.py`, kept here so the eleven rules
    read as one list. Lazy imports count: a cycle "broken" by moving an import
    inside a function is a cycle with a hiding place."""
    graph: dict[str, set[str]] = {}
    for path in automation_files():
        me = path.stem
        edges = set()
        for name in names_imported(path):
            leaf = name.lstrip(".").split(".")[-1]
            if leaf != me and (AUTOMATION / f"{leaf}.py").exists():
                edges.add(leaf)
        graph[me] = edges

    # Tarjan, small enough to write out: any strongly connected component with
    # more than one member is a cycle.
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[set[str]] = []
    counter = [0]

    def strong(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in graph.get(node, set()):
            if neighbour not in index:
                strong(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component = set()
            while True:
                other = stack.pop()
                on_stack.discard(other)
                component.add(other)
                if other == node:
                    break
            if len(component) > 1:
                found.append(component)

    for node in graph:
        if node not in index:
            strong(node)

    assert found == [], f"import cycles inside automation/: {found}"
