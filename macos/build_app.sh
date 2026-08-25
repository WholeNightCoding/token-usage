#!/usr/bin/env bash
# Build "Token Usage.app" — a native Swift menu-bar app for the token-usage dashboard.
#
# Menu-bar (NSStatusItem) app: on launch starts the dashboard server if needed and
# opens the browser; the menu offers 打开面板 / 停止服务 / 退出 (quit also kills
# the server). Zero runtime deps — compiled with swiftc into a native binary.
#
# Reproducible: regenerates the whole .app every run. Rerun after the server or
# icon changes, or on a fresh machine. python path & skill path are resolved at
# build time so no PATH / host assumption is baked in silently.
set -euo pipefail

APP_NAME="Token Usage"
EXE_NAME="TokenUsage"          # binary name inside the bundle (no space)
BUNDLE_ID="com.wholenightcoding.tokenusage"
PORT="8787"
URL="http://127.0.0.1:$PORT/"

# --- resolve paths ---------------------------------------------------------
MACOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$MACOS_DIR/.." && pwd)"
SERVER_PY="$SKILL_DIR/dashboard/server.py"
LOG_FILE="$SKILL_DIR/dashboard/server.log"
SWIFT_SRC="$MACOS_DIR/TokenUsageMenu.swift"

command -v swiftc >/dev/null || { echo "ERROR: swiftc not found (install Xcode Command Line Tools)" >&2; exit 1; }
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "ERROR: python3 not found on PATH" >&2; exit 1; }
PYBIN="$("$PY" -c 'import sys;print(sys.executable)')"
[ -f "$SERVER_PY" ] || { echo "ERROR: server.py not found at $SERVER_PY" >&2; exit 1; }
[ -f "$SWIFT_SRC" ] || { echo "ERROR: swift source not found at $SWIFT_SRC" >&2; exit 1; }

echo "swiftc     : $(swiftc --version 2>/dev/null | head -1)"
echo "python3    : $PYBIN"
echo "server.py  : $SERVER_PY"
echo "skill dir  : $SKILL_DIR"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
APP_PATH="$BUILD_DIR/$APP_NAME.app"
CONTENTS="$APP_PATH/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# --- 1. inject paths into a copy of the Swift source -----------------------
SRC_TMP="$BUILD_DIR/main.swift"
cp "$SWIFT_SRC" "$SRC_TMP"
sed -i '' \
	-e "s|@@PYBIN@@|$PYBIN|g" \
	-e "s|@@SERVER@@|$SERVER_PY|g" \
	-e "s|@@LOG@@|$LOG_FILE|g" \
	-e "s|@@URL@@|$URL|g" \
	-e "s|@@PORT@@|$PORT|g" \
	"$SRC_TMP"

# --- 2. compile ------------------------------------------------------------
swiftc -O -o "$CONTENTS/MacOS/$EXE_NAME" "$SRC_TMP"

# --- 3. icon (reused Pillow generator) -------------------------------------
ICONSET="$BUILD_DIR/TokenUsage.iconset"
"$PYBIN" "$MACOS_DIR/make_icon.py" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/applet.icns"

# --- 4. Info.plist ---------------------------------------------------------
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key><string>$APP_NAME</string>
	<key>CFBundleDisplayName</key><string>$APP_NAME</string>
	<key>CFBundleExecutable</key><string>$EXE_NAME</string>
	<key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
	<key>CFBundleIconFile</key><string>applet</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleShortVersionString</key><string>1.1</string>
	<key>CFBundleVersion</key><string>1.1</string>
	<key>LSMinimumSystemVersion</key><string>11.0</string>
	<key>LSUIElement</key><true/>
	<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# --- 5. ad-hoc sign (arm64 requires a valid signature to run) --------------
codesign --force --deep -s - "$APP_PATH" >/dev/null 2>&1 || \
	echo "WARN: codesign failed; app may still run if the linker ad-hoc signed the binary" >&2

# --- 6. install to /Applications ------------------------------------------
DEST="/Applications/$APP_NAME.app"
rm -rf "$DEST"
cp -R "$APP_PATH" "$DEST"
touch "$DEST"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST" >/dev/null 2>&1 || true

echo "installed  : $DEST"
echo "done."
