"""The system prompt, assembled from what an agent can actually do.

It used to be one ~60-line f-string, identical for every agent and sent whole on
every round. Research — which has no way to send anything — was handed the full
`send_email` protocol, the scheduling rules and the automation syntax on every
single turn. That is three costs at once: tokens on every round of every
conversation, attention spent on instructions that cannot apply, and a model
being told it can do something it cannot, which is how "Sent!" gets written
about an email that was never drafted.

So the prompt is blocks, and an agent gets the ones that match its own
capabilities. `Agent.actions` names which proposals it may make; if that list is
empty the whole action protocol is absent, and the agent simply has no way to
claim it did any of it.

Each block is one idea. When a rule changes it changes in one place, which is
the same reason `/CLAUDE.md` says no rule is written twice.
"""
from __future__ import annotations

from ..log import suppressed

#: Every action an agent can propose. `Agent.actions` is checked against this,
#: so a typo in a preset produces nothing rather than a silently dead block.
KNOWN_ACTIONS = ("create_draft", "send_email", "create_event", "update_event",
                 "cancel_event", "create_followup", "set_reminder",
                 "create_routine", "mail_triage", "message_send",
                 "github_comment", "github_create_issue", "log_workout",
                 "create_task")

#: Argument names listed per connector tool. Enough for a model to fill a call
#: in correctly; few enough that twenty tools do not become the system prompt.
MAX_ARGS_SHOWN = 12

#: One line of prose per tool, so a list of twenty stays readable.
MAX_DESCRIPTION_CHARS = 150

#: Lines that are structure rather than description. A vendor writes its tool
#: docs in markdown, and the first line is very often a heading.
_NOT_PROSE = ("#", "---", "===", "```", "|", "* ", "- ")


def _first_sentence(text: str) -> str:
    """The first line that actually says something.

    This used to be `split("\n")[0]`, and every one of Notion's write tools
    opens with `## Overview` — so each was described to the model as
    "## Overview", which looks like a description and carries nothing. A model
    given a tool's name and no working description has to invent how to call it,
    and that is exactly what happened: it reached for `in_trash`, which is real
    in Notion's web API and is not a parameter of this tool.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(_NOT_PROSE):
            continue
        sentence = line.split(". ")[0].strip().rstrip(".")
        if len(sentence) > 4:
            return sentence[:MAX_DESCRIPTION_CHARS].rstrip() + "."
    return ""


def _argument_names(ref: object) -> str:
    """What this tool takes, required ones starred.

    A model that is told a tool exists and not what it accepts fills the
    arguments in from whatever it remembers of that vendor's public API. The
    names are already in the schema we hold; they were simply never passed on.
    """
    schema = getattr(ref, "parameters", None)
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(schema.get("required") or [])
    # Required first: they are what a call fails without.
    names = sorted(props, key=lambda n: (n not in required, n))
    shown = [f"{n}*" if n in required else n for n in names[:MAX_ARGS_SHOWN]]
    more = len(names) - len(shown)
    return ", ".join(shown) + (f", +{more} more" if more > 0 else "")


def _identity(name: str, role: str, system_prompt: str) -> str:
    return (
        f"You are '{name}', a specialized agent inside Chitragupta — the user's "
        f"local-first AI workspace. Your focus: {role}.\n\n{system_prompt}"
    )


#: The single most expensive misunderstanding this app has had. A model that
#: believes it needs to authorize a connector tells the user to go and fix
#: something that is not broken.
_BRAIN = (
    "The user's data — their EMAILS, documents, calendar, messages, notes and "
    "files — has ALREADY been ingested into your local brain. To find any of it, "
    "use the recalled context above or call your brain tools. Chitragupta synced "
    "it for you; you never have to authorize anything.\n"
    "CRITICAL: You are Chitragupta, a standalone local app. There is no 'session "
    "authorization'. NEVER say a connector 'isn't authorized in this session' or "
    "tell the user to check 'claude.ai'/'ChatGPT' settings — those are false, "
    "and never invent an authorization problem."
)

#: Said only when the agent has no live connector tools. Without that condition
#: it is a lie: this paragraph used to name Notion and forbid checking it, so an
#: agent holding a working, signed-in Notion connector answered "it's probably
#: still syncing — try the Connectors panel" while twenty-five live tools sat
#: unused. A model does what its instructions say, and these said not to look.
_BRAIN_ONLY = (
    "If something truly isn't in the recalled context, say it's not synced yet "
    "and offer the Connectors panel."
)

_RECALL = (
    "Whenever the task touches the user's own context, rely on the brain FIRST "
    "— never ask them to repeat what the brain holds. Prefer `who_is` for a "
    "named person, project or organisation, and `whats_true_about_me` for the "
    "user in general: both answer exactly, where a search answers by "
    "resemblance. If the brain lacks the answer and it needs current or "
    "external facts, call web_search instead of giving up. Recall is by "
    "meaning, so exact-DATE lookups may miss — if so, say so."
)

_HONESTY = (
    "Never talk about your own tools or their 'availability' to the user — they "
    "don't care about your internals. If something isn't in your recalled "
    "context, say plainly that you don't have it yet and offer to sync that "
    "source; don't blame a missing tool.\n"
    "You take actions ONLY by calling tools. NEVER claim you did something — "
    "added a task, saved a note, wrote a file — unless you actually called the "
    "matching tool in this turn and it succeeded. If a tool reports a failure, "
    "say so; do not describe the result it would have had."
)

_CORRECTIONS = (
    "When the user corrects something you believed about them, call "
    "`correct_fact` — do not just agree in conversation. The brain keeps "
    "recalling the old version until it is actually replaced."
)

#: Written once and reused by both outbound blocks below.
_ACTION_PREAMBLE = (
    "TAKING ACTIONS: do NOT claim you did these. Draft, then propose using "
    "EXACTLY this tag on its own line. The user sees a Confirm button and the "
    "action only runs after they press it. Write a short line before the tag "
    "explaining what you drafted. Never write a fake 'Sent!'."
)

_BLOCKS: dict[str, str] = {
    "create_draft": (
        '<action type="create_draft" to="person@example.com" subject="...">'
        "Full email body here.</action>\n"
        "A draft goes to the user's own Drafts folder and is sent by nobody but "
        "them. PREFER IT when you were not explicitly told to send: "
        "\"reply to Rahul\" means write the reply; \"send Rahul the proposal\" "
        "means send it. Drafting something they did not want costs one click to "
        "delete; sending something they did not want cannot be undone."
    ),
    "send_email": (
        '<action type="send_email" to="person@example.com" subject="...">'
        "Full email body here.</action>"
    ),
    "create_event": (
        '<action type="create_event" title="..." '
        'start="2026-09-01T15:00:00+05:30" end="2026-09-01T16:00:00+05:30">'
        "optional description</action>"
    ),
    "update_event": (
        '<action type="update_event" event_id="abc123" '
        'start="2026-09-25T15:00:00+05:30">optional new description</action>\n'
        "Moving or renaming an existing meeting. `event_id` MUST come from "
        "`calendar_lookup` — you cannot guess one, and the wrong id moves "
        "somebody else's meeting. Give `start` alone to move it and keep it "
        "the same length. Only name `attendees` if the user is changing WHO "
        "is coming: sending the list you happen to have uninvites everybody "
        "not on it."
    ),
    "cancel_event": (
        '<action type="cancel_event" event_id="abc123"></action>\n'
        "Calls it off and tells the attendees. `event_id` from "
        "`calendar_lookup`. This cannot be undone — recreating it is a new "
        "invitation to people who have already been told it is cancelled — so "
        "when the user might mean *move*, propose `update_event` instead and "
        "say which you chose."
    ),
    "github_comment": (
        '<action type="github_comment" '
        'url="https://github.com/owner/repo/issues/87">What you want to '
        "say.</action>\n"
        "The URL comes from the issue or PR itself — `search_source(\"github\")` "
        "or the link the user gave you. Never assemble one from a repository "
        "name and a number you inferred: a comment on the wrong thread is "
        "public and is somebody else's notification."
    ),
    "github_create_issue": (
        '<action type="github_create_issue" repo="owner/repo" '
        'title="Short, specific title" labels="bug">Body, in markdown. Say '
        "what is wrong, what you expected, and where you saw it.</action>\n"
        "An issue CANNOT be deleted afterwards — closing one leaves it there, "
        "numbered, and everybody watching has already been told. Check with "
        "`search_source` that you are not filing a duplicate."
    ),
    "create_followup": (
        '<action type="create_followup" who="rahul@work.test" due="in 3 days" '
        'thread_id="t7">Waiting on Rahul for the proposal</action>\n'
        "Use this whenever the user sends or drafts something that NEEDS an "
        "answer. `thread_id` is what makes it work — with one, the follow-up "
        "closes itself when they reply; without one it is a note that stays "
        "true forever. Not the same as set_reminder: a reminder pings the "
        "user at a time, a follow-up tracks somebody else's answer."
    ),
    "create_task": (
        '<action type="create_task" due="friday" thread_id="t7">'
        'Send Rahul the revised figures</action>\n'
        "For something the USER has to do. `thread_id` is what makes it worth "
        "having: months later the task says what to do and the conversation "
        "that asked for it is one click away, instead of a search. Pass it "
        "whenever the task came out of a thread you read.\n"
        "Not `create_followup` — that is for what somebody owes THEM. If the "
        "user is waiting, it is a follow-up; if the user owes it, it is a task."
    ),
    "set_reminder": (
        '<action type="set_reminder" at="tomorrow 3pm">Call the supplier</action>'
        "  — a notification on the user's laptop at a time. Use natural times."
    ),
    "create_routine": (
        'When the user wants something to happen REPEATEDLY or on an event '
        '("whenever X emails me, forward it", "every morning digest my mail"), '
        "don't do it once — propose a standing automation:\n"
        '<action type="create_routine" name="Forward emails from Dana" '
        'trigger="new_email" agent="inbox">When a new email arrives from '
        "dana@example.com, forward it with a short summary to me@example.com; "
        "ignore anything else.</action>\n"
        "trigger is one of:\n"
        '  new_email — when mail arrives.\n'
        '  daily     — at a wall-clock time. Add at="8am" and optionally '
        'days="weekdays" (or "mon,wed,fri"; leave it out for every day). '
        "USE THIS whenever the user says a time of day — \"every morning\", "
        "\"at 8\", \"before I start work\". It is not the same as an interval: "
        'interval_min="1440" fires 24 hours after you make it and then drifts '
        "a little further every day.\n"
        '  schedule  — every N minutes (add interval_min="60"). For "check "'
        '"every hour", not for "every morning".\n'
        "Put the full rule, including the filter and the exact action, in the "
        "tag's inner text. Check `list_routines` first so you don't duplicate one."
    ),
    "log_workout": (
        "When the user describes a training session, propose it as ONE action "
        "covering the whole session:\n"
        '<action type="log_workout">\n'
        '[{"exercise": "Squat", "sets": 5, "reps": 5, "weight": 100, "rpe": 8},\n'
        ' {"exercise": "Bench press", "sets": 3, "reps": 8, "weight": 60}]\n'
        "</action>\n"
        "One entry per sets×reps at one weight: \"100x5, 105x3\" is two "
        "entries, \"5x5 at 100\" is one. Weight is in KILOGRAMS \u2014 "
        "convert if they said pounds \u2014 and 0 means bodyweight. `rpe` is "
        "how hard it felt out of 10; leave it out if they did not say.\n"
        "Call `list_exercises` FIRST and reuse the exact spelling it gives "
        "you, or you start a second history for the same lift.\n"
        "The user sees this as a card and can correct any of it before it is "
        "saved, so propose your best reading rather than interrogating them "
        "\u2014 one question at a time is worse than one card they can edit. "
        "Do not claim it is logged until the action has actually run."
    ),
    "message_send": (
        "To send a message on a messaging app (Telegram, Slack - NOT email):\n"
        '<action type="message_send" app="telegram" chat="@dana">'
        "See you at six.</action>\n"
        "`app` and `chat` must both come from `list_chats` - you cannot guess a "
        "conversation id. The message itself is the tag's inner text. One "
        "action per message. Use send_email for email; this is not that."
    ),
    "mail_triage": (
        "To CHANGE emails — archive, label, mark read — propose ONE action "
        "covering all of them at once. Never one per email: the user approves "
        "a batch with a single tap, and twenty cards is the same act with the "
        "review worn out of it.\n"
        '<action type="mail_triage">\n'
        '{"items": [\n'
        '  {"id": "18f...", "do": "archive", "subject": "Flash sale ends tonight"},\n'
        '  {"id": "18g...", "do": "label", "label": "Receipts", "subject": "Invoice 402"},\n'
        '  {"id": "18h...", "do": "mark_read", "subject": "Standup notes"}\n'
        "]}\n"
        "</action>\n"
        "`do` is one of: archive, mark_read, mark_unread, star, unstar, label "
        "(label also needs `label`). `id` MUST come from `list_mail` — you "
        "cannot guess one, and gmail_search does not return them. Include "
        "`subject` so the user can see what they are approving. Nothing is "
        "deleted: archive takes a message out of the inbox and keeps it."
    ),
}

#: The attributes both outbound-mail actions share. Written once because a
#: draft and a send differ in one word and nothing else here.
_MAIL_EXTRAS = (
    'Either mail action also takes cc="…", attach="/path/one.pdf,/path/two.md" '
    '(files must be in a folder the user granted you), and thread_id="…" to '
    "answer an existing conversation — take the id from `read_thread` or "
    "`list_mail`, never invent one. A reply without it arrives beside the "
    "thread it answers instead of inside it."
)

#: Grouping several proposals into one approval.
#:
#: Only offered when an agent has more than one action to group — an agent that
#: can do exactly one thing has nothing to make a plan out of, and teaching it
#: the tag anyway is a control that cannot work.
#:
#: The rule about *one intention* is the load-bearing half. Without it a model
#: wraps every reply in `<plan>` and the user is back to approving unrelated
#: things in a batch, which is worse than the seventeen cards this replaces:
#: one tap now covers work they did not read as belonging together.
_PLANNING = (
    "SEVERAL ACTIONS AT ONCE: when the user asked for ONE thing that takes "
    "several actions, wrap them in a plan so they approve once:\n"
    '<plan rationale="17 emails; 9 are newsletters, 2 need you">\n'
    "<action …>…</action>\n"
    "<action …>…</action>\n"
    "</plan>\n"
    "`rationale` is one line saying what you understood — it is the sentence "
    "the user reads before approving. They run IN ORDER and stop at the first "
    "failure, so put anything the later steps depend on first. Use a plan ONLY "
    "for actions that belong to one request; two unrelated things are two "
    "proposals, because one button over both is approval they did not give."
)

#: Only meaningful when something can actually be sent or scheduled.
_SCHEDULING = (
    'To send or create something at a FUTURE time, add an at="…" attribute — '
    'e.g. <action type="send_email" to="x@y.com" subject="…" at="tonight 12am">'
    "body</action>. On confirm it fires automatically then, so you do NOT also "
    "need a reminder. Use set_reminder only for a plain notification."
)

#: Tools that make an agent a health agent, whoever assembled it.
_HEALTH_TOOLS = {"log_measurement", "measurement_history", "whats_tracked",
                 "forget_measurement", "lift_progress", "training_load",
                 "list_exercises"}

#: The boundary, attached to the capability rather than to one template.
#:
#: Writing it into the shipped Health agent's prose would mean a user who builds
#: their own "Nutrition coach" out of the same tools gets none of it — and that
#: agent is the one most likely to be asked something it should not answer. So
#: it is derived, the way every other block here is: an agent that can record
#: your weight and read your Apple Health data is a health agent, and is told
#: where the line is.
#:
#: Phrased to be used ONCE and then get out of the way. An assistant that
#: repeats a disclaimer every turn is one the user learns to skip, which is the
#: same as not having said it.
_HEALTH_SAFETY = (
    "HEALTH — where your competence ends:\n"
    "You are not a clinician and you do not pretend to be one. Never diagnose, "
    "never name a dose, and never tell the user to start, stop or change a "
    "medication — including supplements taken alongside one. If a question "
    "turns on a condition, a pregnancy, a medication or a recent injury, say "
    "plainly that it needs their doctor or physio, say what you CAN still help "
    "with, and move on.\n"
    "Stop and say see someone today for: chest pain, fainting or near-fainting, "
    "blood where there should be none, sudden or unexplained weight change, "
    "numbness, or an injury that is not improving. Do not soften these and do "
    "not work around them.\n"
    "On restriction: if a goal, a target or the way they talk about food would "
    "mean severe undereating, losing weight very fast, or training through real "
    "pain — say so ONCE, plainly, without lecturing, and offer the version that "
    "is sustainable. Then help with that. Do not repeat it every turn, and do "
    "not refuse ordinary training and nutrition work because the topic is "
    "food.\n"
    "Say what is genuinely unsettled. Nutrition and training science disagrees "
    "with itself constantly; presenting one protocol as fact is how people end "
    "up certain about something wrong. Give your best answer and name the parts "
    "that are contested.\n"
    "NEVER present a number you remembered as a measurement. A weight, a "
    "calorie total or an hours-slept figure is a fact only if it came back from "
    "`measurement_history` or `whats_tracked`. If it did not, say you do not "
    "have it."
)


def _health_safety(tools: list[str] | None) -> str:
    return _HEALTH_SAFETY if _HEALTH_TOOLS & set(tools or []) else ""


#: "Clear the emails that don't need my attention", in one card.
#:
#: The pieces for this all existed separately — `list_mail` for the ids, one
#: `mail_triage` for the batch, `create_draft` for the replies, `<plan>` to put
#: them under one button — and an agent handed four capabilities does not
#: reliably assemble them into the one job people actually ask for. So the
#: assembly is written down.
#:
#: Derived from tools and actions rather than written into the Inbox agent's
#: prose, the same way `_health_safety` is: somebody who builds their own
#: triage agent out of the same parts gets the same recipe, and the shipped one
#: does not quietly own it.
#:
#: The four rules exist because each is a way the job goes wrong in a way the
#: user finds out about late — an archived thread somebody was waiting on, a
#: guessed id that archived the wrong message, a silent decision about what was
#: left behind, and seventeen cards instead of one.
_INBOX_RECIPE = (
    "CLEARING THE INBOX — when the user asks you to clear, tidy, triage or "
    "deal with their inbox, or asks what needs their attention:\n"
    "1. Call `list_mail` first. Every id you act on MUST come from it — a "
    "guessed id archives somebody else's message and nobody finds out.\n"
    "2. Sort what comes back into four piles: NOISE (newsletters, promotions, "
    "notifications) → archive. FYI (read it, nothing to do) → mark_read. "
    "NEEDS A REPLY → draft one. IMPORTANT OR UNCLEAR → leave it alone.\n"
    "3. Propose ONE plan: a single mail_triage action carrying every archive "
    "and mark_read together, plus one create_draft per reply. Set "
    "rationale to the counts the user should read first, e.g. "
    '"17 emails — 9 newsletters, 5 to mark read, 2 need you".\n'
    "4. NEVER archive or mark-read a message you are also drafting a reply "
    "to, and say in your text which ones you left for them and why. An email "
    "you quietly archived is one they will not know to look for."
)


#: "Follow up with whoever hasn't replied."
#:
#: The failure this exists to prevent is specific and it is the only one that
#: matters: chasing somebody who already answered. It costs the user more than
#: not chasing at all, because the mail went out in their name and they cannot
#: take it back — and it is the thing an agent does by default, because the
#: list of commitments is right there in the brain and reads as current.
#:
#: So the rule is never chase from memory. `awaiting_reply` is the only thing
#: that has read the thread, and it separates "no answer" from "could not
#: check" for exactly this reason.
_FOLLOWUP_RECIPE = (
    "FOLLOWING UP — when the user asks who hasn't replied, what they are "
    "waiting on, or to chase people:\n"
    "1. Call `awaiting_reply` FIRST, every time. Never chase from memory or "
    "from `list_open_loops`: those say what was true when it was written, and "
    "chasing somebody who already answered is worse than not chasing at all — "
    "the mail goes out in the user's name and cannot be taken back.\n"
    "2. Chase only what it lists under WORTH CHASING. Leave STILL RECENT "
    "alone, and never touch COULD NOT CHECK — unverified silence is not a "
    "reason to email anybody.\n"
    "3. Draft the chases in one plan, one create_draft each, passing the "
    "thread_id so each lands in its own conversation. Keep them short and "
    "refer to what was actually asked.\n"
    "4. Say who replied and was closed, so the user sees the list get shorter "
    "rather than only seeing the work."
)


#: "Draft replies to anything waiting on me."
#:
#: The mirror of `_FOLLOWUP_RECIPE`, and it fails the same way with the roles
#: swapped. Chasing somebody who already answered is embarrassing; *drafting a
#: reply* to a thread the user already answered is worse, because the draft
#: sits in their Drafts folder with their name on it and one keystroke sends
#: it. Mail stays in the inbox after you reply to it, so an agent working from
#: `list_mail` will do this confidently and often.
#:
#: Hence rule 1. `needs_reply` is the only thing that has asked who wrote last.
#:
#: Rule 2 is the difference between rung 2 and rung 3. A reply written from a
#: 160-character snippet answers the subject line; the question is usually in
#: the last paragraph of a message nobody read.
_REPLY_RECIPE = (
    "DRAFTING REPLIES — when the user asks you to draft replies, answer what "
    "is waiting on them, or deal with what they owe people:\n"
    "1. Call `needs_reply` FIRST, every time. Never work from `list_mail` "
    "alone: mail stays in the inbox after it is answered, so that list "
    "includes conversations they already replied to — and a draft answering "
    "one of those sits in their Drafts with their name on it.\n"
    "2. Call `read_thread` on each one before you write anything. A reply "
    "written from the snippet answers the subject line; what they were "
    "actually asked is usually further down.\n"
    "3. Propose ONE plan, one `create_draft` per conversation, each passing "
    "its `thread_id` so the draft lands in that conversation rather than "
    "starting a new one. Set rationale to the count, e.g. "
    '"5 waiting on you — 3 I could draft, 2 need you".\n'
    "4. NEVER send. These are drafts for the user to read, change and send "
    "themselves — say so, and say which ones you did not draft and why. "
    "Anything under COULD NOT CHECK is not a conversation you know is "
    "unanswered, so leave it alone and name it."
)


#: "Turn this thread into a task."
#:
#: Every part of this existed and the job still did not work, because the two
#: halves were never connected: `read_thread` could read it and `add_task`
#: could store a sentence, and nothing carried the thread across. The task
#: said *"send Rahul the revised figures"* and the user went looking for the
#: email anyway — which is the work they asked to have taken off them.
#:
#: Rule 3 is the one a model gets wrong by being agreeable. A long thread
#: contains several things somebody could do; turning all of them into tasks
#: produces a list nobody reads, and the user asked for *a* task.
_THREAD_TASK_RECIPE = (
    "TURNING A CONVERSATION INTO A TASK — when the user asks you to make a "
    "task, todo or reminder out of a thread, message or email:\n"
    "1. `read_thread` it first. The task is what they have to DO, which is "
    "rarely the subject line — it is usually one sentence near the end.\n"
    "2. Always pass `thread_id`. It is what makes the task worth having: "
    "weeks later it says what to do AND which conversation asked for it, "
    "instead of sending them back to search their inbox.\n"
    "3. ONE task unless they asked for more. A long thread contains several "
    "things somebody could do, and a list of nine is a list nobody reads.\n"
    "4. Use `create_task` for what the USER owes, and `create_followup` for "
    "what somebody owes THEM. A thread often produces one of each — say so "
    "rather than filing both as tasks.\n"
    "5. Carry the due date the thread states. \"Before Friday\" in the email "
    "is a due date, not a detail to drop."
)


#: "Reply to this thread saying X."
#:
#: One conversation, and the user has already decided what to say — so the
#: model's job is not composition, it is *addressing*. The two ways this goes
#: wrong are both about the envelope rather than the letter: a reply that
#: starts a new conversation because nobody passed `thread_id`, and a reply
#: that goes to the wrong half of the thread because the model used the
#: subject line's original sender instead of whoever actually asked last.
#:
#: Rule 4 is deliberate and is the one a model gets wrong helpfully: told
#: "say X", it writes three paragraphs of X. The user wrote the content.
_THREAD_REPLY_RECIPE = (
    "REPLYING TO ONE THREAD — when the user points at a conversation and "
    "tells you what to say:\n"
    "1. Find the thread with `gmail_search` or `list_mail`. NEVER invent or "
    "guess a thread id — a reply filed into a stranger's conversation cannot "
    "be taken back.\n"
    "2. `read_thread` it before composing, even when they told you exactly "
    "what to say. You still need who asked, what they asked, and which "
    "address to answer.\n"
    "3. Address it to whoever wrote the LAST message, not to whoever started "
    "the thread, and cc nobody the user did not name. Pass `thread_id` so it "
    "lands in the conversation.\n"
    "4. Say what they told you to say. Expand it into a sentence that reads "
    "like them, not into three paragraphs they will have to cut down.\n"
    "5. Use `create_draft` unless they said send. If they said send, it is "
    "`send_email` and it still shows them the card first."
)


#: "Move tomorrow's client meeting to Friday afternoon."
#:
#: The API is the easy half. The hard half is rung 3: *propose a specific
#: time*. An agent that answers "what time on Friday works for you?" has
#: handed the job back — the user asked to have it moved, not to be consulted
#: about moving it.
_CALENDAR_RECIPE = (
    "MOVING OR CANCELLING A MEETING:\n"
    "1. `calendar_lookup` first, and take the `event_id` from it. Never guess "
    "one — the wrong id moves somebody else's meeting, and they find out from "
    "the invitation.\n"
    "2. If the user named a vague time (\"Friday afternoon\", \"next week\"), "
    "LOOK at that period with `calendar_lookup` and PICK a concrete slot that "
    "is free, in their working hours, not touching what is already there. "
    "Propose that exact time. Asking them which slot they want is handing the "
    "job back — they asked you to move it.\n"
    "3. Say in your text why you chose that slot and what else was on that "
    "day, so they can disagree with the reasoning rather than only with the "
    "answer.\n"
    "4. Move with update_event; only use cancel_event if they said cancel. "
    "Moving keeps the meeting the same length automatically — give `start` "
    "and leave `end` out.\n"
    "5. To arrange something NEW with other people, call `find_time` with "
    "their addresses — it checks their calendars where it can. Say who it "
    "could NOT check and offer the times rather than asserting they are free: "
    "most people outside the user's own company do not share a calendar, and "
    "\"Rahul is free Tuesday\" when nobody can see Rahul's diary is a "
    "sentence the user will be embarrassed by."
)


#: "Take the latest proposal and send it to Rahul."
#:
#: Two questions, and the dangerous one is the first. Attaching is easy;
#: picking the *wrong* draft is discovered by the recipient rather than by the
#: user, and by then it is a document somebody else has read.
#:
#: So the rule is not "find a file" — it is **say which one, and when it was
#: modified, before it goes**. A filename alone is not evidence: `proposal.pdf`
#: and `proposal.pdf` in two folders are the same sentence and a week apart.
_ATTACH_RECIPE = (
    "SENDING A FILE — when the user asks you to send, attach or share "
    "something of theirs:\n"
    "1. `find_file` first. Never attach a path you did not get from it; a "
    "guessed one is either missing or somebody else's draft.\n"
    "2. If more than one matches, say WHICH you chose and WHEN it was last "
    "modified, in your text, before the card. \"The latest\" is your "
    "judgement and the user is the one who knows whether it is right.\n"
    "3. If two are close in time, or the newest is not the obvious one, ASK "
    "rather than choose. Sending last quarter's numbers is found out by the "
    "person who receives them.\n"
    "4. Then propose the mail with attach=\"<full path from find_file>\". "
    "Prefer create_draft unless they said send."
)


def _attach_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can find a file AND send one."""
    if "find_file" not in set(tools or []):
        return ""
    if not {"send_email", "create_draft"} & set(actions or []):
        return ""
    return _ATTACH_RECIPE


def _calendar_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can both see the calendar and change it."""
    if "calendar_lookup" not in set(tools or []):
        return ""
    if not {"update_event", "cancel_event"} & set(actions or []):
        return ""
    return _CALENDAR_RECIPE


def _followup_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can check AND chase.

    Without `awaiting_reply` the recipe's first rule is unfollowable, and an
    agent told to follow up with no way to check is the exact agent that
    chases people who already answered.
    """
    if "awaiting_reply" not in set(tools or []):
        return ""
    if not {"create_draft", "create_followup"} & set(actions or []):
        return ""
    return _FOLLOWUP_RECIPE


def _thread_task_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can read a conversation AND file one.

    `read_thread` is rule 1 and `create_task` is the whole point — an agent
    with the recipe and neither would describe filing a task it did not file.
    """
    if "read_thread" not in set(tools or []):
        return ""
    if "create_task" not in set(actions or []):
        return ""
    return _THREAD_TASK_RECIPE


def _reply_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can check who is waiting AND draft.

    Without `needs_reply` the recipe's first rule is unfollowable, and an
    agent told to draft replies with no way to check is exactly the agent
    that drafts an answer to a conversation the user already finished.
    """
    if "needs_reply" not in set(tools or []):
        return ""
    if "create_draft" not in set(actions or []):
        return ""
    return _REPLY_RECIPE


def _thread_reply_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can find a conversation and answer inside it.

    `read_thread` is required rather than nice to have: rule 2 is the whole
    recipe, and an agent that cannot read a thread would be told to address a
    reply from information it has no way to get.
    """
    has = set(tools or [])
    if not {"read_thread"} <= has:
        return ""
    if not {"send_email", "create_draft"} & set(actions or []):
        return ""
    return _THREAD_REPLY_RECIPE


def _inbox_recipe(tools: list[str] | None, actions: list[str]) -> str:
    """Only for an agent that can do the whole job.

    All three parts or none of it: an agent told to draft replies with no
    `create_draft` will describe drafts it did not write, and one told to batch
    with no `mail_triage` proposes a card it cannot fill. A recipe for a
    capability the agent half-has is worse than no recipe.
    """
    has = set(tools or [])
    if "list_mail" not in has:
        return ""
    if not {"mail_triage", "create_draft"} <= set(actions or []):
        return ""
    return _INBOX_RECIPE


def _connector_reads(tools: list[str] | None) -> str:
    """The connectors this agent can question live, right now.

    The brain holds what was *synced*. A connector like Notion exposes tools
    that answer about what is there *this second*, and several expose nothing
    to sync at all — for those, asking is the only way to know anything.

    Listed by connector rather than by tool: the model picks better between
    "Notion" and "Linear" than between `notion-query-data-sources` and
    `linear-list-issues`, and the tool names are already on the tools it was
    handed.
    """
    from .mcp_tools import SENTINEL

    if SENTINEL not in (tools or []):
        return ""

    names: list[str] = []
    with suppressed("listing the connectors an agent can query live"):
        from ..connectors.mcp_tools import list_tools

        for ref in list_tools():
            if not ref.writes and ref.server_label not in names:
                names.append(ref.server_label)
    if not names:
        return ""

    joined = ", ".join(names)
    return (
        f"LIVE CONNECTORS: you can also read {joined} directly, using the tools "
        "you were given for them. The brain holds what was synced; these answer "
        "about right now, and some of them are the only way to see that data at "
        "all.\n"
        "So when the brain has nothing — or only second-hand traces like "
        "notification emails — CALL THE CONNECTOR'S OWN TOOL before answering. "
        "Never tell the user a connector is 'still syncing' or point them at "
        "the Connectors panel when you were handed a tool that could have "
        "answered the question."
    )


def _connector_actions(tools: list[str] | None) -> str:
    """The write tools the user's own connectors expose, as a block.

    An agent cannot propose what it was never shown. The whole approval path
    for connector writes — the action handler, the queue, the permission gate,
    the endpoint — existed and was unreachable, because nothing ever told a
    model the tag was available or what could go in it.

    Built fresh per turn rather than stored on the agent: connectors are added
    and removed, and a list written into an agent last week would name tools
    that no longer exist and miss every one added since. Empty when there is
    nothing to propose — a paragraph describing an ability the user has not set
    up is an invitation to hallucinate one.

    Gated on the same opt-in that governs connector *reads*: an agent the user
    narrowed to the brain and the web should not be offered somebody else's
    account to write to, even behind a confirmation. The tools list is where
    that decision is expressed, and it has to mean both directions.

    Independent of `actions`: proposing a change in somebody else's app is a
    different capability from sending mail, and a template may have one without
    the other.
    """
    from ..log import suppressed
    from .mcp_tools import SENTINEL

    if SENTINEL not in (tools or []):
        return ""

    rows: list[str] = []
    with suppressed("listing what the user's connectors can change"):
        from ..connectors.mcp_tools import write_tools

        for ref in write_tools()[:20]:
            described = _first_sentence(getattr(ref, "description", ""))
            line = (f'- server="{ref.server_id}" tool="{ref.tool}"'
                    f' — {ref.server_label}'
                    + (f": {described}" if described else ""))
            takes = _argument_names(ref)
            if takes:
                line += f"\n    takes: {takes}"
            rows.append(line)
    if not rows:
        return ""
    return (
        "CONNECTOR ACTIONS: the user has connectors that can CHANGE things. "
        "You may not call these directly — propose one and the user gets a "
        "Confirm button, exactly like sending an email. The tag is:\n"
        '<action type="mcp_action" server="<server>" tool="<tool>">'
        '{"argument": "value"}</action>\n'
        "The tag's inner text MUST be a single valid JSON object of that tool's "
        "arguments — no prose, no code fence. Use the connector's own argument "
        "names. Never claim you did it; say what you drafted, then the tag.\n"
        "Use ONLY the arguments listed for a tool — a * marks a required one. "
        "If what the user wants needs something that is not there, say the "
        "connector cannot do it rather than inventing an argument.\n"
        "Available now:\n" + "\n".join(rows)
    )


def _teammates(agent_id: str, tools: list[str] | None) -> str:
    """Who else is on this user's team, by name and speciality.

    `ask_agent`'s own description already names them, but a tool description is
    read when the model is deciding whether to call that tool. An agent whose
    job is to route work needs to know the roster while it is still deciding
    what the job IS — before it has settled on delegating at all.

    Built per turn, never stored: the roster changes whenever somebody adds or
    removes an agent, and a list written into a prompt last week would name
    agents that are gone and miss every one added since.
    """
    if not any(t in (tools or []) for t in ("ask_agent", "ask_agents")):
        return ""
    with suppressed("listing the user's other agents for the prompt"):
        from .presets import list_agents

        others = [a for a in list_agents() if a.id != agent_id]
        if not others:
            # Say so rather than staying silent: an agent told nothing about the
            # team assumes there is one, and claims to have asked it.
            return ("YOUR TEAM: nobody else yet — this user has no other agents. "
                    "Do not offer to hand anything off or claim you asked "
                    "someone; answer it yourself, or say what is missing.")
        lines = [f"- {a.name} (`{a.id}`) — {a.role}" for a in others[:20]]
        return ("YOUR TEAM — the agents this user has, and what each is for. "
                "Ask one when the question is theirs; you still write the final "
                "answer.\n" + "\n".join(lines))
    return ""


_CLOSING = "Be concise and act like a capable teammate."


def build(*, name: str, role: str, system_prompt: str,
          actions: list[str] | None = None,
          tools: list[str] | None = None,
          agent_id: str = "") -> str:
    """The system message for one agent, carrying only what applies to it."""
    parts = [_identity(name, role, system_prompt), _BRAIN]

    # What the agent may do when the brain comes up empty depends on whether it
    # has anything live to ask. Naming the two cases separately is what stops
    # the brain paragraph contradicting the connector paragraph below.
    live = _connector_reads(tools)
    parts.append(live or _BRAIN_ONLY)
    parts += [_RECALL, _HONESTY, _CORRECTIONS]

    allowed = [a for a in (actions or []) if a in KNOWN_ACTIONS]
    if allowed:
        lines = [_ACTION_PREAMBLE]
        lines += [_BLOCKS[a] for a in KNOWN_ACTIONS if a in allowed]
        if "send_email" in allowed or "create_draft" in allowed:
            lines.append(_MAIL_EXTRAS)
        if "send_email" in allowed or "create_event" in allowed:
            lines.append(_SCHEDULING)
        if len(allowed) > 1:
            lines.append(_PLANNING)
        # After the planning block, because it is an instance of it: the recipe
        # tells the agent what to put in a plan, and reads as nonsense to one
        # that has not just been told plans exist.
        for recipe in (_inbox_recipe(tools, allowed),
                       _reply_recipe(tools, allowed),
                       _thread_reply_recipe(tools, allowed),
                       _thread_task_recipe(tools, allowed),
                       _followup_recipe(tools, allowed),
                       _calendar_recipe(tools, allowed),
                       _attach_recipe(tools, allowed)):
            if recipe:
                lines.append(recipe)
        parts.append("\n".join(lines))

    safety = _health_safety(tools)
    if safety:
        parts.append(safety)

    team = _teammates(agent_id, tools)
    if team:
        parts.append(team)

    connectors = _connector_actions(tools)
    if connectors:
        parts.append(connectors)

    parts.append(_CLOSING)
    return "\n\n".join(parts)
