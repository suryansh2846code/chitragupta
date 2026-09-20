# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for a self-contained Chitragupta.app.

`scripts/build-macos-app.sh` writes a *shim* bundle whose launcher runs the
project's own virtualenv. That is a convenience for the machine it was built on
and cannot be given to anybody — there is no Python inside it. This spec builds
the real thing: interpreter, dependencies and web assets in one bundle.

Two decisions carry most of the size and most of the risk:

* **Torch is excluded.** `sentence-transformers` is an optional extra; the
  default embedder is the pure-Python `hash` one, which is why the app can run
  with no downloads at all. Bundling torch would add well over a gigabyte to
  every download for a feature most users never switch on. A user who wants
  local embeddings installs the extra into a source checkout.
* **The optional connector SDKs are declared as hidden imports.** They are
  imported inside functions so the app starts without them; PyInstaller's
  static analysis therefore cannot see them, and a bundle built without this
  list would ship an app whose Gmail connector fails at the moment it is used.
"""
import pathlib
import re

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BLOCK_CIPHER = None

# This spec's own directory. PyInstaller injects SPECPATH; every path below is
# anchored on it rather than on the working directory, because `build-dmg.sh`
# invokes PyInstaller from inside `packaging/` — and a cwd-relative path here is
# exactly how the icon silently stopped shipping for the whole life of the spec.
HERE = pathlib.Path(SPECPATH)  # noqa: F821 — injected by PyInstaller
ICON = HERE / "icon.icns"

# One source of truth for the version. It was hardcoded here as 0.2.0 while
# pyproject.toml said 0.1.0 and the shim bundle's Info.plist said 0.2.0 — three
# numbers for one build, which is how a user reports a version that never
# existed.
VERSION = re.search(
    r'^version = "([^"]+)"',
    (HERE.parent / "pyproject.toml").read_text(), re.M).group(1)

# Imported lazily inside functions, so the analyser cannot find them.
HIDDEN = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "webview", "webview.platforms.cocoa",
    "googleapiclient", "google_auth_oauthlib", "google.auth",
    "notion_client", "pypdf", "docx", "pptx", "ddgs",
    # Telethon is imported inside functions so a build without it still runs;
    # that is also why the analyser cannot see it and it has to be named here.
    "telethon",
    # Playwright, and it is NOT decoration. It reaches the bundle today only
    # because `browser/driver.py` spells the import statically inside a
    # function, which PyInstaller's bytecode analysis happens to follow.
    # Rewrite that one line as `importlib.import_module(...)` and 130 MB of
    # driver silently stops shipping — the build still succeeds, the app still
    # starts, `can_drive()` still answers (it only asks `find_spec`), and the
    # browser dies the moment a user actually opens a page, after they have
    # downloaded 150 MB of Chromium. Named here so that cannot happen quietly.
    "playwright", "playwright.sync_api",
    "anyio._backends._asyncio",
]
HIDDEN += collect_submodules("chitragupta")

# Everything the server serves. `chitragupta/web` is read from disk at runtime by
# `api/assets.py::WEB`, so it has to travel with the bundle.
DATAS = [
    (str(HERE.parent / "chitragupta" / "web"), "chitragupta/web"),
    (str(HERE.parent / "chitragupta" / "data"), "chitragupta/data"),
]
DATAS += collect_data_files("ddgs", include_py_files=False)

# Large optional dependencies, and anything that only exists to build wheels.
EXCLUDED = [
    "torch", "sentence_transformers", "transformers", "scipy", "sklearn",
    "matplotlib", "pandas", "IPython", "notebook", "pytest", "mypy", "ruff",
    "tkinter", "PIL", "PyQt5", "PySide6",
]

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(HERE.parent)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED,
    cipher=BLOCK_CIPHER,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=BLOCK_CIPHER)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Chitragupta",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX-packed binaries fail notarisation
    console=False,             # a GUI app: no terminal window
    target_arch=None,          # whatever this machine is; see docs/DISTRIBUTION.md
    codesign_identity=None,    # signed as one bundle later, not per-binary
    entitlements_file=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="Chitragupta",
)

app = BUNDLE(
    coll,
    name="Chitragupta.app",
    # `build-dmg.sh` runs PyInstaller from inside `packaging/`, so this test is
    # relative to THIS directory — it read `packaging/icon.icns` and therefore
    # looked for `packaging/packaging/icon.icns`, which never exists. The value
    # was right and only the test was wrong, so the bundle silently shipped
    # PyInstaller's generic `icon-windowed.icns` and adding the artwork changed
    # nothing. `build-dmg.sh` now asserts the built plist to keep it honest.
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="ai.chitragupta.app",
    version=VERSION,
    info_plist={
        "CFBundleName": "Chitragupta",
        "CFBundleDisplayName": "Chitragupta",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # macOS 26 replaced Launchpad with a Spotlight "Applications" browser
        # that groups by category, and an app with no category has no bucket to
        # land in — it is simply not listed. The bundle was installed, signed,
        # registered and Spotlight-indexed, and still did not appear anywhere a
        # user would look for it. Nothing warns you; the key just has to be here.
        "LSApplicationCategoryType": "public.app-category.productivity",
        # Chitragupta is a window, not a menu-bar accessory. (An accessory policy
        # is what would let the sign-in card float over another app's
        # full-screen Space — see docs/DESKTOP-SIGNIN.md for why that trade was
        # refused.)
        "LSUIElement": False,
        # Shown in the macOS permission prompts. These strings are the entire
        # explanation the user gets, so they say what Chitragupta does with the
        # data and that it stays on the machine.
        "NSAppleEventsUsageDescription":
            "Chitragupta reads your Notes and Calendar to build your local brain. "
            "Nothing leaves your Mac.",
        "NSCalendarsUsageDescription":
            "Chitragupta reads your calendar so your agents know your schedule. "
            "It stays on this Mac.",
        "NSContactsUsageDescription":
            "Chitragupta reads your contacts so it can recognise the people you "
            "work with. It stays on this Mac.",
        "NSDesktopFolderUsageDescription":
            "Chitragupta indexes folders you choose so your agents can search them.",
        "NSDocumentsFolderUsageDescription":
            "Chitragupta indexes folders you choose so your agents can search them.",
        "NSDownloadsFolderUsageDescription":
            "Chitragupta indexes folders you choose so your agents can search them.",
    },
)
