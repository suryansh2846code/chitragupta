"""A scorecard for what the agents can actually do.

"The agents are better now" is the kind of claim that rots. Every capability
here is checked by running the real loop against a scripted model — no network,
no keys, no spend — and the result is a number that can be compared between
commits and argued with.

Deliberately not a benchmark of answer *quality*: that needs a real model and a
human, and it moves when the model does. These check the things the harness
itself is responsible for — does a deeper budget get used, do independent tools
overlap, does a model that repeats itself get stopped, can an agent consult
another, is an unattended outbound action gated. Those either work or they do
not, and if they do not, no model is going to rescue them.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from ..log import get_logger

log = get_logger(__name__)


@dataclass
class Check:
    key: str
    title: str
    passed: bool
    detail: str = ""


@dataclass
class Scorecard:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> float:
        """Out of 10, so it can be compared with a human rating."""
        return round(10 * self.passed / self.total, 1) if self.total else 0.0

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "score": self.score, "passed": self.passed, "total": self.total,
            "checks": [{"key": c.key, "title": c.title, "passed": c.passed,
                        "detail": c.detail} for c in self.checks],
        }

    def render(self) -> str:
        lines = [f"Agent capability: {self.score}/10  ({self.passed}/{self.total})"]
        for c in self.checks:
            lines.append(f"  {'PASS' if c.passed else 'FAIL'}  {c.title}"
                         + (f" — {c.detail}" if c.detail else ""))
        return "\n".join(lines)


def _swap(module, name: str, value) -> None:
    """Replace a module attribute for the duration of a check.

    Through a helper rather than in line: `runtime.get_provider` is an
    lru_cache wrapper and `resolve_usable_model` has a precise signature, so a
    plain assignment is a type error even though substituting them is the whole
    point. Taking the attribute name as an argument also keeps the linter from
    rewriting `setattr(x, "literal", v)` straight back into that assignment.
    """
    setattr(module, name, value)


def _scripted(script, **kw):
    """A provider that answers from a script, installed over the real one."""
    from ..models.base import ChatResult, LLMProvider, ToolCall

    class _Scripted(LLMProvider):
        name = "eval"
        model = "eval-1"

        def __init__(self):
            self.rounds = 0
            self.tools_offered: list[list[str]] = []

        def is_ready(self):
            return True, ""

        def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=1500):
            self.tools_offered.append([t.name for t in (tools or [])])
            if not tools or self.rounds >= len(script):
                return ChatResult(text=kw.get("final", "done"))
            step = script[self.rounds]
            self.rounds += 1
            if isinstance(step, str):
                return ChatResult(text=step)
            return ChatResult(text="", tool_calls=[
                ToolCall(id=f"c{self.rounds}-{i}", name=n, arguments=a)
                for i, (n, a) in enumerate(step)])

    return _Scripted()


def run(*, include_slow: bool = True) -> Scorecard:
    """Run every capability check and return the scorecard."""
    import inspect as _inspect

    from . import delegation, grounding, runtime
    from . import file_tools as file_tools_mod
    from . import mcp_tools as mcp
    from . import tools as tools_mod
    from .effort import get_effort

    card = Scorecard()

    def check(key, title):
        def record(passed, detail=""):
            card.checks.append(Check(key, title, bool(passed), detail))
        return record

    # Keep the real implementations, restore them whatever happens.
    saved_impls = dict(tools_mod.TOOL_IMPLS)
    saved_provider = runtime.get_provider
    saved_resolve = runtime.resolve_usable_model
    saved_supplier = mcp._supplier

    def use(provider) -> None:
        """Point the runtime at a scripted model for the next check."""
        _swap(runtime, "get_provider", lambda _p, _m: provider)

    _swap(runtime, "resolve_usable_model", lambda _p, m: (m or "eval-1", None))
    try:
        # ── depth ────────────────────────────────────────────────────────
        tools_mod.TOOL_IMPLS["list_entities"] = lambda **kw: "an entity"
        deep_script = [[("list_entities", {"limit": i})] for i in range(40)]
        provider = _scripted(deep_script)
        use(provider)
        high = runtime.run_turn("research", "dig deep", effort="high")
        check("depth", "Goes deeper than the old five-round ceiling")(
            high.steps_used > 5, f"{high.steps_used} tool rounds")

        provider = _scripted([[("list_entities", {"limit": i})] for i in range(40)])
        use(provider)
        low = runtime.run_turn("research", "dig", effort="low")
        check("effort", "The effort setting actually changes the budget")(
            low.steps_used < high.steps_used,
            f"low {low.steps_used} vs high {high.steps_used} rounds")

        # ── no runaway ───────────────────────────────────────────────────
        calls = {"n": 0}

        def counted(**kw):
            calls["n"] += 1
            return "the same answer"

        tools_mod.TOOL_IMPLS["list_entities"] = counted
        provider = _scripted([[("list_entities", {"limit": 3})] for _ in range(30)])
        use(provider)
        repeated = runtime.run_turn("research", "dig", effort="high")
        check("no_repeat", "A repeated call is answered from memory, not re-run")(
            calls["n"] == 1, f"executed {calls['n']}x")
        check("stall", "A model that stops learning is asked to answer")(
            provider.rounds < 30 and bool(repeated.reply),
            f"stopped after {provider.rounds} rounds")

        # ── budget exhaustion still answers ──────────────────────────────
        tools_mod.TOOL_IMPLS["list_entities"] = lambda **kw: f"entity {time.time()}"
        provider = _scripted([[("list_entities", {"limit": i})] for i in range(100)],
                             final="here is what I found")
        use(provider)
        spent = runtime.run_turn("research", "dig", effort="high")
        check("budget_answer", "Spending the whole budget still yields an answer")(
            spent.reply == "here is what I found", spent.reply[:40])

        # ── our own grounding note stays out of the arguments ────────────
        # The date is stated in front of the question so a small model cannot
        # miss it; a model then copies its input into the call it emits, and
        # `search_brain`'s query reaches `parse_date_range`, which reads the
        # date we supplied as a filter and answers from an empty brain.
        note_args: dict = {}

        def capture(**kw):
            note_args.update(kw)
            return "some context"

        tools_mod.TOOL_IMPLS["search_brain"] = capture
        echoed = grounding.prefixed("Tuesday, September 15, 2026", "who is she")
        provider = _scripted([[("search_brain", {"query": echoed})], "found her"])
        use(provider)
        runtime.run_turn("research", "who is she")
        check("date_note", "The date we tell the model never reaches a tool")(
            bool(note_args) and "[Today is" not in note_args.get("query", ""),
            note_args.get("query", "(never called)")[:60])

        # ── stop actually stops ──────────────────────────────────────────
        # Everything after the first check is spend the user asked to end.
        stop = threading.Event()
        spend = {"tools": 0}

        def stops_the_turn(**kw):
            spend["tools"] += 1
            stop.set()                      # the user presses Stop, mid-tool
            return "ok"

        tools_mod.TOOL_IMPLS["list_entities"] = stops_the_turn
        provider = _scripted([[("list_entities", {"limit": i})] for i in range(24)])
        use(provider)
        halted = runtime.run_turn("research", "dig deep", effort="high",
                                  cancel=stop)
        check("stop", "Stop ends the turn, not just the watching")(
            halted.stopped and provider.rounds == 1 and spend["tools"] == 1,
            f"{provider.rounds} model call(s) and {spend['tools']} tool(s), "
            f"of a 24-round budget")

        # ── parallelism ──────────────────────────────────────────────────
        if include_slow:
            live = {"now": 0, "peak": 0}

            def slow(**kw):
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
                time.sleep(0.12)
                live["now"] -= 1
                return "ok"

            tools_mod.TOOL_IMPLS["list_entities"] = slow
            provider = _scripted([[("list_entities", {"limit": i}) for i in range(4)],
                                  "done"])
            use(provider)
            started = time.perf_counter()
            runtime.run_turn("research", "dig", effort="high")
            elapsed = time.perf_counter() - started
            check("parallel", "Independent tool calls overlap")(
                live["peak"] > 1 and elapsed < 0.12 * 4,
                f"{live['peak']} at once, {elapsed:.2f}s for four 0.12s calls")

        # ── planning ─────────────────────────────────────────────────────
        tools_mod.TOOL_IMPLS.update(saved_impls)
        provider = _scripted([[("update_plan", {"steps": ["find it", "draft it"],
                                                "done_through": 1})], "done"])
        use(provider)
        planned = runtime.run_turn("research", "two-part job", effort="high")
        check("planning", "Keeps a plan on multi-part work, and reports it")(
            len(planned.plan) == 2 and planned.plan[0]["done"],
            f"{len(planned.plan)} steps")

        # ── delegation ───────────────────────────────────────────────────
        provider = _scripted([[("ask_agent", {"agent_id": "personal",
                                              "question": "what is on today?"})],
                              "Personal says nothing is on."])
        use(provider)
        asked = runtime.run_turn("inbox", "check with personal", effort="high")
        delegated = any(s.name == "ask_agent" and "personal answered" in s.result
                        for s in asked.trace if s.kind == "tool_result")
        check("delegation", "An agent can consult another and get its answer")(
            delegated)

        token = delegation.enter("inbox", get_effort("high"))
        try:
            cycle = delegation.refusal("inbox")
            off = delegation.refusal("nobody")
        finally:
            delegation.leave(token)
        check("delegation_guard", "Delegation refuses loops and unknown agents")(
            bool(cycle) and bool(off))

        # ── grounding ────────────────────────────────────────────────────
        provider = _scripted([], final="ok")
        use(provider)
        grounded = runtime.run_turn("research", "what do you know about me?",
                                    effort="medium")
        check("grounding", "The brain is recalled before the model chooses")(
            any(s.name == "auto_recall" for s in grounded.trace)
            or grounded.steps_used == 0)

        # ── streaming ────────────────────────────────────────────────────
        provider = _scripted([], final="streamed answer")
        use(provider)
        seen: list[dict] = []
        streamed = runtime.run_turn("research", "hello", effort="low",
                                    on_event=seen.append)
        check("streaming", "A turn reports progress while it happens")(
            any(e["type"] == "token" for e in seen)
            and streamed.reply == "streamed answer")

        # ── safety ───────────────────────────────────────────────────────
        from .permissions import check as permission_check
        check("unattended_outbound",
              "An unattended agent cannot mail a stranger")(
            not permission_check("send_email",
                                 {"to": "stranger@evil.test"}).allowed)
        check("no_escalation",
              "An unattended agent cannot create automations")(
            not permission_check("create_routine", {"name": "x"}).allowed)
        check("reads_stay_free", "Reading and note-taking are never gated")(
            permission_check("set_reminder", {"message": "x"}).allowed)

        # ── tool hygiene ─────────────────────────────────────────────────
        bad = tools_mod.run_tool("add_task", {"nope": 1})
        unknown = tools_mod.run_tool("no_such_tool", {})
        check("tool_validation",
              "Model-written tool arguments are validated, not trusted")(
            "Schema validation error" in bad and "Unknown tool" in unknown)

        # ── the user's connectors ────────────────────────────────────────
        # Faked, because a real MCP server is a subprocess and a scorecard must
        # not need one installed to say whether the wiring works. What is being
        # measured is exactly the wiring: a connector's read tool reaches the
        # model and its answer lands in the turn, and its write tools are never
        # offered at all — those go through propose → confirm (permissions.py).
        from types import SimpleNamespace

        def _ref(tool: str, writes: bool) -> SimpleNamespace:
            return SimpleNamespace(
                qualified_name=f"demo:{tool}", server_id="demo",
                server_label="Demo", tool=tool, description=f"{tool} on Demo.",
                parameters={"type": "object", "properties": {}}, writes=writes)

        fake_connectors = SimpleNamespace(
            list_tools=lambda: [_ref("list_items", False), _ref("create_item", True)],
            call_tool=lambda _name, _args: "one item")
        _swap(mcp, "_supplier", lambda: fake_connectors)
        mcp.clear_cache()

        provider = _scripted([[("demo_list_items", {})], "there is one item"])
        use(provider)
        # With the connector attached to the message, the way `@` does it. An
        # agent must be allowed before it reaches a connector, so a turn that
        # grants nothing is measuring the gate rather than the tool path.
        connected = runtime.run_turn("research", "what is in demo?",
                                     effort="medium", connectors=["demo"])
        offered = provider.tools_offered[0] if provider.tools_offered else []
        check("connector_tools",
              "An agent can read the user's own connectors mid-turn")(
            any(s.name == "demo_list_items" and "one item" in s.result
                for s in connected.trace if s.kind == "tool_result"))
        check("connector_writes_withheld",
              "A connector's write tools are never offered to a model")(
            "demo_list_items" in offered
            and not any("create_item" in n for n in offered))

        # ── and the same call, with nothing granted ──────────────────────
        provider = _scripted([[("demo_list_items", {})], "I could not read it"])
        use(provider)
        ungranted = runtime.run_turn("research", "what is in demo?", effort="medium")
        refused = [s for s in ungranted.trace
                   if s.kind == "tool_result" and "permission" in s.result]
        check("connector_permission",
              "An agent asks before it reaches a connector")(
            bool(refused), refused[0].result[:48] if refused else "reached it anyway")

        # ── a connector WRITE, and the standing grant for one ────────────
        #
        # The capability is not "can it write" — it could always do that after
        # a tap. It is that the repeated write stops asking, per `server:tool`,
        # without that being a blank cheque for the server. Three facts, and
        # the third is the one worth a case: a grant is a statement about a
        # tool the user saw, so it cannot cover a verb nobody can take back.
        from . import permissions as _perm

        _tool = {"server_id": "demo", "tool": "create_item"}
        before = _perm.check("mcp_action", _tool)
        _perm.grant("demo:create_item", kind=_perm.TOOL_RECIPIENT,
                    note="scorecard")
        try:
            after = _perm.check("mcp_action", _tool)
            sibling = _perm.check("mcp_action",
                                  {"server_id": "demo", "tool": "delete_item"})
        finally:
            _perm.revoke("demo:create_item", kind=_perm.TOOL_RECIPIENT)

        check("connector_write_grant",
              "A repeated connector write can stop asking, one tool at a time")(
            not before.allowed and after.allowed and not sibling.allowed,
            f"before={before.allowed} after={after.allowed} "
            f"sibling={sibling.allowed}")
        check("connector_write_grant_bounded",
              "No standing grant can cover a write that cannot be undone")(
            not sibling.allowed and "cannot be undone" in sibling.reason,
            sibling.reason[:60])

        # ── changing the inbox, as one approved batch ────────────────────
        #
        # An agent could send mail for as long as this project existed and
        # could not archive one message: `gmail.send` cannot touch a message
        # that already exists. The capability is the whole loop — ids from
        # `list_mail`, one proposal covering all of them, one card.
        from ..actions import parse_actions
        from .approvals import describe as describe_action

        tools_mod.TOOL_IMPLS["list_mail"] = lambda **kw: (
            "- id=m1 thread=t1 [unread]\n  subject: Flash sale\n"
            "- id=m2 thread=t2\n  subject: Weekly newsletter")
        provider = _scripted([
            [("list_mail", {"query": "in:inbox"})],
            'Tidying these two.\n<action type="mail_triage">'
            '{"items":[{"id":"m1","do":"archive","subject":"Flash sale"},'
            '{"id":"m2","do":"archive","subject":"Weekly newsletter"}]}</action>',
        ])
        use(provider)
        triaged = runtime.run_turn("inbox", "clear my inbox", effort="medium",
                                   connectors=["gmail"])
        reply = triaged.reply or ""
        proposals = parse_actions(reply)
        batch = proposals[0]["params"]["items"] if proposals else []
        check("mail_triage",
              "An agent can change the inbox, not only read it")(
            len(proposals) == 1 and len(batch) == 2,
            f"{len(proposals)} card(s) for {len(batch)} email(s)")
        check("mail_triage_card",
              "The batch card names the emails and shows no internals")(
            bool(batch)
            and "Flash sale" in describe_action("mail_triage", {"items": batch})
            and "m1" not in describe_action("mail_triage", {"items": batch}))

        # ── the whole job: "clear the emails that don't need me" ─────────
        #
        # Every part of this existed separately — ids from `list_mail`, one
        # `mail_triage` for the batch, `create_draft` for the replies, `<plan>`
        # to put them under one button — and four capabilities is not the same
        # as the one job people actually ask for. This is the assembly, scored.
        #
        # The check that matters is not "did it emit a plan": it is that the
        # message it drafted a reply to is NOT also in the archive batch. An
        # email quietly archived under a reply is one the user will not know to
        # look for, and it is the way this job fails silently.
        from ..actions import parse_plans

        tools_mod.TOOL_IMPLS["list_mail"] = lambda **kw: (
            "- id=m1 thread=t1 [unread]\n  from: news@shop.test\n  subject: Flash sale\n"
            "- id=m2 thread=t2\n  from: list@weekly.test\n  subject: Weekly newsletter\n"
            "- id=m3 thread=t3 [unread]\n  from: ci@build.test\n  subject: Build passed\n"
            "- id=m4 thread=t4 [unread]\n  from: rahul@work.test\n  subject: Proposal?")
        provider = _scripted([
            [("list_mail", {"query": "in:inbox"})],
            "Four emails. Two are noise, one is an FYI, and Rahul is waiting.\n"
            '<plan rationale="4 emails — 2 newsletters, 1 to mark read, 1 needs you">\n'
            '<action type="mail_triage">'
            '{"items":[{"id":"m1","do":"archive","subject":"Flash sale"},'
            '{"id":"m2","do":"archive","subject":"Weekly newsletter"},'
            '{"id":"m3","do":"mark_read","subject":"Build passed"}]}</action>\n'
            '<action type="create_draft" to="rahul@work.test" '
            'subject="Re: Proposal?" thread_id="t4">Sending it tonight.</action>\n'
            "</plan>",
        ])
        use(provider)
        cleared = runtime.run_turn("inbox", "clear the emails that don't need me",
                                   effort="medium", connectors=["gmail"])
        plans = parse_plans(cleared.reply or "")
        steps = plans[0].steps if plans else []
        triaged_ids = {i.get("id") for s in steps if s["type"] == "mail_triage"
                       for i in s["params"].get("items", [])}
        drafted = [s for s in steps if s["type"] == "create_draft"]

        check("inbox_clearing",
              "Clearing the inbox is one plan, not seventeen cards")(
            len(plans) == 1 and len(steps) == 2,
            f"{len(plans)} plan(s) of {len(steps)} step(s)")
        check("inbox_clearing_drafts",
              "What needs a reply gets a draft, not an archive")(
            len(drafted) == 1 and "m4" not in triaged_ids,
            f"{len(drafted)} draft(s); triaged {sorted(triaged_ids)}")
        check("inbox_clearing_gate",
              "The whole batch still waits for one tap")(
            parse_plans(cleared.reply or "")[0].risk().value == "red"
            if plans else False,
            "a plan is as risky as its worst step")

        # ── job 2: "draft replies to anything waiting on me" ─────────────
        #
        # The mirror of chasing, and it fails the same way with the roles
        # swapped. Mail stays in the inbox after you answer it, so an agent
        # working from `list_mail` drafts a second reply to a conversation
        # that finished on Tuesday — and unlike a stale follow-up, that draft
        # is sitting in the user's Drafts with their name on it.
        #
        # So the scored fact is not "did it draft": it is that the thread
        # `needs_reply` reported under COULD NOT CHECK was left alone, and
        # that nothing was SENT. Job 2 stops at rung 3 deliberately.
        tools_mod.TOOL_IMPLS["needs_reply"] = lambda **kw: (
            "WAITING ON YOU — they wrote last and you have not answered:\n"
            "- Rahul — \"Proposal?\", 4 day(s) waiting  (thread t_open)\n\n"
            "COULD NOT CHECK — I do not know whether these are answered, so "
            "do not assume either way:\n"
            "- \"Invoice\" — could not read that thread (thread t_unknown)\n\n"
            "Not listed: 1 already answered.")
        tools_mod.TOOL_IMPLS["read_thread"] = lambda **kw: (
            "Rahul wrote: can you send the proposal before Friday?")
        provider = _scripted([
            [("needs_reply", {})],
            [("read_thread", {"thread_id": "t_open"})],
            "One I could draft; one I could not check, so I left it.\n"
            '<plan rationale="2 waiting on you — 1 drafted, 1 I could not check">\n'
            '<action type="create_draft" to="rahul@work.test" '
            'subject="Re: Proposal?" thread_id="t_open">'
            'Sending it before Friday.</action>\n'
            "</plan>",
        ])
        use(provider)
        replied = runtime.run_turn(
            "inbox", "draft replies to anything waiting on me",
            effort="medium", connectors=["gmail"])
        reply_text = replied.reply or ""
        reply_plans = parse_plans(reply_text)
        reply_steps = reply_plans[0].steps if reply_plans else []
        # `auto_recall` is not a choice the model made — the loop runs it on
        # every turn — so the question is which tool it reached for FIRST, and
        # whether it was the inbox listing rather than the check.
        chosen = [s.name for s in replied.trace
                  if s.kind == "tool_result" and s.name != "auto_recall"]

        check("needs_reply_checked_first",
              "Drafting replies starts by asking who actually wrote last")(
            bool(chosen) and chosen[0] == "needs_reply"
            and "list_mail" not in chosen,
            f"tools used: {chosen}")
        check("reply_drafting",
              "What is waiting on the user gets a draft in its own thread")(
            len(reply_steps) == 1
            and reply_steps[0]["type"] == "create_draft"
            and reply_steps[0]["params"].get("thread_id") == "t_open",
            f"{len(reply_steps)} step(s): "
            f"{[s['type'] for s in reply_steps]}")
        check("reply_drafting_never_sends",
              "Job 2 prepares; the user is still the send button")(
            not any(s["type"] == "send_email" for s in reply_steps),
            "it sent instead of drafting")
        check("reply_drafting_leaves_the_unverified_alone",
              "A thread we could not read is not one we know is unanswered")(
            "t_unknown" not in reply_text,
            "it wrote to a thread nobody could check")

        # ── job 3: "reply to this thread saying X" ────────────────────────
        #
        # One conversation, and the user has already decided the content — so
        # the capability being scored is addressing, not composition. Both
        # ways it goes wrong are envelope failures: a reply with no
        # `thread_id` starts a new conversation, and a reply addressed from
        # the subject line goes to whoever STARTED the thread rather than
        # whoever asked last. On a long thread those are different people.
        tools_mod.TOOL_IMPLS["read_thread"] = lambda **kw: (
            "1. dana@work.test: kicking this off\n"
            "2. lee@work.test: adding Rahul\n"
            "3. rahul@work.test: when can you get this to us?")
        provider = _scripted([
            [("gmail_search", {"query": "budget"})],
            [("read_thread", {"thread_id": "t_budget"})],
            "Telling Rahul it lands tonight.\n"
            '<action type="create_draft" to="rahul@work.test" '
            'subject="Re: Budget" thread_id="t_budget">'
            'Sending it tonight.</action>',
        ])
        use(provider)
        one = runtime.run_turn(
            "inbox", "reply to the budget thread saying I'll send it tonight",
            effort="medium", connectors=["gmail"])
        # Named `reply` because `test_only_the_reply_is_ever_handed_to_
        # parse_actions` greps for exactly that: the guard keeps a list of
        # *reply* spellings so no local variable can become a hole in it.
        reply = one.reply or ""
        one_props = parse_actions(reply)
        one_params = one_props[0]["params"] if one_props else {}

        check("thread_reply_lands_in_the_thread",
              "A reply to a conversation stays in that conversation")(
            one_params.get("thread_id") == "t_budget",
            f"thread_id={one_params.get('thread_id')!r}")
        check("thread_reply_answers_who_asked",
              "It answers whoever asked last, not whoever started the thread")(
            "rahul@work.test" in str(one_params.get("to", "")),
            f"to={one_params.get('to')!r}")

        # ── job 13: "turn this thread into a task" ───────────────────────
        #
        # Every part of this existed and the job did not work, because the
        # sentence was all that survived: `add_task` was a TOOL, so the task
        # never reached the action log and nothing carried the thread across.
        # Three weeks later the task says what to do and the user searches
        # their inbox for the conversation anyway.
        #
        # So the scored fact is the link, not the filing.
        tools_mod.TOOL_IMPLS["read_thread"] = lambda **kw: (
            "rahul@work.test: can you send the revised figures before Friday?")
        provider = _scripted([
            [("read_thread", {"thread_id": "t_budget"})],
            "Filed it, with a way back to the thread.\n"
            '<action type="create_task" due="friday" thread_id="t_budget">'
            'Send Rahul the revised figures</action>',
        ])
        use(provider)
        filed = runtime.run_turn(
            "inbox", "turn this thread into a task", effort="medium",
            connectors=["gmail"])
        reply = filed.reply or ""
        filed_props = parse_actions(reply)
        filed_params = filed_props[0]["params"] if filed_props else {}

        check("thread_to_task",
              "A conversation becomes a task that knows where it came from")(
            len(filed_props) == 1
            and filed_props[0]["type"] == "create_task"
            and filed_params.get("thread_id") == "t_budget",
            f"{len(filed_props)} action(s), "
            f"thread_id={filed_params.get('thread_id')!r}")

        # And the round trip, because the card is not the capability: the
        # action has to actually store the link, verify itself and be
        # reversible — the rungs `add_task` never reached.
        from ..actions import run_now as _run_now
        from ..tasks import get_tasks as _get_tasks

        made = _run_now("create_task", {"title": "Send the figures",
                                        "thread_id": "t_budget"})
        stored = _get_tasks().get(made.get("id") or "") or {}
        check("thread_to_task_round_trip",
              "The task is verified, logged, and can be taken back")(
            made.get("ok") and made.get("verified") is True
            and bool(made.get("log_id")) and made.get("reversible") is True
            and stored.get("source_ref") == "t_budget",
            f"verified={made.get('verified')} log={bool(made.get('log_id'))} "
            f"ref={stored.get('source_ref')!r}")
        # The scorecard runs against the user's real stores, so it puts back
        # what it created rather than leaving a task nobody asked for.
        from ..actions import REGISTRY as _REGISTRY

        _undo = _REGISTRY["create_task"].undo
        if _undo is not None:                       # declared, and checked above
            _undo({}, made)

        # ── job 9: "prep me for my next meeting" ─────────────────────────
        #
        # Five lookups behind one question, and the one an agent skips is
        # always the same one — it stops as soon as it can write a paragraph
        # that sounds prepared. So the assembly is a tool, and what is scored
        # is that the agent reaches for it rather than rebuilding it badly
        # out of `calendar_lookup` plus a guess.
        tools_mod.TOOL_IMPLS["meeting_prep"] = lambda **kw: (
            "NEXT: “Budget review” — Mon 21 Sep, 2:00 PM\n\n"
            "WHO: rahul@work.test, dana@work.test\n\n"
            "WHAT YOU KNOW:\n"
            "rahul@work.test:\n  - asked for the revised figures\n\n"
            "dana@work.test: nothing in the brain about them yet.\n\n"
            "YOU OWE:\n- Send Rahul the revised figures (due 2026-09-25)")
        provider = _scripted([
            [("meeting_prep", {})],
            "Budget review at 2, with Rahul and Dana. Rahul is still waiting "
            "on the revised figures — that is the thing to have ready. I have "
            "nothing on Dana yet.",
        ])
        use(provider)
        prepped = runtime.run_turn(
            "chief-of-staff", "prep me for my next meeting", effort="medium",
            connectors=["gcal"])
        prep_used = [s.name for s in prepped.trace
                     if s.kind == "tool_result" and s.name != "auto_recall"]
        # Named `reply` for the source guard in
        # `test_only_the_reply_is_ever_handed_to_parse_actions`.
        reply = prepped.reply or ""
        prep_text = reply.lower()

        check("meeting_prep_used",
              "Prepping for a meeting is one call, not five it might skip")(
            prep_used[:1] == ["meeting_prep"],
            f"tools used: {prep_used}")
        check("meeting_prep_surfaces_what_you_owe",
              "The brief leads with the thing the user has not done")(
            "figures" in prep_text,
            prep_text[:70])
        check("meeting_prep_acts_on_nothing",
              "Job 9 stops at prepared — it is a briefing, not a decision")(
            parse_actions(reply) == [],
            "it proposed an action nobody asked for")

        # ── job 10: "tell Rahul I'll send it tonight" ────────────────────
        #
        # The messaging mirror of job 3, and the stakes are higher for a
        # reason that is not about words: there is no draft. A message is on
        # somebody's phone the instant it is approved, and no app here lets
        # us unsend — so the scored fact is that the chat id came from
        # `list_chats` rather than from the model's imagination.
        #
        # And the second: a named time must WAIT. `execute()` honoured `at`
        # for a hardcoded pair of actions and `message_send` was never added
        # to it, so "tell him at six" sent immediately and said nothing.
        tools_mod.TOOL_IMPLS["list_chats"] = lambda **kw: (
            "Telegram — 2 conversation(s):\n"
            "- Rahul Mehta  (telegram id 4411)\n"
            "- Standup  (telegram id 9902)")
        provider = _scripted([
            [("list_chats", {})],
            "Telling Rahul.\n"
            '<action type="message_send" app="telegram" chat="4411">'
            "I'll send it tonight.</action>",
        ])
        use(provider)
        told = runtime.run_turn(
            "chief-of-staff", "tell Rahul I'll send it tonight",
            effort="medium", connectors=["telegram"])
        reply = told.reply or ""
        told_props = parse_actions(reply)
        told_params = told_props[0]["params"] if told_props else {}
        told_used = [s.name for s in told.trace
                     if s.kind == "tool_result" and s.name != "auto_recall"]

        check("message_chat_looked_up",
              "A message goes to a conversation that was looked up, not guessed")(
            told_used[:1] == ["list_chats"]
            and told_params.get("chat") == "4411",
            f"tools={told_used} chat={told_params.get('chat')!r}")

        # The scheduling half, checked against the registry rather than a
        # scripted turn: what matters is that `execute` cannot silently send
        # now, and that is a property of the action, not of one model's reply.
        check("message_can_wait_until_a_named_time",
              "\"Tell him at six\" waits until six instead of going out now")(
            _REGISTRY["message_send"].schedulable is True
            and "at" in _REGISTRY["message_send"].fields,
            "a named time would be dropped and the message sent immediately")

        # Put the real one back. The `messaging` check further down drives the
        # genuine `list_chats` against a fake connector, and a canned string
        # left in its place made it report that the user had no conversations.
        tools_mod.TOOL_IMPLS["list_chats"] = saved_impls["list_chats"]

        # ── writing to the work surfaces ─────────────────────────────────
        #
        # Linear and Notion are reached as CUSTOM SOURCES, so a write to
        # either is an `mcp_action` with a per-tool grant — already scored
        # above. The named actions that used to sit here drove the built-in
        # connectors, and having both meant the agent could pick the route
        # the user had not set up and fail in front of a green CONNECTED
        # badge. One route, and it is the vendor's own server.
        #
        # Drive stays first-party: it is Google OAuth, which is already
        # connected for mail and calendar, and there is no Drive MCP server.
        from ..actions import Risk

        check("drive_write",
              "A document can be created in the user's own Drive")(
            "drive_create_doc" in _REGISTRY
            and _REGISTRY["drive_create_doc"].risk is Risk.GREEN,
            "it is not green, so overnight preparation needs a tap")
        check("work_surface_writes_have_one_route",
              "Notion and Linear are written to one way, not two")(
            not any(n.startswith(("notion_", "linear_")) for n in _REGISTRY),
            "a built-in write action came back alongside the MCP route")

        # ── renaming and moving, with both ends inside a granted folder ──
        check("move_file",
              "A file can be renamed or moved, and never out of the sandbox")(
            "move_file" in tools_mod.TOOL_DEFS
            and "_resolve(to)" in _inspect.getsource(file_tools_mod.move_file),
            "the destination is not checked against the folder grants")

        # ── chasing only the people who actually owe an answer ───────────
        #
        # The failure worth scoring is not "did it draft a chase". It is
        # chasing somebody who already replied: the mail goes out in the
        # user's name and cannot be taken back, and it is what an agent does
        # by default because the commitment list is right there and reads as
        # current. So the scripted model is given a list containing one
        # answered thread and one that could not be read, and the check is
        # that neither of them is written to.
        from .followup_tools import awaiting_reply as _awaiting

        tools_mod.TOOL_IMPLS["awaiting_reply"] = lambda **kw: (
            "WORTH CHASING (3+ days, no reply):\n"
            "- Priya: the contract — priya@work.test, 9 day(s) with no reply"
            "  (thread t_stale, id aaa111)\n\n"
            "COULD NOT CHECK — do not chase these:\n"
            "- Lee: the invoice (id ccc333) — could not read that thread\n\n"
            "ANSWERED since you asked, now closed:\n"
            "- Rahul: the proposal — Rahul replied.")
        provider = _scripted([
            [("awaiting_reply", {"stale_days": 3})],
            "Rahul replied, so that one is closed. Lee's thread I could not "
            "read, so I have left it.\n"
            '<plan rationale="1 worth chasing; Rahul replied, Lee unchecked">\n'
            '<action type="create_draft" to="priya@work.test" '
            'subject="Re: the contract" thread_id="t_stale">Just checking in '
            "on this.</action>\n"
            "</plan>",
        ])
        use(provider)
        chased = runtime.run_turn("inbox", "follow up with whoever hasn't replied",
                                  effort="medium", connectors=["gmail"])
        plans = parse_plans(chased.reply or "")
        wrote_to = {s["params"].get("to") for p in plans for s in p.steps}
        tools_used = [s.name for s in chased.trace if s.kind == "tool_call"]

        check("followup_checks_first",
              "A chase is written from the thread, never from memory")(
            "awaiting_reply" in tools_used, f"tools used: {tools_used}")
        check("followup_spares_the_answered",
              "Nobody who already replied gets chased")(
            "rahul@work.test" not in wrote_to and len(wrote_to) == 1,
            f"wrote to {sorted(wrote_to)}")
        check("followup_spares_the_unchecked",
              "Silence we could not verify is not a reason to email anybody")(
            "lee@work.test" not in wrote_to,
            f"wrote to {sorted(wrote_to)}")
        assert _awaiting is not None        # the tool the recipe names exists

        # ── moving a meeting to a slot it picked itself ──────────────────
        #
        # The API half of this is ordinary. The half worth scoring is rung 3:
        # the user said "Friday afternoon", and an agent that answers "what
        # time on Friday works for you?" has handed the job back. So the
        # scripted calendar has a Friday with one thing already on it, and the
        # check is that the proposal carries a CONCRETE time which does not
        # collide with it.
        tools_mod.TOOL_IMPLS["calendar_lookup"] = lambda **kw: (
            "Calendar, 2026-09-25:\n"
            "- Client call, Thu 24 Sep 15:00–16:00\n  id=ev_client\n"
            "- Standup, Fri 25 Sep 14:00–14:30\n  id=ev_standup\n"
            "\nUse the id to move or cancel one. Never guess an id.")
        provider = _scripted([
            [("calendar_lookup", {"when": "this week"})],
            "Friday afternoon has standup at 14:00, so I have put it at 15:30 "
            "— the next clear hour.\n"
            '<action type="update_event" event_id="ev_client" '
            'start="2026-09-25T15:30:00+05:30"></action>',
        ])
        use(provider)
        moved = runtime.run_turn("personal", "move the client call to Friday "
                                 "afternoon", effort="medium",
                                 connectors=["gcal"])
        # Named `reply` so the source guard in
        # `test_only_the_reply_is_ever_handed_to_parse_actions` can see what
        # this is: a model reply and nothing else.
        reply = moved.reply or ""
        proposals = parse_actions(reply)
        picked = proposals[0]["params"] if proposals else {}

        check("calendar_move",
              "A meeting can be moved, not only created")(
            len(proposals) == 1 and picked.get("type", "update_event"),
            f"{len(proposals)} proposal(s)")
        check("calendar_move_id",
              "The event is addressed by an id that came from the calendar")(
            picked.get("event_id") == "ev_client",
            f"event_id={picked.get('event_id')!r}")
        check("calendar_move_concrete",
              "A vague time becomes a specific one, not a question back")(
            "T" in str(picked.get("start") or "")
            and "14:00" not in str(picked.get("start") or ""),
            f"start={picked.get('start')!r}")
        from .permissions import NEVER_UNATTENDED

        check("calendar_move_gate",
              "Moving a meeting still waits for a tap")(
            "update_event" in NEVER_UNATTENDED,
            "it emails everybody in the meeting")

        # ── arranging something with somebody we cannot see ──────────────
        #
        # The failure worth scoring is not "did it find a slot". It is
        # asserting that a slot works for a person whose calendar is not
        # shared — Google answers an unreadable calendar with an empty busy
        # list, so the naive read is "completely free all week". Most people
        # outside the user's own company are exactly that case.
        tools_mod.TOOL_IMPLS["find_time"] = lambda **kw: (
            "30-minute slots in next week (09:00–18:00 weekdays):\n"
            "- Tue 22 Sep, 10:00 AM  →  2026-09-22T10:30:00+05:30\n"
            "- Wed 23 Sep, 2:00 PM  →  2026-09-23T14:30:00+05:30\n\n"
            "Checked against: your calendar\n"
            "NOT checked (their calendar is not shared with you): "
            "rahul@work.test. Offer these times, do not assert they are free "
            "for them.")
        provider = _scripted([
            [("find_time", {"with_people": "rahul@work.test",
                            "when": "next week"})],
            "Two slots are clear in your week. I could not see Rahul's "
            "calendar, so these are offers rather than confirmations.\n"
            '<action type="create_draft" to="rahul@work.test" '
            'subject="Time next week?">Would Tue 22nd 10:00 or Wed 23rd 14:00 '
            "suit you?</action>",
        ])
        use(provider)
        arranged = runtime.run_turn("personal", "find a time with Rahul next "
                                    "week", effort="medium", connectors=["gcal"])
        said = (arranged.reply or "")
        used = [s.name for s in arranged.trace if s.kind == "tool_call"]

        check("find_time_checks",
              "A time is proposed from the calendar, not from thin air")(
            "find_time" in used, f"tools used: {used}")
        check("find_time_offers",
              "An unshared calendar is an offer, not a claim that they are free")(
            "could not see" in said.lower() or "not checked" in said.lower(),
            said[:80])
        check("find_time_concrete",
              "The user is given times, not asked for one")(
            "10:00" in said or "14:00" in said, said[:80])

        # ── sending a file, and saying which one ─────────────────────────
        #
        # Attaching is the easy half. The half worth scoring is that the agent
        # NAMES the version it picked: attaching last quarter's draft is found
        # out by the person who receives it, not by the user. So the scripted
        # search returns three proposals a week apart and the check is that the
        # reply says which, with its date, and attaches that exact path.
        tools_mod.TOOL_IMPLS["find_file"] = lambda **kw: (
            "3 file(s) matching “proposal” — newest first:\n"
            "- Acme proposal.pdf\n  /Work/2026/Q3/Acme proposal.pdf\n"
            "  modified 18 Sep 2026, 09:12 · 84,210 bytes\n"
            "- proposal-draft.md\n  /Work/proposal-draft.md\n"
            "  modified 11 Sep 2026, 17:40 · 9,004 bytes\n"
            "- old proposal.pdf\n  /Work/2026/old proposal.pdf\n"
            "  modified 03 Jul 2025, 11:02 · 80,551 bytes")
        provider = _scripted([
            [("find_file", {"name": "proposal"})],
            "Three match. The newest is Acme proposal.pdf, modified 18 Sep "
            "2026 — the other two are a week and a year older.\n"
            '<action type="create_draft" to="rahul@work.test" '
            'subject="The proposal" attach="/Work/2026/Q3/Acme proposal.pdf">'
            "Attached, as promised.</action>",
        ])
        use(provider)
        attached = runtime.run_turn("personal", "send Rahul the latest proposal",
                                    effort="medium")
        reply = attached.reply or ""
        proposed = parse_actions(reply)
        params = proposed[0]["params"] if proposed else {}

        check("attach_searches",
              "A file is attached from a search, not from a guessed path")(
            "find_file" in [s.name for s in attached.trace
                            if s.kind == "tool_call"],
            "nothing looked for the file")
        check("attach_names_the_version",
              "The user is told WHICH version, with its date")(
            "18 Sep" in reply and "Acme proposal.pdf" in reply,
            reply[:90])
        check("attach_sends_what_it_named",
              "The path attached is the one it said it chose")(
            params.get("attach") == "/Work/2026/Q3/Acme proposal.pdf",
            f"attach={params.get('attach')!r}")

        # ── messaging, across whichever app it is on ─────────────────────
        #
        # Two apps landed together because one app is a feature and two is a
        # contract: the tools, the action and the card must not know which one
        # they are talking to, or a third app costs a copy of all three.
        from .. import messaging as messaging_mod
        from ..messaging import Chat

        class _App:
            name, label = "demo_chat", "DemoChat"

            def is_configured(self):
                return True, ""

            def chats(self, limit=30):
                return [Chat(id="101", name="Dana", kind="dm", unread=1)]

            def history(self, chat_id, limit=50):
                return []

            def send(self, chat_id, text):
                return {"ok": True, "detail": "sent"}

        saved_apps = messaging_mod.apps
        _swap(messaging_mod, "apps", lambda: [_App()])
        try:
            provider = _scripted([
                [("list_chats", {})],
                'I will tell her.\n<action type="message_send" app="demo_chat" '
                'chat="101">On my way.</action>',
            ])
            use(provider)
            chatted = runtime.run_turn("inbox", "tell Dana I am coming",
                                       effort="medium", connectors=["demo_chat"])
            listed = [s for s in chatted.trace
                      if s.kind == "tool_result" and "id=101" in s.result]
            reply = chatted.reply or ""
            proposed = parse_actions(reply)
            check("messaging",
                  "An agent can read the user's messaging apps, whichever they are")(
                bool(listed), "list_chats returned nothing addressable")
            check("messaging_send",
                  "Sending a message is proposed, never done unattended")(
                len(proposed) == 1
                and proposed[0]["params"].get("app") == "demo_chat",
                f"{len(proposed)} proposal(s)")
        finally:
            _swap(messaging_mod, "apps", saved_apps)

        # ── measuring, rather than remembering a number ──────────────────
        #
        # The Health agent was told to "review honestly" with nothing to review:
        # every weight went into the brain as prose, so progress was answered by
        # estimating from recall. The capability is that a trend now comes back
        # as arithmetic the agent did not have to do.
        from datetime import UTC, datetime, timedelta

        from .. import metrics as metrics_mod

        base = (datetime.now(UTC) - timedelta(days=60)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        metrics_mod.log_many(
            [{"metric": "weight", "unit": "kg",
              "value": 70 + i * 0.05 + (1.2 if i % 2 else -1.1),
              "at": (base + timedelta(days=i)).isoformat()}
             for i in range(60)], source="evaluation")
        try:
            summary = metrics_mod.summarise("weight", days=90)
            trend = summary.get("trend") or {}
            check("measurements",
                  "A trend is measured and smoothed, not estimated from recall")(
                summary.get("n") == 60 and 2.0 < trend.get("change", 0) < 4.0,
                f"{summary.get('n')} readings, trend {trend.get('change')}")

            provider = _scripted([[("whats_tracked", {})],
                                  [("measurement_history", {"metric": "weight"})],
                                  "you are up about 3 kg over the two months"])
            use(provider)
            reviewed = runtime.run_turn("health", "am I actually gaining?",
                                        effort="medium")
            looked = [s.name for s in reviewed.trace if s.kind == "tool_result"]
            check("health_checks_before_judging",
                  "The health agent looks at the data before saying anything")(
                "whats_tracked" in looked or "measurement_history" in looked,
                f"tools used: {looked}")
        finally:
            metrics_mod.forget("weight", source="evaluation")

    finally:
        _swap(mcp, "_supplier", saved_supplier)
        mcp.clear_cache()
        tools_mod.TOOL_IMPLS.clear()
        tools_mod.TOOL_IMPLS.update(saved_impls)
        _swap(runtime, "get_provider", saved_provider)
        _swap(runtime, "resolve_usable_model", saved_resolve)

    return card
