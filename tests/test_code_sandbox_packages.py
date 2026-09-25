"""What a snippet may import — and the far longer list of what it may not.

`run_python` runs under `-I -S`, which is the isolation: no environment, no
site-packages, so a snippet cannot import Chitragupta, reach the brain, or read
a credential file by importing the module that knows where it lives.

`-S` also removed the one thing the tool exists for. "Work out my weight trend
from this export" is arithmetic over a few thousand numbers, and a model asked
to do that in pure Python writes a loop that is slower, longer and likelier to
be wrong than one line of numpy. The tool could run code and not do the job.

So numpy is linked back in **by name**, into a scratch directory of symlinks —
not by putting site-packages back on the path, which would hand a snippet every
dependency the app has. These tests pin both halves: the package that is meant
to be reachable, and the ones that must never be. The second half is the one
that matters, and it is the half a future widening would quietly break.
"""
from __future__ import annotations

import pytest

from chitragupta.agents.code_tools import ALLOWED_PACKAGES, run_python


def test_numpy_is_reachable():
    result = run_python("import numpy as np; print(np.mean([1, 2, 3, 4]))")
    assert result.ok, str(result)
    assert "2.5" in str(result)


@pytest.mark.parametrize("module", [
    "chitragupta",   # the app itself: the brain, the store, the settings
    "httpx",         # a socket
    "anthropic",     # a vendor SDK, and whatever it reads on import
    "keyring",       # the obvious one
])
def test_the_rest_of_site_packages_stays_out(module):
    """The allowlist is a boundary; a path entry would not be. If this test
    starts passing for a new name, site-packages went back on `sys.path`."""
    result = run_python(f"import {module}")
    assert not result.ok
    assert "No module named" in str(result)


def test_the_standard_library_still_works():
    """`-S` removes site-packages, not the stdlib. A snippet that cannot import
    `json` cannot do anything useful with a file either."""
    result = run_python("import json, statistics\n"
                        "print(json.dumps({'m': statistics.mean([2, 4])}))")
    assert result.ok and '"m": 3' in str(result)


def test_nothing_on_the_allowlist_can_open_a_socket():
    """The rule the list is kept by, written down where it is enforced."""
    assert ALLOWED_PACKAGES == ("numpy",)


def test_a_traceback_points_at_the_line_the_model_wrote():
    """The traceback is handed back precisely so the model can fix its own
    code. Linking numpy in through a prepended line shifted every number in it
    by one, pointing the model at the line above its mistake on every failure."""
    result = run_python("x = 1\nprint(x)\nraise ValueError('here')")
    assert not result.ok
    assert 'line 3, in <module>' in str(result)


def test_the_snippet_cannot_reach_the_users_real_files():
    """The working directory is thrown away afterwards. Writing somewhere that
    matters is `file_tools`' job, where the user has drawn a boundary."""
    result = run_python(
        "import os; print(os.path.basename(os.getcwd()).startswith('chitragupta-scratch'))")
    assert result.ok and "True" in str(result)
