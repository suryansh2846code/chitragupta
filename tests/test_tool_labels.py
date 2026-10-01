"""Every tool a person can switch on has a name a person can read.

`_LABELS` exists because a list of thirty-odd rows reading `whats_true_about_me`
and `browse_press` is our variable names on the user's screen — the comment
above it says exactly that. It is a hand-kept table beside the tool registry,
and two hand-kept lists that must agree are the shape this codebase keeps
getting bitten by.

It had already drifted twice when these were written. Six tools had no entry at
all — `edit_file`, `what_i_looked_at`, and the four browser verbs added in the
commit before this one — so they rendered under "Other" as their own function
names. And `TOOL_CATEGORIES`, the *order* the panel reads in, was missing four
of the categories `_LABELS` actually uses, so those sections rendered after the
"Other" bucket.

Neither failure breaks anything. Both are only visible as a screen that reads
badly, which is precisely why they survived.
"""
from __future__ import annotations

import pytest

from chitragupta.agents import tools


@pytest.mark.parametrize("name", sorted(tools.TOOL_DEFS))
def test_every_tool_has_a_name_a_person_can_read(name):
    """The fallback renders the id, deliberately ugly so it gets noticed. This
    is what notices it."""
    assert name in tools._LABELS, (
        f"{name} has no label, so the permissions panel shows a user our "
        "function name. Add it to _LABELS in agents/tools.py.")


@pytest.mark.parametrize("name", sorted(tools.TOOL_DEFS))
def test_a_label_is_one_word_not_a_sentence(name):
    """The category already says the subject: under Memory, "Search" needs no
    further qualification and "Search your brain" only repeats the heading."""
    label, _ = tools.tool_label(name)

    assert label, name
    assert len(label) <= 16, f"{name}: {label!r} is a sentence, not a label"


def test_every_category_used_is_also_ordered():
    """`TOOL_CATEGORIES` is what the panel sorts by. A category missing from it
    sorts after "Other" — below the bucket for things nobody has named yet."""
    used = {category for _, category in tools._LABELS.values()}

    assert not used - set(tools.TOOL_CATEGORIES), (
        f"{sorted(used - set(tools.TOOL_CATEGORIES))} are used as categories "
        "and missing from TOOL_CATEGORIES, so they render after “Other”.")


def test_every_ordered_category_is_actually_used():
    """The other direction. A heading nobody is in is an empty section, or a
    rename somebody only did half of."""
    used = {category for _, category in tools._LABELS.values()}

    assert not set(tools.TOOL_CATEGORIES) - used, (
        f"{sorted(set(tools.TOOL_CATEGORIES) - used)} are ordered and have no "
        "tools in them.")


def test_nothing_falls_into_other():
    """"Other" is the fallback, and a fallback that anything actually lands in
    has stopped being one."""
    stray = [n for n in tools.TOOL_DEFS if tools.tool_label(n)[1] == "Other"]

    assert not stray, f"{stray} are uncategorised"
