#!/usr/bin/env bash
# Rebuild the `character/` package and copy its bundle to where the app serves it.
#
# `character/` is a standalone, dependency-free package that is meant to be
# lifted into its own repository (`git subtree split --prefix=character`). The
# app must not import across that line, and the app's own static directory must
# stay inside the Python package or the built `.dmg` would not contain it.
#
# So the bundle is copied, and `tests/test_character_asset.py` fails if the copy
# and the source ever differ. A generated file plus a test that proves it is
# current is the only version of "the same code in two places" that does not rot
# — the alternative is discovering at a user's first launch that the avatar
# renderer is three months old.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v node >/dev/null 2>&1; then
  echo "sync-character: node is required to build the character package" >&2
  exit 1
fi

node character/scripts/build.js
# Bare `node --test`, run from the package: a quoted glob is only expanded
# by the test runner on Node 22+, and silently matches nothing on 18 and 20.
( cd character && node --test >/dev/null )

cp character/dist/character.global.js chitragupta/web/character.js

echo "chitragupta/web/character.js  <-  character/dist/character.global.js"
