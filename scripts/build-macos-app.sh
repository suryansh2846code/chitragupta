#!/bin/bash
# Build a double-clickable "Chitragupta.app" for THIS machine, for development.
#
# The launcher it writes runs this checkout's virtualenv, so the bundle contains
# no Python and works nowhere else. That is the point — it is a fast way to get
# an icon in ~/Applications while developing.
#
# To build something you can give to somebody else, use scripts/build-dmg.sh:
# it bundles the interpreter with PyInstaller, signs with Developer ID, and
# notarises. See docs/DISTRIBUTION.md.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Read the version rather than restating it — this file said 0.2.0 while
# pyproject.toml said 0.1.0.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$PROJECT_DIR/pyproject.toml" | head -1)"
VENV="$PROJECT_DIR/.venv"
APP_DIR="${1:-$HOME/Applications}/Chitragupta.app"
CONTENTS="$APP_DIR/Contents"

if [ ! -x "$VENV/bin/chitragupta" ]; then
  echo "❌ venv not found at $VENV — run: uv venv && uv pip install -e '.[all,desktop]'"
  exit 1
fi

echo "Building $APP_DIR …"
rm -rf "$APP_DIR"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# launcher
cat > "$CONTENTS/MacOS/Chitragupta" <<EOF
#!/bin/bash
cd "$PROJECT_DIR"
exec "$VENV/bin/chitragupta" app
EOF
chmod +x "$CONTENTS/MacOS/Chitragupta"

# Info.plist
cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <!-- Named apart from the shipped build on purpose. Both can be installed at
       once, and two rows reading "Chitragupta" in the Applications browser is
       how you end up debugging the wrong one. -->
  <key>CFBundleName</key><string>Chitragupta (dev)</string>
  <key>CFBundleDisplayName</key><string>Chitragupta (dev)</string>
  <!-- NOT ai.chitragupta.app. This bundle and the shipped one are different
       apps — this launcher runs the checkout's venv — and when both claimed the
       same identifier macOS treated them as one: the app vanished from the
       macOS 26 Applications browser entirely, because the view dedupes by
       identifier and resolved to the copy it would not list. They would also
       have shared "Open With" defaults, TCC permission grants and window
       state, so a dev build could silently answer for the installed one. -->
  <key>CFBundleIdentifier</key><string>ai.chitragupta.app.dev</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>Chitragupta</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- macOS 26 replaced Launchpad with a Spotlight "Applications" browser that
       groups by category. Without this key the app has no bucket and is not
       listed at all — installed, signed and indexed, but nowhere a user can
       find it. Nothing warns you, so it is easy to lose an afternoon to. -->
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict>
</plist>
EOF

# The icon lives with the rest of the packaging assets. This used to look in
# scripts/, where there has never been one, so the dev bundle silently shipped
# the generic application icon — the same cwd-relative mistake build-dmg.sh
# documents having made, and just as invisible, because a missing icon looks
# like a choice.
if [ -f "$PROJECT_DIR/packaging/icon.icns" ]; then
  cp "$PROJECT_DIR/packaging/icon.icns" "$CONTENTS/Resources/icon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string icon" "$CONTENTS/Info.plist" 2>/dev/null || true
fi

echo "✓ Built $APP_DIR"
echo "  Open it from $HOME/Applications (or double-click). First launch: right-click → Open."
