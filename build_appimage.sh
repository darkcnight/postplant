# Spike Planted AppImage build script
# Creates a self-contained AppImage you can double-click to run.

set -e
cd "$(dirname "$0")"

APPDIR="dist/SpikePlanted.AppDir"
OUT="SpikePlanted.AppImage"

echo "==> Step 1: PyInstaller bundle"
python3 -m PyInstaller \
  --noconfirm \
  --onedir \
  --name "SpikePlanted" \
  --add-data "config.yaml:." \
  --add-data "ha_client.py:." \
  --add-data "detector.py:." \
  --add-data "effects.py:." \
  --hidden-import "PyQt6.QtCore" \
  --hidden-import "PyQt6.QtGui" \
  --hidden-import "PyQt6.QtWidgets" \
  --hidden-import "mss" \
  --hidden-import "cv2" \
  --hidden-import "yaml" \
  --hidden-import "httpx" \
  app.py

echo "==> Step 2: Set up AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r dist/SpikePlanted/* "$APPDIR/usr/bin/"

# AppRun launcher
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
# Keep config/.env next to the AppImage, not inside it
APPIMAGE_DIR="$(dirname "$(readlink -f "${APPIMAGE:-$0}")")"
export SPIKE_WORK_DIR="$APPIMAGE_DIR"
cd "$APPIMAGE_DIR"
exec "$HERE/usr/bin/SpikePlanted" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Desktop entry
cat > "$APPDIR/SpikePlanted.desktop" << 'DESKTOP'
[Desktop Entry]
Name=Spike Planted
Comment=Valorant spike events → Home Assistant lights
Exec=SpikePlanted
Icon=spike_planted
Type=Application
Categories=Game;Utility;
DESKTOP

# Simple icon (SVG red circle)
cat > "$APPDIR/spike_planted.svg" << 'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <circle cx="128" cy="128" r="120" fill="#0d0d1a" stroke="#ff4444" stroke-width="8"/>
  <circle cx="128" cy="128" r="70" fill="#8b0000" opacity="0.7"/>
  <text x="128" y="148" font-family="Arial" font-size="80" font-weight="bold"
        fill="white" text-anchor="middle">!</text>
</svg>
SVG

echo "==> Step 3: Build AppImage"
if [ -f /tmp/appimagetool ]; then
  ARCH=x86_64 /tmp/appimagetool --appimage-extract-and-run "$APPDIR" "$OUT" 2>&1
  echo ""
  echo "✅  AppImage built: $(pwd)/$OUT"
  echo "    Copy .env alongside it and double-click to launch."
else
  echo "⚠️  appimagetool not found — PyInstaller bundle is at dist/SpikePlanted/"
  echo "    Run the binary directly:  ./dist/SpikePlanted/SpikePlanted"
fi

