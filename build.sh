#!/bin/zsh
# Bygger «PDF til Markdown.app» og en .dmg, og signerer og notariserer hvis det er satt opp.
#
# Miljøvariabler (alle valgfrie):
#   SIGN_IDENTITY   f.eks. "Developer ID Application: Navn Navnesen (TEAMID)". Uten denne blir appen ad hoc-signert.
#   NOTARY_PROFILE  navnet på en profil lagret med `xcrun notarytool store-credentials`. Krever SIGN_IDENTITY.
#   APP_VERSION     versjonsnummer, standard 1.0.0
#   BUNDLE_ID       standard io.github.foffster.pdf-til-markdown
#   BUILD_ROOT      hvor bygget skjer. Ligger utenfor prosjektmappa som standard, så sky-synkronisering
#                   (iCloud, Google Drive) slipper å synke flere hundre MB med midlertidige filer.
#   PYTHON          Python 3.10 eller nyere, standard /opt/homebrew/bin/python3.12
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="PDF til Markdown"
export APP_VERSION="${APP_VERSION:-1.0.0}"
export BUNDLE_ID="${BUNDLE_ID:-io.github.foffster.pdf-til-markdown}"
BUILD_ROOT="${BUILD_ROOT:-$HOME/Library/Caches/pdf-til-markdown-build}"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.12}"
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

VENV="$BUILD_ROOT/venv"
export TESSDATA_DIR="$BUILD_ROOT/tessdata"
DIST="$BUILD_ROOT/dist"
APP="$DIST/$APP_NAME.app"
DMG="$DIST/PDF-til-Markdown-$APP_VERSION.dmg"

step() { print -P "\n%B==> $1%b"; }

step "Python-miljø"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements-build.txt"

step "Språkdata for OCR (norsk + engelsk)"
mkdir -p "$TESSDATA_DIR"
for lang in nor eng; do
  [ -s "$TESSDATA_DIR/$lang.traineddata" ] || \
    curl -fsSL -o "$TESSDATA_DIR/$lang.traineddata" \
      "https://github.com/tesseract-ocr/tessdata_best/raw/main/$lang.traineddata"
done

step "PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean \
  --distpath "$DIST" --workpath "$BUILD_ROOT/work" \
  "$ROOT/packaging/$APP_NAME.spec"

if [ -n "$SIGN_IDENTITY" ]; then
  step "Signerer med «$SIGN_IDENTITY»"
  # Innerst først: alle Mach-O-filer, så selve appen med entitlements
  find "$APP/Contents" -type f | while read -r f; do
    if file -b "$f" | grep -q "Mach-O"; then
      codesign --force --timestamp --options runtime --sign "$SIGN_IDENTITY" "$f"
    fi
  done
  codesign --force --timestamp --options runtime \
    --entitlements "$ROOT/packaging/entitlements.plist" \
    --sign "$SIGN_IDENTITY" "$APP"
  codesign --verify --strict --deep --verbose=2 "$APP"
else
  step "Ingen SIGN_IDENTITY – appen er bare ad hoc-signert og kan kun brukes på denne Macen uten advarsel"
fi

step "Lager $DMG"
STAGE="$BUILD_ROOT/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Programmer"
hdiutil create -quiet -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ -n "$SIGN_IDENTITY" ]; then
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
  if [ -n "$NOTARY_PROFILE" ]; then
    step "Notariserer (tar vanligvis noen minutter)"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler staple "$APP"
    spctl --assess --type open --context context:primary-signature --verbose "$DMG"
  else
    step "Ingen NOTARY_PROFILE – hopper over notarisering"
  fi
fi

step "Ferdig"
echo "App: $APP"
echo "DMG: $DMG"
du -sh "$APP" "$DMG"
