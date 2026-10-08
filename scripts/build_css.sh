#!/usr/bin/env bash
# Build static/css/app.css from assets/css/input.css with the standalone Tailwind CLI.
#
# The output is committed (Render's build doesn't run this); CI runs this script and
# fails if static/css/app.css changes, i.e. if someone changed classes without rebuilding.
# Works in Git Bash on Windows, on Linux (CI) and on Apple-silicon macOS.
#
#   bash scripts/build_css.sh
set -euo pipefail

TAILWIND_VERSION="4.3.3"  # keep in step with the version the UI was designed against
# Official SHA-256 values from the release's sha256sums.txt:
# https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/sha256sums.txt
SHA256_LINUX_X64="dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a"
SHA256_WINDOWS_X64="e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955"
SHA256_MACOS_ARM64="cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d"

cd "$(dirname "$0")/.."  # run from the repository root, wherever it was called from

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset="tailwindcss-linux-x64"; expected="$SHA256_LINUX_X64" ;;
  MINGW*-x86_64 | MSYS*-x86_64 | CYGWIN*-x86_64)
    asset="tailwindcss-windows-x64.exe"; expected="$SHA256_WINDOWS_X64" ;;
  Darwin-arm64) asset="tailwindcss-macos-arm64"; expected="$SHA256_MACOS_ARM64" ;;
  *) echo "build_css.sh: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

sha256() {
  if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# tools/ is gitignored; the version is in the file name so an upgrade downloads afresh.
binary="tools/tailwindcss-${TAILWIND_VERSION}-${asset}"
if [ ! -f "$binary" ]; then
  mkdir -p tools
  url="https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/${asset}"
  echo "Downloading ${url}"
  curl -fsSL --retry 3 -o "${binary}.part" "$url"
  mv "${binary}.part" "$binary"
fi

# Checked on every run, not only after downloading: a corrupted or replaced binary
# must never build the stylesheet.
actual="$(sha256 "$binary")"
if [ "$actual" != "$expected" ]; then
  rm -f "$binary"
  echo "build_css.sh: SHA-256 mismatch for ${asset}" >&2
  echo "  expected ${expected}" >&2
  echo "  got      ${actual}" >&2
  echo "The download was removed; check the pinned version and checksums." >&2
  exit 1
fi
chmod +x "$binary"

"$binary" -i assets/css/input.css -o static/css/app.css --minify

# Same bytes on every platform: LF line endings only (the Windows build could write CRLF).
tr -d '\r' < static/css/app.css > static/css/app.css.tmp
mv static/css/app.css.tmp static/css/app.css

echo "Built static/css/app.css ($(wc -c < static/css/app.css | tr -d ' ') bytes)"
