"""Agent runtime: the model + tool-use loop that makes an agent *do* things."""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ..brain import get_brain
from ..config import get_settings
from ..log import get_logger, suppressed
from ..models import Message, get_provider, tool_bridge
from ..models.base import ChatResult
from ..models.entitlements import resolve_usable_model
from . import background, cancellation, connector_grants, context, delegation, grounding, planning
from .agent import Agent, AgentMemory
from .context import build_history
from .effort import Effort, get_effort
from .loop import BUDGET_PROMPT, STALL_LIMIT, ToolRunner
from .presets import get_agent
from .tools import build_tools

#: What a stopped turn says after whatever it had already written. The user
#: pressed the button, so this confirms rather than apologises.
STOPPED_NOTE = "■ Stopped."

#: Given to the model when the turn's *money* runs out rather than its rounds.
#: Worded separately from BUDGET_PROMPT because the two are different facts and
#: a model that is told the wrong one reasons about the wrong constraint.
SPEND_PROMPT = (
    "You have spent this turn's budget. Do not call any more tools and do not "
    "ask another agent. Answer now with what you have, and say plainly which "
    "parts you could not finish."
)

#: Replaced by the per-turn budget in `effort.py`. Kept as the floor a turn
#: can never drop below, so a misconfigured profile cannot produce a loop that
#: never calls a tool at all.
MIN_STEPS = 3

log = get_logger(__name__)


@dataclass
class TraceStep:
    kind: str                       # "tool_call" | "tool_result"
    name: str = ""
    arguments: dict = field(default_factory=dict)
    result: str = ""
    repeated: bool = False          # the model asked for something it already had


@dataclass
class TurnResult:
    agent_id: str
    reply: str
    trace: list[TraceStep] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    runtime_identity: dict[str, Any] = field(default_factory=dict)
    effort: str = ""
    steps_used: int = 0
    plan: list[dict] = field(default_factory=list)
    #: The user pressed Stop. The reply is whatever had been written by then.
    stopped: bool = False
    #: What this turn cost, summed over every model call it made — including the
    #: ones a sub-agent made on its behalf. Effort has always been described as
    #: spending the user's money; this is the first version that can say how much.
    tokens_in: int = 0
    tokens_out: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "reply": self.reply,
            "provider": self.provider,
            "model": self.model,
            "runtime_identity": self.runtime_identity,
            "effort": self.effort,
            "steps_used": self.steps_used,
            "plan": self.plan,
            "stopped": self.stopped,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "trace": [
                {"kind": s.kind, "name": s.name, "arguments": s.arguments,
                 "result": s.result, "repeated": s.repeated}
                for s in self.trace
            ],
        }




_LEARN_SYS = (
    "From the user's message, extract ONLY durable facts they revealed about "
    "themselves, their work, people, preferences, or plans — things worth "
    "remembering long-term. Ignore questions, commands, and small talk. "
    'Return STRICT JSON: {"facts": ["...", "..."]}. Empty list if nothing durable. '
    "Write each fact as a standalone third-person statement (e.g. 'The user "
    "prefers X')."
)

# first-person cues that suggest the user is disclosing something durable
_DISCLOSURE = re.compile(
    r"\b(i am|i'm|my |i work|i live|i prefer|i like|i hate|i use|i build|"
    r"i'm building|i want|i need|remember that|i usually|i always|i own)\b",
    re.I,
)


def _auto_learn(user_text: str, provider) -> int:
    text = user_text.strip()
    # cheap gate: skip pure questions / anything with no self-disclosure
    if text.endswith("?") and not _DISCLOSURE.search(text):
        return 0
    if not _DISCLOSURE.search(text):
        return 0

    facts: list[str] = []
    ready, _ = provider.is_ready()
    if provider.name != "mock" and ready:
        try:
            res = provider.chat(
                [Message(role="system", content=_LEARN_SYS),
                 Message(role="user", content=text[:1500])],
                temperature=0, max_tokens=300,
            )
            raw = res.text
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            facts = [f for f in json.loads(raw).get("facts", []) if f.strip()]
        except Exception:
            facts = []
    if not facts:
        # heuristic fallback: store the disclosing sentence itself
        facts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                 if _DISCLOSURE.search(s)][:3]

    brain = get_brain()
    stored = 0
    for fact in facts[:5]:
        if brain.ingest(fact, source="agent", kind="fact", title="learned")["memories"]:
            stored += 1
    return stored


def _learn_from_turn(user_text: str, reply: str, provider) -> None:
    """Grow the brain from a finished turn. Runs after the user has their answer.

    Two steps, in order: raw-memory capture, which is the searchable evidence,
    then canonical curation, which is the durable versioned model of the user.
    The second is allowed to fail without disturbing the first.
    """
    _auto_learn(user_text, provider)
    with suppressed("from ..brain.canonical import get_canonical …"):
        from ..brain.canonical import get_canonical
        get_canonical().learn_from_conversation(user_text, reply, provider=provider)


PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "openai": "OpenAI",
    "claude": "Anthropic",
    "anthropic": "Anthropic",
    "claude-code": "Claude Code",
    "gemini": "Google",
    "google": "Google",
    "cursor": "Cursor",
    "xai": "xAI",
    "grok": "xAI",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "subscription": "Subscription",
    "mock": "Mock",
}


def build_runtime_identity(agent: Agent, provider: Any) -> dict[str, Any]:
    provider_name = getattr(provider, "name", "unknown")
    provider_display = PROVIDER_DISPLAY_NAMES.get(provider_name.lower(), provider_name.title())
    model_name = getattr(provider, "model", "") or "default"

    harness = None
    if provider_name == "openai":
        with suppressed("from ..models.chatgpt_auth import get_chatgpt_access_token …"):
            from ..models.chatgpt_auth import get_chatgpt_access_token
            if not getattr(provider, "api_key", None) and get_chatgpt_access_token():
                harness = "Codex harness"
    elif provider_name == "claude-code":
        harness = "Claude Code harness"
    elif provider_name == "cursor":
        harness = "Cursor session bridge"
    elif provider_name == "subscription":
        harness = "Subscription gateway"

    return {
        "application": "Chitragupta",
        "agent_id": agent.id,
        "agent_name": agent.name,
        "agent_role": getattr(agent, "role", ""),
        "provider": provider_display,
        "provider_name": provider_name,
        "model": model_name,
        "harness": harness,
    }


def format_runtime_context_prompt(identity: dict[str, Any]) -> str:
    app = identity.get("application", "Chitragupta")
    agent_name = identity.get("agent_name", "Assistant")
    role = identity.get("agent_role", "")
    provider = identity.get("provider", "Unknown")
    model = identity.get("model", "unknown")
    harness = identity.get("harness")

    lines = [
        "RUNTIME CONTEXT — AUTHORITATIVE",
        "",
        f"Application: {app}",
        f"Selected Agent: {agent_name}",
    ]
    if role:
        lines.append(f"Agent Role: {role}")
    lines.append(f"Provider: {provider}")
    lines.append(f"Model: {model}")
    if harness:
        lines.append(f"Harness: {harness}")

    role_desc = f"your {role} Agent" if role else f"the {agent_name} Agent"
    harness_suffix = f" through the {harness}" if harness else ""

    lines.extend([
        "",
        "The user selected this agent. The model/provider above are the actual runtime configuration for this turn.",
        "",
        'If asked "what model are you using?", "which AI are you using?", "what provider are you running on?", '
        'or similar questions, answer directly from this runtime context.',
        f"Example truthful answer: \"I'm {role_desc}, running on {provider}'s `{model}` model{harness_suffix}.\"",
        "- Do not claim the model is unknown if the Model field is populated.",
        "- Do not invent a different model.",
    ])
    return "\n".join(lines)


def _collect(events, emit, cancel=None) -> ChatResult:
    """Drain a provider stream, passing text on as it arrives.

    The whole response is still assembled, because the agent loop needs the
    tool calls from the same round — streaming only the prose would be simpler
    and would break it.

    Text is also accumulated as it goes, which only matters when the drain ends
    early: a turn stopped mid-sentence still has the sentence, and throwing it
    away would make Stop cost the user the answer they had already paid for.
    """
    result = None
    partial: list[str] = []
    for event in events:
        if cancellation.stopped(cancel):
            break
        if event.kind == "text" and event.text:
            partial.append(event.text)
            emit({"type": "token", "text": event.text})
        elif event.kind == "done":
            result = event.result
    return result if result is not None else ChatResult(text="".join(partial))


def _gated(runner: ToolRunner, name: str):
    """A handler that runs one tool the way the loop would.

    Bound to the name rather than closing over a loop variable: the list is
    built in a comprehension, and a closure over `t` would give every tool the
    last one's name — the classic version of this bug, and silent, because
    every call still works and simply runs the wrong tool.
    """
    def call(**arguments: Any) -> str:
        return str(runner.invoke(name, arguments))

    return call


def _watch(emit, trace: list[TraceStep]):
    """Report a tool a backend ran inside its own loop, as if we had run it.

    Same two events and the same trace steps the loop emits, because to the
    person watching it *is* the same thing: their agent looked something up.
    Which process the call happened in is our problem, not theirs.
    """
    def seen(name: str, arguments: dict, result: str) -> None:
        emit({"type": "tool_call", "name": name, "arguments": arguments})
        emit({"type": "tool_result", "name": name, "result": result[:2000],
              "repeated": False})
        trace.append(TraceStep(kind="tool_call", name=name,
                               arguments=arguments))
        trace.append(TraceStep(kind="tool_result", name=name, result=result))

    return seen


def _answer_without_tools(provider, messages, on_failure, emit):
    """One last call with tools withheld, to turn research into an answer.

    Returning the failure `TurnResult` rather than raising keeps the contract
    that a turn never raises — an uncaught error here is what turned a provider
    401 into a 500 the last time.
    """
    try:
        return _collect(provider.stream(messages, tools=None, temperature=0.15),
                        emit).text
    except Exception as exc:
        return on_failure(exc)


def run_turn(agent_id: str, user_text: str, *,
             provider_name: str | None = None,
             model_name: str | None = None,
             effort: str | Effort | None = None,
             images: list | None = None,
             cancel: threading.Event | None = None,
             connectors: list[str] | None = None,
             persist: bool = True,
             on_event: Callable[[dict], None] | None = None) -> TurnResult:
    """Run one turn for `agent_id` and return what it produced.

    `persist=False` makes the turn isolated: it reads the shared brain, but it
    neither reads nor writes the agent's own conversation. That is what a
    delegated question needs. Without it, asking Inbox something that it passes
    to Research put a message into the user's Research chat that they never
    typed — and then folded it into that agent's running summary, so one
    delegated question permanently distorted a conversation happening elsewhere.
    """
    agent = get_agent(agent_id)
    profile = effort if isinstance(effort, Effort) else get_effort(effort)
    # Streaming is a callback rather than a second implementation of the loop.
    # One code path answers whether or not anyone is watching it happen, which
    # is the only way the streamed turn and the plain one cannot drift.
    emit = on_event or (lambda _event: None)
    settings = get_settings()
    p_name = (provider_name.strip() if provider_name else None) or agent.model_provider or settings.model_provider
    m_name = (model_name.strip() if model_name else None) or agent.model_name
    # Only fall back to settings.model_name if the active provider matches the global default provider
    if not m_name and (p_name == settings.model_provider or not agent.model_provider):
        m_name = settings.model_name
    # A stored model id can outlive the provider's catalog (an agent binding or a
    # saved preference made before a model was retired). Re-check it against what
    # this account offers now, so we substitute instead of 400-ing mid-chat.
    m_name, replaced_model = resolve_usable_model(p_name, m_name)
    if (replaced_model and not model_name and replaced_model == agent.model_name):
        # The agent's own saved binding pointed at a model that no longer works.
        # Repair it so the picker stops showing a dead id, instead of silently
        # substituting on every future turn.
        with suppressed("from .agent_models import set_agent_model …"):
            from .agent_models import set_agent_model
            set_agent_model(agent_id, p_name, m_name)
    provider = get_provider(p_name, m_name)
    identity = build_runtime_identity(agent, provider)
    # Fail fast with a helpful message if the chosen backend isn't usable.
    ready, why = provider.is_ready()
    if not ready:
        return TurnResult(
            agent_id=agent_id,
            reply=f"⚠️ The **{provider.name}** model isn't ready: {why}.\n\n"
                  "Pick another model in the Model dropdown, or fix the backend "
                  "(e.g. run `ollama serve`, or set the API key).",
            provider=provider.name, model=provider.model,
            runtime_identity=identity,
        )
    # Attached images, refused BEFORE any spend. A model that cannot see, or a
    # backend with nowhere to put an image, is a fact we already know — sending
    # the turn anyway would bill the user for a failure we predicted.
    images = list(images or [])
    if images:
        from ..models.images import refusal_for
        refusal = refusal_for(images, provider, p_name, provider.model)
        if refusal:
            return TurnResult(
                agent_id=agent_id, reply=f"⚠️ {refusal}",
                provider=provider.name, model=provider.model,
                runtime_identity=identity,
            )

    mem = AgentMemory()
    tools = build_tools(agent.tools, self_id=agent.id, effort=profile)

    # Ground the agent in the present. LLMs have no clock, so without this they
    # hallucinate the date. Small models ignore mid-context system notes, so we
    # (a) state it in the system prompt AND (b) prepend it to the model-facing
    # user turn — right next to the question, where even a 3B model can't miss it.
    from datetime import datetime
    now = datetime.now().astimezone()
    date_line = f"{now:%A, %B %d, %Y}"
    time_line = f"{now:%-I:%M %p} {now:%Z}"

    runtime_prompt = format_runtime_context_prompt(identity)

    messages: list[Message] = [
        Message(role="system", content=runtime_prompt),
        Message(
            role="system",
            content=(
                agent.system_message()
                + f"\n\nRIGHT NOW it is {date_line}, {time_line}. This is the "
                  "authoritative current date — never state any other date as today. "
                  "Resolve 'today', 'tomorrow', 'this week' from this date."
            ),
        ),
    ]

    # Auto-recall: inject the relevant slice of the brain up front so the agent
    # *already knows the user* regardless of whether the (possibly small) model
    # decides to call search_brain. Tools remain for going deeper / live data.
    trace: list[TraceStep] = []
    recalled = get_brain().recall(
        user_text, limit=profile.recall_limit, prefer=agent.recall_sources or None)
    if recalled["context"]:
        messages.append(Message(role="system", content=recalled["context"]))
        trace.append(TraceStep(
            kind="tool_result", name="auto_recall",
            result=f"{len(recalled['memory_hits'])} memories, "
                   f"{len(recalled['entities'])} entities",
        ))

    # Ground task-capable agents in the ACTUAL current tasks (authoritative),
    # so they never invent or regurgitate stale tasks from chat history.
    if "list_tasks" in agent.tools:
        from ..tasks import get_tasks
        open_tasks = get_tasks().list()
        if open_tasks:
            lines = [f"- {t['title']}" + (f" (due {t['due']})" if t["due"] else "")
                     for t in open_tasks[:20]]
            messages.append(Message(
                role="system",
                content=("The user's CURRENT open tasks (this is the authoritative "
                         "list — use it; never invent tasks not shown here):\n"
                         + "\n".join(lines)),
            ))
        else:
            messages.append(Message(
                role="system",
                content="The user currently has NO open tasks. Do not claim otherwise.",
            ))

    # An isolated turn is one self-contained question, not a conversation: no
    # history in, and (below) nothing written out.
    if persist:
        messages += build_history(mem, agent, profile, provider)
    # model sees the date adjacent to the question; stored memory stays clean
    messages.append(Message(
        role="user", content=grounding.prefixed(date_line, user_text),
        images=images))
    if persist:
        mem.append(agent.id, "user", user_text)
    # Connectors the user attached to this message with `@`. Held for the turn
    # and released on every exit path below, so one message's grant cannot leak
    # into the next.
    grant_token = connector_grants.allow_for_this_turn(connectors)
    runner = ToolRunner(effort=profile, cancel=cancel, agent_id=agent.id)
    # A backend that has no tool-call protocol of its own — the three vendor
    # CLIs — is handed these same tools over MCP and runs them itself
    # (`models/tool_bridge.py`). It gets handlers that go through the runner
    # rather than the raw implementations, so a connector an agent may not
    # reach is refused there exactly as it is here. Providers that *do* return
    # tool calls never touch a handler: `run_tool` dispatches by name.
    tools = [replace(t, handler=_gated(runner, t.name)) for t in tools]
    # Left in the context rather than set on the provider: `get_provider` is
    # `@lru_cache`d, so one instance answers every concurrent turn and an
    # attribute there would draw one agent's tool calls into another's trace.
    watch_token = tool_bridge.observe(_watch(emit, trace))
    budget = max(MIN_STEPS, profile.max_steps)
    chain_token = delegation.enter(agent.id, profile, cancel)
    # Shared with every sub-agent this turn reaches, so three agents at High
    # spend one budget between them rather than three.
    ledger = delegation.current_chain().spend
    plan_token = planning.start()
    stalls = 0
    reply = ""
    was_stopped = False
    spent = [0, 0]      # input, output tokens across every call this turn

    def _failed(exc: Exception) -> TurnResult:
        hint = ""
        if provider.name == "ollama":
            hint = " Is Ollama running? Start it with `ollama serve`."
        elif provider.name in ("anthropic", "openai", "openrouter"):
            hint = " Check the API key and your connection."
        return TurnResult(
            agent_id=agent_id,
            reply=f"⚠️ The **{provider.name}** model failed: {str(exc)[:200]}.{hint}",
            trace=trace, provider=provider.name, model=provider.model,
            runtime_identity=identity, effort=profile.name,
        )

    # The chain has to be left on every exit path, including a provider
    # failure — a ContextVar that is set and never reset leaks this agent
    # into whatever the caller does next.
    try:
        for _step in range(budget):
            # Before spending a model call. This is the check that matters
            # most: everything after it is the part the user is paying for.
            if cancellation.stopped(cancel):
                was_stopped = True
                break
            # Rounds are a poor proxy for cost: one carrying a long conversation
            # and four tool results is worth many carrying a sentence. When the
            # money runs out the turn still answers — it is just told to stop
            # looking, the same way a spent step budget is handled below.
            if ledger is not None and ledger.exhausted and _step > 0:
                log.debug("agent %s hit its token budget after %d rounds",
                          agent_id, _step)
                messages.append(Message(role="system", content=SPEND_PROMPT))
                reply = _answer_without_tools(provider, messages, _failed, emit)
                if isinstance(reply, TurnResult):
                    return reply
                break
            # low temperature → more reliable instruction-following & tool use
            try:
                result = _collect(provider.stream(messages, tools=tools,
                                                  temperature=0.15), emit, cancel)
            except Exception as exc:
                return _failed(exc)
            round_in = getattr(result, "input_tokens", 0) or 0
            round_out = getattr(result, "output_tokens", 0) or 0
            spent[0] += round_in
            spent[1] += round_out
            if ledger is not None:
                ledger.add(round_in + round_out)
            # Stopped while the answer was arriving — keep what was written.
            if cancellation.stopped(cancel):
                reply = result.text or reply
                was_stopped = True
                break
            if not result.wants_tools:
                reply = result.text
                break

            # The date we put in front of the question comes back inside the
            # arguments, because a model copies its own input. Take it out
            # here, once, before anything reads it: the same clean call then
            # reaches the memo, the trace the user watches, and the tool —
            # where `search_brain` would otherwise read a date in a query as a
            # filter and answer from an empty brain. See `grounding.py`.
            result.tool_calls = [
                replace(call, arguments=grounding.strip_arguments(call.arguments))
                for call in result.tool_calls
            ]

            messages.append(Message(
                role="assistant", content=result.text, tool_calls=result.tool_calls,
            ))
            for call in result.tool_calls:
                emit({"type": "tool_call", "name": call.name,
                      "arguments": call.arguments})
            outcomes = runner.run(result.tool_calls)
            for outcome in outcomes:
                call = outcome.call
                trace.append(TraceStep(
                    kind="tool_call", name=call.name, arguments=call.arguments))
                trace.append(TraceStep(kind="tool_result", name=call.name,
                                       result=outcome.output, repeated=outcome.repeated))
                emit({"type": "tool_result", "name": call.name,
                      "result": outcome.output[:2000],
                      "repeated": outcome.repeated})
                messages.append(Message(
                    role="tool", content=outcome.output, tool_call_id=call.id,
                    name=call.name,
                ))

            if cancellation.stopped(cancel):
                was_stopped = True
                break

            # Put the plan back in front of the model. Without re-stating it,
            # a long turn drifts: the plan scrolls out of attention and the
            # agent finishes part one thoroughly and forgets the rest.
            plan = planning.current()
            if plan is not None and plan.steps:
                messages.append(Message(role="system", content=plan.render()))
                emit({"type": "plan", "steps": plan.as_list()})

            # "That did not work" is worth saying once, explicitly. Left to
            # itself a model often re-issues the same broken call, and the
            # repeat memo then answers it from cache — so it never learns.
            if any(not o.ok for o in outcomes):
                messages.append(Message(role="system", content=planning.RETRY_NUDGE))

            # A round that learned nothing is the failure mode a deeper loop
            # introduces: with budget left and no new information, a model will
            # re-issue the same calls indefinitely.
            if runner.round_was_all_repeats(outcomes):
                stalls += 1
                if stalls >= STALL_LIMIT:
                    log.debug("agent %s stalled after %d rounds; asking it to answer",
                              agent_id, _step + 1)
                    if cancellation.stopped(cancel):
                        was_stopped = True
                        break
                    messages.append(Message(role="system", content=BUDGET_PROMPT))
                    reply = _answer_without_tools(provider, messages, _failed, emit)
                    if isinstance(reply, TurnResult):
                        return reply
                    break
            else:
                stalls = 0
        else:
            # Budget spent while still calling tools. An agent that has looked
            # things up for twenty rounds can usually answer — it has just never
            # been told to stop.
            if cancellation.stopped(cancel):
                # A stopped turn does not get one more call to wrap up. The
                # user asked for it to end, not to finish tidily.
                was_stopped = True
            else:
                messages.append(Message(role="system", content=BUDGET_PROMPT))
                reply = _answer_without_tools(provider, messages, _failed, emit)
                if isinstance(reply, TurnResult):
                    return reply

    finally:
        connector_grants.reset(grant_token)
        tool_bridge.stop_observing(watch_token)
        delegation.leave(chain_token)
        # Read the plan before releasing it — it is what the UI shows to explain
        # what the agent thought it was doing.
        plan_snapshot = (planning.current() or planning.Plan()).as_list()
        planning.finish(plan_token)

    if was_stopped:
        # Keep the half-written answer: the user paid for it. `STOPPED_NOTE`
        # goes on the end rather than replacing it, so the transcript reads as
        # what happened instead of as an error.
        reply = f"{reply.strip()}\n\n{STOPPED_NOTE}" if reply.strip() else STOPPED_NOTE
    reply = reply or "(the model returned nothing)"
    steps_used = len([s for s in trace if s.kind == "tool_call"])

    if persist:
        mem.append(agent.id, "assistant", reply,
                   tool_json=json.dumps(context.digest_of(trace)))

    if was_stopped:
        # Both of the calls below are model calls, and a stopped turn has no
        # business making two more of them. Nothing is lost that matters: the
        # user's message is already stored as searchable evidence, and the next
        # turn they actually finish will curate it.
        return TurnResult(
            agent_id=agent.id, reply=reply, trace=trace,
            provider=provider.name, model=provider.model,
            runtime_identity=identity, effort=profile.name,
            steps_used=steps_used, plan=plan_snapshot, stopped=True,
            tokens_in=spent[0], tokens_out=spent[1],
        )

    if not persist:
        # The "user" of an isolated turn is another agent, so nothing it said is
        # a fact the user disclosed. Learning from it would teach the brain the
        # app's own words.
        return TurnResult(
            agent_id=agent.id, reply=reply, trace=trace,
            provider=provider.name, model=provider.model,
            runtime_identity=identity, effort=profile.name,
            steps_used=steps_used, plan=plan_snapshot,
            tokens_in=spent[0], tokens_out=spent[1],
        )

    # Auto-learn: quietly capture durable facts the user revealed this turn, so
    # simply talking to an agent grows the brain — no manual "add fact" step.
    # (1) raw-memory capture (searchable evidence), (2) canonical curation so
    # the durable, versioned model of the user keeps up with the conversation.
    #
    # Both are model calls and neither changes the answer, so they no longer
    # happen between the last word arriving and the turn returning. The user
    # used to watch the text finish and then wait, with nothing on screen
    # saying why. See `background.py`.
    trace.append(TraceStep(kind="tool_result", name="auto_learn",
                           result="learning from this turn in the background"))
    background.after_turn(_learn_from_turn, user_text, reply, provider)
    return TurnResult(
        agent_id=agent.id, reply=reply, trace=trace,
        provider=provider.name, model=provider.model,
        runtime_identity=identity, effort=profile.name, steps_used=steps_used,
        plan=plan_snapshot, tokens_in=spent[0], tokens_out=spent[1],
    )
