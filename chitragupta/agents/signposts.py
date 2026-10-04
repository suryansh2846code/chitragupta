"""Where to send a user, written once.

Several things tell somebody where to go and turn a permission on: the refusal
an agent gets when it reaches a connector it has not been allowed
(`loop.NEEDS_PERMISSION`), the paragraph naming what it has not been given
(`prompt._WITHHELD`), the sentence about running code (`access`), and the
chat's own cards. All four named the same screen, in four strings.

**That is a rule written four times, and it drifted the moment the screen
moved.** Permissions became a tab in each agent's own profile and the Settings
page went; every one of those sentences carried on naming the page, so an agent
refused a connector would tell the user to open something that is not there —
which is the exact failure `NEEDS_PERMISSION` was written to fix in the first
place. It had said only "ask them for it", an agent invented a Settings path,
and the fix was to name the real one. Naming it four times meant the fix only
held until somebody moved the screen.

It is a leaf on purpose: `loop`, `prompt` and `access` all read it, and none of
them may import each other.
"""
from __future__ import annotations

#: How a person opens one agent's permissions, in the words they would use.
#:
#: The `⋯` rather than a menu path: it is the control they press, it sits
#: beside the agent's name in the rail and in the chat header, and there is no
#: longer a settings page to walk them to.
PERMISSIONS = "the ⋯ beside this agent's name, then Permissions"

#: The same place, said from outside a given agent's own conversation — the
#: chat's cards talk about several agents at once, so "this agent" would be
#: wrong there.
PERMISSIONS_ANY_AGENT = "the ⋯ beside an agent's name, then Permissions"
