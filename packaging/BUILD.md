# Standalone Release Builds

MiLaS is a Tkinter desktop application. Matplotlib embeds its plots using the
TkAgg canvas. Python's `tkinter` module is part of the standard library, while
Tcl/Tk must also be present in the Python build environment and included in
standalone output. The native build scripts use Nuitka's `tk-inter` support to
collect Tcl/Tk libraries and data. NumPy support is supplied by Nuitka's
standard package configuration; the former `numpy` plugin is deprecated and
does no work. Matplotlib data is likewise included by its Nuitka package
configuration, so an explicit all-data inclusion is redundant. Nuitka's
documented Tk plugin can discover Tcl/Tk library directories;
the staging check records the actual runtime files and fails if it cannot find
the Tcl/Tk runtime.

Build tools are pinned separately from application runtime dependencies in
`requirements-build-*.txt`. The application runtime is synchronized from
`uv.lock`.

## Linux x86_64

Build locally on Linux Mint 21.3 or Ubuntu 22.04 x86_64 (glibc 2.35), the
supported oldest Linux baseline. Nuitka does not bundle glibc, so building on a
newer system can make the binary incompatible with Mint 21.3. The script checks
the architecture and glibc before building.

The Linux build uses the tested pyenv interpreter at
`$HOME/.pyenv/versions/3.12.14/bin/python`. Before running the build, verify that
this interpreter is installed and reports Python 3.12.14, Tk 8.6, and
`TkDefaultFont` as DejaVu Sans. The build script checks these requirements and
stops with an explanatory error if they are not met. It creates or reuses the
project-local `.venv-xft` environment with that interpreter; `uv` does not
install or select a different Python for this Linux build. The script also
clears any activated `VIRTUAL_ENV` and checks that `.venv-xft` has the requested
pyenv installation as its base interpreter. Runtime sync, packaging
dependencies, TkAgg validation, Nuitka, and license staging all run through
`.venv-xft/bin/python`.

On your Linux Mint 21.3 machine, from the extracted project directory, run:

```bash
cd ~/MiLaS
test -x "$HOME/.pyenv/versions/3.12.14/bin/python"
bash packaging/build-linux.sh
```

The script checks the interpreter, Linux architecture, and glibc; synchronizes
the locked application dependencies into `.venv-xft`; installs the Linux build
tools into that same environment; verifies Tkinter, the Tk 8.6 default font,
and Matplotlib's TkAgg canvas; enables Nuitka's `tk-inter` plugin and packages
MiLaS data. Nuitka's built-in package configuration supplies NumPy and
Matplotlib runtime components. It starts with clean compiler and release
directories; checks for NumPy's `_multiarray_umath` and `_umath_linalg` native
extensions and Matplotlib's rc/font data; and verifies Tcl/Tk payloads before
staging notices. After tar creation, a Python archive-member validator checks
the exact expected paths directly. This avoids a `tar | grep -q` false failure
under shell `pipefail`.

The build is manual and platform-specific. Run it on Linux Mint yourself, then
inspect and test the resulting archive locally; this source ZIP does not
contain a prebuilt Linux release. The script writes
`deliverables/MiLaS-linux-x86_64.tar.gz`. It contains a
`MiLaS-linux-x86_64/` folder with `MiLaS.bin`, the bundled runtime, assets,
`README.md`, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, the
`LGPL-2.1-Section-6-Additional-Permission.md`, and generated
`LICENSES/Tcl-Tk-runtime-files.txt`, both upstream Tcl/Tk license texts,
`LICENSES/OpenSSL-Apache-2.0.txt`, `LICENSES/GNU-LGPL-2.1.txt`, the matching
GCC source RPM under `SOURCE/`, the locked Python distribution licenses, and
`BUNDLED_RUNTIME_INVENTORY.md`. The build downloads the pinned AlmaLinux
`gcc-8.5.0-28.el8_10.alma.1.src.rpm` from the URL recorded in
`THIRD_PARTY_NOTICES.md`, checks its SHA-256 before staging, and stops if the
source or license text does not match the expected provenance. The stager
matches bundled libquadmath and libgfortran independently by pinned ELF build
IDs. It writes `SOURCE/LIBQUADMATH-SOURCE.md` and
`SOURCE/LIBGFORTRAN-SOURCE.md` with binary hashes, source RPM checksum, source
locations, and license-text checksums. The libgfortran row uses GCC 8.5.0
GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1, never NumPy aggregate
wheel metadata. The complete
corresponding source RPM is included inside the same release archive, so it is
available from the same downloaded archive as the Linux binary without
depending on a separate source download remaining online. The generated
manifest records the actual ELF consumer and `$ORIGIN` search path used to
load the separate library. The package carries the Section 6 additional
permission for modifications and reverse engineering required to debug
libquadmath changes, while the other PolyForm Noncommercial terms remain in
force. These source and linkage checks are not release approval. The stager also copies the OpenSSL license from
`packaging/licenses/`, records the actual build interpreter's OpenSSL version
and resolves the Linux `_ssl` shared-library source/package when available.
Nuitka's compilation report is retained under `build/deploy/` for
local provenance review but is not copied into the archive because it may
contain machine-specific paths. The inventory is generated from this build's
actual ELF payload: file hashes are matched to installed wheel `RECORD`
metadata and the selected CPython runtime; other shared libraries must match
the local dynamic-loader cache and a `dpkg`-owned file. The corresponding
Debian package copyright file is copied to `LICENSES/Linux-packages/`. The
inventory records only archive-relative payload paths and includes package,
version, declared license, notice path, and evidence. CPython extension modules
are attributed to CPython only when their source path is linked to CPython by
Nuitka's report or the selected interpreter's standard-library extension
directory; their `ldd` dependencies are listed separately, and bundled
dependencies must have their own inventory rows and notices. System libraries
are hash-matched against loader-cache candidates (including resolved SONAME
targets), then verified through `dpkg` ownership, package version, and the
package copyright file. This handles versioned payload names that differ from
the SONAME alias. No license is inferred from a filename. Absolute source paths
are stripped from all generated inventory fields. Anything without verified
component, version, license, and included notice provenance is marked
unresolved, and final archive validation fails until it is classified. It does
not reuse a Windows inventory.

If any Linux payload remains unresolved, the build writes a local-only report
to `build/deploy/native-provenance-diagnostics.json` before archive validation
fails. It contains each staged file's full local path, SHA-256 and ELF build ID,
`readelf` dynamic entries, `ldd` resolutions, matching Nuitka report source
records, `dpkg` package/version data, and available package copyright/license
records. Nuitka's `${sys.real_prefix}` source token is resolved to the selected
interpreter's `sys.base_prefix`; loader-reported paths are retained while
dpkg ownership is also retried against resolved and `/lib`/`/usr/lib`
usr-merged spellings. Failed ownership queries and their diagnostics are
recorded rather than misread as a package name. This diagnostic file is not copied into the release. You can also run
the same collection explicitly from the project root:

```bash
.venv-xft/bin/python packaging/diagnose_native_provenance.py \
  build/deploy/MiLaS.dist build/release/MiLaS-linux-x86_64 \
  --nuitka-report build/deploy/nuitka-report.xml \
  --output build/deploy/native-provenance-diagnostics.json
```

The JSON intentionally contains local absolute paths for troubleshooting; do
not publish or include it in the archive. Review each record's source/package
evidence before assigning a component or license. Missing evidence stays
unresolved, and the archive validator remains blocking.

End-user launch:

```bash
tar -xzf MiLaS-linux-x86_64.tar.gz
cd MiLaS-linux-x86_64
./MiLaS.bin
```

The host still needs a compatible Linux desktop stack, graphics drivers, and
system libraries used by Tcl/Tk and Matplotlib.

## Windows x86_64

Build on Windows 10/11 x64 or Windows Server 2022 x64 with the Visual Studio
C++ build tools (`dumpbin.exe`) available on `PATH`. Run from PowerShell in the
project root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\build-windows.ps1
```

The script synchronizes the locked CPython 3.12.14 environment, verifies the
Tkinter and TkAgg imports, enables Nuitka's `tk-inter` plugin, and creates a
standalone folder. Nuitka's built-in package configuration collects NumPy and
Matplotlib components without the deprecated NumPy plugin or redundant
Matplotlib package-data option.

Output: `deliverables/MiLaS-windows-x64.zip`. It contains a
`MiLaS-windows-x64/` folder with `MiLaS.exe`, the bundled runtime, Tcl/Tk data,
assets, and the project README/license/notice files. Windows staging also
generates `BUNDLED_RUNTIME_INVENTORY.md` and
`LICENSES/Tcl-Tk-runtime-files.txt`, and copies the Tcl/Tk license texts,
`LICENSES/OpenSSL-Apache-2.0.txt`, and locked Python distribution licenses.
License collection checks installed `importlib.metadata` file entries, the
wheel `RECORD`, and Core Metadata `License-File` declarations; it prints a
per-distribution count and fails with those details if no license text can be
located.
The Windows build needs a licensed Visual Studio x64 REDIST directory.
`MILAS_VC_REDIST_CRT_DIR` is optional when the automatic search finds a
suitable directory. The search checks `VCToolsRedistDir` and Visual Studio
installations registered with `vswhere`. Set the variable if the search finds
no suitable directory or if you intend to use a particular directory. The
specified directory is added as a candidate; if several candidates are found,
the script selects the highest MSVC directory version. Check the build output
`Using Visual Studio REDIST CRT folder: ...` to confirm the selected path. The
variable must point to a directory matching
`VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC*.CRT`. Each candidate is checked
for `msvcp140.dll`, `vcruntime140.dll`, and `vcruntime140_1.dll`.
`prepare_windows_crt.py` also verifies each file's Microsoft
PE identity and product version, and checks that its staged bytes match the
source SHA-256. It scans the
standalone native files with `dumpbin`, then uses the build-only `delvewheel`
tool to rewrite NumPy extension import tables from the wheel's hash-suffixed
`msvcp140-<hash>.dll` name to `msvcp140.dll`. It copies the three CRT files
directly from that REDIST directory: `numpy.libs/msvcp140.dll`,
`vcruntime140.dll`, and `vcruntime140_1.dll`. The Microsoft DLL bytes are never
renamed or edited. The build fails if any hashed import or hashed CRT DLL
remains, if the Visual Studio PE identity does not match, or if source and
staged SHA-256 values differ. It runs the Nuitka executable with
`--verify-numpy-runtime`, which imports NumPy through the packaged loader
without opening the GUI. The generated inventory records the REDIST source
class, PE `CompanyName`, `OriginalFilename`, `ProductVersion`, and source and
staged hashes, without including local absolute paths. This records provenance;
the distributor must still have a license that permits redistribution.

The libffi DLL identifies ABI 8 but does not encode
its upstream release version; the inventory states this limitation rather
than inferring a release from the uv version. Staging includes the libffi,
zlib, OpenBLAS and LAPACK license texts plus the Microsoft terms notice.
NumPy's `numpy.libs/libscipy_openblas64_*.dll` receives both its separate
OpenBLAS BSD-3-Clause license and the bundled LAPACK BSD-3-Clause-Attribution
notice. The `kiwisolver/_cext.pyd` inventory row points directly to its
staged `LICENSE` and identifies the license as BSD-3-Clause.
Microsoft runtime redistribution remains subject to the applicable licensed
Visual Studio terms; consult that notice before distribution.
The inventory records actual Windows files and the OpenSSL/CPython version
reported by the uv-managed Python distribution. The build script checks the
generated files before packaging and verifies their entries in the resulting
ZIP with the same direct archive-member validator.

End-user launch:

1. Extract `MiLaS-windows-x64.zip`.
2. Open the extracted `MiLaS-windows-x64` folder and run `MiLaS.exe`.

## Runtime licensing and validation

`THIRD_PARTY_NOTICES.md` describes the Python, OpenSSL, Tcl/Tk, Matplotlib,
NumPy, NASA texture, and other third-party components. The stager records the
actual Tcl/Tk and OpenSSL versions reported by that platform's build
interpreter, lists platform-specific native files and their source/license,
writes `BUNDLED_RUNTIME_INVENTORY.md`, and writes
`LICENSES/Tcl-Tk-runtime-files.txt`. It copies separate Tcl and Tk license
texts, the Apache 2.0 license for OpenSSL 3.x, the complete LGPL 2.1 text for libquadmath, the GPL 3.0 and GCC Runtime
Library Exception 3.1 texts for libgfortran, and the matching GCC source RPM, the CPython PSF
license, and the locked Python distribution license files. Linux package copyright notices
are included only for dpkg packages whose file bytes match native payload in
that Linux build. The archive validator inspects actual tar/ZIP members,
rejects absolute machine paths and unresolved entries, and compares the
inventory against every archived ELF/PE payload; it fails on omissions, extra
rows, missing license references, or any unresolved native binary. Inspect
each generated inventory against its platform's final archive; Linux and
Windows contents and OpenSSL versions must not be assumed identical. The Linux
archive validator additionally checks that bundled libquadmath and libgfortran
match their pinned build IDs, license texts, source RPM, and separate inventory
rows. It checks that the bundled libquadmath has the
verified ELF build ID, that its per-build SHA-256 in `SOURCE/LIBQUADMATH-SOURCE.md`
matches the actual archived file, and that the exact pinned GCC source RPM and
the complete LGPL text are present with their verified hashes. It also checks
the actual ELF consumer's `DT_NEEDED` entry and `$ORIGIN` RPATH/RUNPATH, which
document the intended separately replaceable shared-library mechanism under
LGPL 2.1 section 6(b). The additional permission is included to address LGPL section 6 modification
and debugging rights while retaining the remaining PolyForm Noncommercial
terms. A Linux release is not validated until it is rebuilt locally and the
resulting archive passes the strict inventory and evidence checks.

The top-level `LICENSE` and `NOTICE` cover original MiLaS contributions.
Third-party license texts under `LICENSES/` apply only to their respective
components and do not modify the MiLaS license.

Do not treat the notices as a conclusion that any license obligations have
been met. Confirm applicable source, attribution, redistribution, and
third-party terms for each platform package before distributing it. The
standalone packages remain separate from the source ZIP.

After each native build, test its archive on a clean target machine or VM that
has neither Python nor uv installed. Extract it outside the source checkout;
launch it; verify both globe textures load; exercise the orbit, comparison
table, annual-insolation plot, all parameter sliders and quick comparisons;
confirm slider release redraws once; and test SPACE pause/resume and globe
rotation. Also confirm the Tcl/Tk runtime files and license are present in the
archive. Linux and Windows release builds are platform-specific and must be
built and tested on their respective operating systems.
