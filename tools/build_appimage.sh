#!/usr/bin/env bash
# Build dist/Postplant-x86_64.AppImage (a single file: double-click to run).
# Needs: python3 with venv, network access (pip, and python-appimage's base image).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 -m venv "$work/venv"
"$work/venv/bin/pip" install -q python-appimage

recipe="$work/recipe"
cp -r "$root/packaging/appimage" "$recipe"
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/pyproject.toml")"
# Install from a wheel (the project path may contain spaces, which pip can't take here).
"$work/venv/bin/pip" wheel -q --no-deps -w "$work/wheels" "$root"
ls "$work"/wheels/postplant-*.whl > "$recipe/requirements.txt"

mkdir -p "$root/dist"
cd "$work"
"$work/venv/bin/python-appimage" build app -p "${PYTHON_VERSION:-3.12}" -n Postplant "$recipe"
mv "$work"/Postplant*.AppImage "$root/dist/Postplant-$version-x86_64.AppImage"
echo "Built dist/Postplant-$version-x86_64.AppImage"
