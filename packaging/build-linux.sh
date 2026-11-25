#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "Build must run on x86_64 Linux." >&2
    exit 1
fi
glibc_version="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
if [[ "$glibc_version" != "2.35" ]]; then
    echo "Build must use glibc 2.35 (Linux Mint 21.3 / Ubuntu 22.04); found $glibc_version." >&2
    exit 1
fi

build_python="${HOME}/.pyenv/versions/3.12.14/bin/python"
if [[ ! -x "$build_python" ]]; then
    echo "Required Python interpreter was not found or is not executable: $build_python" >&2
    echo "Install Python 3.12.14 with Tk 8.6 and DejaVu Sans at that path before building." >&2
    exit 1
fi
if ! "$build_python" - <<'PY'
import sys

def fail(message):
    print(f"Linux build requires {message}.", file=sys.stderr)
    raise SystemExit(1)

if sys.version_info[:3] != (3, 12, 14):
    fail(f"Python 3.12.14; found {sys.version.split()[0]}")

try:
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    tk_font = root.tk.call("font", "actual", "TkDefaultFont", "-family")
    root.destroy()
except Exception as exc:
    fail(f"a working Tk 8.6 / DejaVu Sans installation ({exc})")

tk_version = tuple(int(part) for part in tkinter.TkVersion.__str__().split(".")[:2])
if tk_version != (8, 6):
    fail(f"Tk 8.6; found {tkinter.TkVersion}")
if str(tk_font).casefold() != "dejavu sans":
    fail(f"TkDefaultFont DejaVu Sans; found {tk_font!r}")

print(f"Build interpreter: Python {sys.version.split()[0]}")
print(f"Tk {tkinter.TkVersion}; TkDefaultFont: {tk_font}")
PY
then
    echo "The selected interpreter does not meet MiLaS's Linux build requirements." >&2
    exit 1
fi

build_dir="$ROOT_DIR/build/deploy"
dist_dir="$build_dir/MiLaS.dist"
release_dir="$ROOT_DIR/build/release/MiLaS-linux-x86_64"
archive="$ROOT_DIR/deliverables/MiLaS-linux-x86_64.tar.gz"
# Nuitka writes its standalone tree into build/deploy. Remove that directory
# and release staging before every build so stale payloads cannot be reused.
rm -rf "$build_dir" "$release_dir"
mkdir -p "$build_dir" "$ROOT_DIR/build/release"

build_env="$ROOT_DIR/.venv-xft"
build_env_python="$build_env/bin/python"
export UV_PROJECT_ENVIRONMENT="$build_env"
# Do not let a separately activated environment override this project's
# dedicated build environment.
unset VIRTUAL_ENV

uv sync --locked --python "$build_python"
if [[ ! -x "$build_env_python" ]]; then
    echo "uv did not create the expected project environment at $build_env" >&2
    exit 1
fi
uv pip install --python "$build_env_python" -r packaging/requirements-build-linux.txt
"$build_env_python" - "$build_python" <<'PY'
from pathlib import Path
import sys

import tkinter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

root = tkinter.Tk()
root.withdraw()
tk_font = root.tk.call("font", "actual", "TkDefaultFont", "-family")
root.destroy()
expected_base = Path(sys.argv[1]).resolve().parents[1]
if Path(sys.base_prefix).resolve() != expected_base:
    raise SystemExit(
        f".venv-xft was not created from the requested interpreter: "
        f"expected {expected_base}, found {Path(sys.base_prefix).resolve()}"
    )
tk_version = tuple(map(int, str(tkinter.TkVersion).split(".")[:2]))
if sys.version_info[:3] != (3, 12, 14) or tk_version != (8, 6) or str(tk_font).casefold() != "dejavu sans":
    raise SystemExit(".venv-xft must use Python 3.12.14, Tk 8.6, and TkDefaultFont DejaVu Sans")
print(f"Tkinter uses Tcl {tkinter.Tcl().eval('info patchlevel')} / Tk {tkinter.TkVersion}")
print(f"TkDefaultFont: {tk_font}")
print(f"Matplotlib TkAgg backend: {FigureCanvasTkAgg.__name__}")
PY
"$build_env_python" -m nuitka packaging/MiLaS.py \
    --mode=standalone \
    --enable-plugin=tk-inter \
    --include-package-data=milas \
    --report="$build_dir/nuitka-report.xml" \
    --output-dir="$build_dir" \
    --output-filename=MiLaS.bin \
    --quiet

test -x "$dist_dir/MiLaS.bin"
for required_matplotlib_file in \
    "$dist_dir/matplotlib/mpl-data/matplotlibrc" \
    "$dist_dir/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf"; do
    if [[ ! -s "$required_matplotlib_file" ]]; then
        echo "Nuitka did not stage required Matplotlib data: $required_matplotlib_file" >&2
        exit 1
    fi
done
shopt -s nullglob
numpy_core_extensions=("$dist_dir"/numpy/_core/_multiarray_umath.*)
numpy_linalg_extensions=("$dist_dir"/numpy/linalg/_umath_linalg.*)
if (( ${#numpy_core_extensions[@]} == 0 || ${#numpy_linalg_extensions[@]} == 0 )); then
    echo "Nuitka did not stage required NumPy native extensions without the obsolete plugin." >&2
    exit 1
fi
"$build_env_python" - "$dist_dir" "$release_dir" "$ROOT_DIR" <<'PY'
import shutil
import sys
from pathlib import Path

dist, release, root = map(Path, sys.argv[1:])
if release.exists():
    shutil.rmtree(release)
release.mkdir(parents=True)
shutil.copytree(dist, release, dirs_exist_ok=True)
for name in (
    "LICENSE", "NOTICE",
    "LGPL-2.1-Section-6-Additional-Permission.md",
):
    shutil.copy2(root / name, release / name)
PY
# libquadmath is redistributed inside the NumPy runtime tree. Ship the exact
# AlmaLinux GCC source RPM identified from the archived ELF build ID, and
# verify it before staging any license/inventory claims.
quadmath_source_name="gcc-8.5.0-28.el8_10.alma.1.src.rpm"
quadmath_source_dir="$release_dir/SOURCE"
quadmath_source="$quadmath_source_dir/$quadmath_source_name"
quadmath_source_url="https://vault.almalinux.org/8/BaseOS/Source/Packages/$quadmath_source_name"
quadmath_source_sha256="c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0"
if ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
    echo "Linux build requires curl and sha256sum to stage the matching libquadmath source." >&2
    exit 1
fi
mkdir -p "$quadmath_source_dir"
curl --fail --location --retry 3 --connect-timeout 15 --max-time 300 \
    "$quadmath_source_url" --output "$quadmath_source"
if ! printf '%s  %s\n' "$quadmath_source_sha256" "$quadmath_source" | sha256sum --check --status; then
    echo "Downloaded GCC source RPM failed its pinned SHA-256 check: $quadmath_source_name" >&2
    exit 1
fi
"$build_env_python" packaging/stage_release_licenses.py "$dist_dir" "$release_dir" \
    --python-source "local pyenv CPython 3.12.14 (machine-specific interpreter path omitted); OpenSSL resolved from its _ssl shared-library linkage" \
    --nuitka-report "$build_dir/nuitka-report.xml"
unresolved_count="$("$build_env_python" - "$release_dir/BUNDLED_RUNTIME_INVENTORY.md" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"^Unresolved native payload count: (\d+)\.", text, re.M)
print(match.group(1) if match else "1")
PY
)"
if [[ "$unresolved_count" != "0" ]]; then
    diagnostic="$build_dir/native-provenance-diagnostics.json"
    "$build_env_python" packaging/diagnose_native_provenance.py \
        "$dist_dir" "$release_dir" \
        --nuitka-report "$build_dir/nuitka-report.xml" \
        --output "$diagnostic"
    echo "Native payload provenance remains unresolved; diagnostic evidence was written to $diagnostic" >&2
fi
for required in \
    "$release_dir/BUNDLED_RUNTIME_INVENTORY.md" \
    "$release_dir/LICENSES/Tcl-Tk-runtime-files.txt" \
    "$release_dir/LICENSES/Tcl-license.terms" \
    "$release_dir/LICENSES/Tk-license.terms" \
    "$release_dir/LICENSES/OpenSSL-Apache-2.0.txt" \
    "$release_dir/LICENSES/GNU-LGPL-2.1.txt" \
    "$release_dir/LICENSES/GPL-3.0.txt" \
    "$release_dir/LICENSES/GCC-Runtime-Library-Exception-3.1.txt" \
    "$release_dir/LGPL-2.1-Section-6-Additional-Permission.md" \
    "$release_dir/SOURCE/$quadmath_source_name" \
    "$release_dir/SOURCE/LIBQUADMATH-SOURCE.md" \
    "$release_dir/SOURCE/LIBGFORTRAN-SOURCE.md"; do
    if [[ ! -s "$required" ]]; then
        echo "Required release notice or inventory was not staged: $required" >&2
        exit 1
    fi
done
mkdir -p deliverables
tar -C "$ROOT_DIR/build/release" -czf "$archive" MiLaS-linux-x86_64
# Inspect archive members directly. A tar | grep -q pipeline can make tar exit
# with SIGPIPE under pipefail after grep finds an early match.
"$build_env_python" packaging/verify_release_archive.py "$archive" MiLaS-linux-x86_64
echo "Created $archive"
