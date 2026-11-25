# Third-Party Notices

This file identifies third-party assets and software used by MiLaS. It does
not change or replace any third-party terms, and it does not establish that a
particular distribution meets all applicable license obligations. The
PolyForm Noncommercial License applies only to original MiLaS material that
Stefan Caesar is entitled to license; it does not cover Python, Tcl/Tk,
Matplotlib, NumPy, the Earth texture, or other third-party components.

## Earth texture

Earth texture: Blue Marble: Next Generation, produced by Reto Stöckli, NASA
Earth Observatory, NASA Goddard Space Flight Center. Grayscale conversion for
MiLaS by Stefan Caesar. Credit: NASA Earth Observatory. Source:
https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-map/

NASA is not affiliated with, and does not endorse or recommend, MiLaS. See
NASA's media usage guidance:
https://www.nasa.gov/nasa-brand-center/images-and-media/

## Python runtime and locked distributions

MiLaS's runtime dependency set is resolved by `uv.lock`. The application
imports Matplotlib and NumPy directly. The listed transitive packages are part
of the runtime dependency closure. Native builds generate
`BUNDLED_RUNTIME_INVENTORY.md` from the installed lockfile closure and copy the
license/notice files supplied by those distributions into `LICENSES/Python/`.

| Role | Distribution | Locked version | License / terms | Source |
| --- | --- | --- | --- | --- |
| Runtime direct | Matplotlib | 3.11.2 | Matplotlib License Agreement; bundled fonts and other notices also apply | https://matplotlib.org/stable/project/license.html |
| Runtime direct | NumPy | 2.5.4 (2.4.6 for Python 3.11) | Wheel metadata: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | https://numpy.org/doc/stable/license.html |
| Runtime transitive | contourpy | 1.4.0 (1.3.3 for Python <3.12) | BSD 3-Clause | https://pypi.org/project/contourpy/ |
| Runtime transitive | cycler | 0.12.1 | BSD 3-Clause | https://github.com/matplotlib/cycler/blob/main/LICENSE |
| Runtime transitive | fonttools | 4.68.0 | MIT; upstream external notices also apply where included | https://github.com/fonttools/fonttools/blob/main/LICENSE |
| Runtime transitive | kiwisolver | 1.5.1 | BSD-3-Clause (Modified BSD / New or Revised BSD) | https://github.com/nucleic/kiwi/blob/main/LICENSE |
| Runtime transitive | packaging | 26.3 | `Apache-2.0 OR BSD-2-Clause` | https://github.com/pypa/packaging/blob/main/LICENSE |
| Runtime transitive | Pillow | 13.0.0 | MIT-CMU plus notices for bundled codecs | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| Runtime transitive | pyparsing | 3.3.3 | MIT | https://github.com/pyparsing/pyparsing/blob/master/LICENSE |
| Runtime transitive | python-dateutil | 3.0.0 | Dual license as stated in its bundled `LICENSE` | https://github.com/dateutil/dateutil/blob/master/LICENSE |
| Runtime transitive | six | 1.17.0 | MIT | https://github.com/benjaminp/six/blob/main/LICENSE |
| Runtime transitive | tzdata | 2026.5 | Apache-2.0 | https://github.com/python/tzdata/blob/master/LICENSE |
| Development only | pytest | 9.2.0 | MIT | https://github.com/pytest-dev/pytest/blob/main/LICENSE |

The conditional package versions above follow the Python markers in
`uv.lock`. Python versions below 3.11 are not supported by MiLaS. The build
scripts currently pin CPython 3.12.14, for which the lock resolves NumPy 2.5.4
and contourpy 1.4.0. `pytest` and its development-only dependencies are not
part of the application runtime closure.

Standalone packages also contain a CPython runtime. The native build stages
the applicable Python Software Foundation License Version 2 text from the
pinned CPython distribution. Python's `tkinter` module is Python standard
library code and is covered by the applicable CPython license; that is
separate from the license for the Tcl/Tk implementation it wraps.

<!-- platform:windows:start -->
## Windows standalone runtime libraries

The native inventory matches Windows runtime files by SHA-256 against the
selected CPython installation. The separately shipped `libffi-8.dll` is
identified as the libffi ABI 8 runtime and carries the upstream MIT license in
`packaging/licenses/libffi-LICENSE.txt`. Its PE image does not encode the
upstream libffi release version, so the inventory records that version as
unavailable instead of inferring it from the uv version or DLL name.
`zlib1.dll` is classified from its PE ProductVersion and carries the zlib
1.3.1 license in `packaging/licenses/zlib-1.3.1-LICENSE.txt`.

The Windows build rewrites NumPy extension import tables that refer to its
hash-suffixed `msvcp140` DLL so they request `msvcp140.dll`, then copies the
unmodified `msvcp140.dll`, `vcruntime140.dll`, and `vcruntime140_1.dll`
directly from the selected Visual Studio x64 REDIST directory. The generated
runtime inventory records each DLL's version, archive-relative path, and
matching source/staged SHA-256 hashes without writing local absolute paths.
This evidence does not establish that the distributor's Visual Studio license
permits redistribution; see the included Microsoft runtime terms.

The Windows NumPy wheel bundles `numpy.libs/libscipy_openblas64_*.dll`. Its
separate OpenBLAS BSD-3-Clause license and LAPACK BSD-3-Clause-Attribution
notice are included as `LICENSES/OpenBLAS-BSD-3-Clause.txt` and
`LICENSES/OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt`. These are distinct
from NumPy's aggregate wheel license metadata; the generated native inventory
records the exact DLL path and both notices.

<!-- platform:windows:end -->
## OpenSSL

OpenSSL 3.x is licensed under the Apache License, Version 2.0. The complete
Apache 2.0 text is kept at `packaging/licenses/OpenSSL-Apache-2.0.txt` and is
copied into each release as `LICENSES/OpenSSL-Apache-2.0.txt`. Staging fails if
it cannot find bundled OpenSSL library files or if the build interpreter
reports a version outside OpenSSL 3.x. Each platform's generated
`BUNDLED_RUNTIME_INVENTORY.md` records the version and provenance for that
build.

<!-- platform:linux:start -->
The Linux build uses CPython 3.12.14 and reports OpenSSL 3.x through Python's
`ssl.OPENSSL_VERSION`. Linux obtains OpenSSL through the locally installed
pyenv CPython's `_ssl` linkage; the generated inventory records the resolved
shared library path, and Debian package/version when available. Review those
details against the final Linux package.
<!-- platform:linux:end -->

Source and license references:

- OpenSSL 3.x licensing: https://www.openssl.org/source/license.html
- Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0.txt

<!-- platform:linux:start -->
### OpenSSL version observed in the supplied Linux release archive

The inspected Linux archive contains `libcrypto.so.3` and `libssl.so.3`,
whose embedded version string is OpenSSL 3.0.2 (15 March 2022). Its ELF build
metadata identifies `debian-amd64`; the archive does not preserve the exact
Debian/Ubuntu source package revision, so that revision cannot be stated from
the artifact alone. The Linux staging script now records the resolved library
path and Debian package/version from the local build host when available.

<!-- platform:linux:end -->
<!-- platform:linux:start -->
## GCC libgfortran in the Linux standalone package

The inspected Linux payload contains `libgfortran-83c28eba-468e71e5.so.5.0.0`.
Its ELF build ID is `04dbe32dceb80654422140fb7641f732065999c8` and its SHA-256 is
`a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160`. The
matching GCC source package is `gcc-8.5.0-28.el8_10.alma.1.src.rpm`, whose
SHA-256 is `c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0`.
It supplies GCC 8.5.0 source; the shared library has ABI major 5. The source
RPM contains `gcc-8.5.0-20210514.tar.xz`; its
`libgfortran/runtime/error.c` header identifies GPL version 3 or later and
GCC Runtime Library Exception 3.1. The complete texts from `COPYING3` and
`COPYING.RUNTIME` are included as `LICENSES/GPL-3.0.txt` and
`LICENSES/GCC-Runtime-Library-Exception-3.1.txt`. The binary is inventoried as
an independent GCC component, not under NumPy's aggregate wheel license.
`SOURCE/LIBGFORTRAN-SOURCE.md` records the source package, binary identity, and
license-text checksums for each Linux build.

Applicable terms: GNU GPL version 3 or later, with GCC Runtime Library
Exception 3.1. This is the license statement in the identified GCC source; the
Exception must be read together with the GPL text.

## GCC libquadmath in the Linux standalone package

The inspected Linux archive contains `libquadmath-2284e583-a9307bba.so.0.0.0`,
a separate shared library bundled in the NumPy runtime tree. It is not covered
by NumPy's aggregate BSD/0BSD/MIT/Zlib/CC0 wheel license metadata. The ELF GNU
build ID `c1c5f8d61f17e5579c4e85718845b74e4b94116c` matches the unmodified
`/usr/lib64/libquadmath.so.0.0.0` payload from AlmaLinux's
`libquadmath-8.5.0-28.el8_10.alma.1.x86_64.rpm`. The original RPM payload SHA-256
is `2284e5833f5ea62b141499d32373fc207878c1778fe004b4867bc41d75086be2`; the
binary RPM SHA-256 is
`e50ce3215fc8c5fcabb60664fe4933dc84db9f7b27d304e3168fafacb6016a9e`. The
repackaged ELF in the inspected archive has SHA-256
`990efc472f5819185bf5b67f000d243fd0791ef4f085eb4a4d5838f417f9a2da` and the
same build ID; a future build's generated
`SOURCE/LIBQUADMATH-SOURCE.md` records that exact archived hash for each build.

The Linux build now includes the matching AlmaLinux source package as
`SOURCE/gcc-8.5.0-28.el8_10.alma.1.src.rpm`, pinned to SHA-256
`c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0`. This
source RPM contains the exact GCC 8.5.0 source tarball, AlmaLinux `gcc.spec`,
and its build patches; the source tar contains the complete
`gcc-8.5.0-20210514/libquadmath/` source subtree, headers, build files, and
`COPYING.LIB`. The complete LGPL text from that source file is separately
included as `LICENSES/GNU-LGPL-2.1.txt`. The source RPM's exact URL is also
recorded in the generated manifest:
https://vault.almalinux.org/8/BaseOS/Source/Packages/gcc-8.5.0-28.el8_10.alma.1.src.rpm

The matching `COPYING.LIB` identifies GNU LGPL version 2.1 or, at the
recipient's option, any later version. The Linux release archive carries the
complete corresponding GCC source RPM beside the binary package contents at
`SOURCE/gcc-8.5.0-28.el8_10.alma.1.src.rpm`; it is pinned to the SHA-256 above.
That source RPM contains the GCC source tarball, the AlmaLinux `gcc.spec`, and
the patches used for that package build. The source tarball contains the full
`libquadmath` source subtree, headers, interface definitions, build files, and
`COPYING.LIB`. This is the concrete source copy delivered in the same release
archive and therefore at the same download location as the Linux binary. The
archive also includes the complete LGPL text and a generated manifest with the
archived library's own SHA-256. The build and archive validator fail if this
source RPM or license text is absent or has a different hash.

The concrete distribution conditions are assessed as follows; these records
are not a legal opinion:

- **LGPL 2.1 sections 1 and 2 (source distribution and modifications):** the
  source package carries upstream copyright and license notices. MiLaS does
  not modify libquadmath. If a future release modifies it, that release must
  identify the changes and their dates, preserve notices, and license the
  modified library under LGPL 2.1-or-later; it must be re-audited.
- **Section 4 (library object code):** the release distributes the complete
  corresponding machine-readable source with the library, plus the LGPL text.
  The library's LGPL terms govern that source and binary; the MiLaS license
  does not replace or restrict those rights.
- **Section 6 (the program using the library):** ELF inspection verifies that
  bundled `libgfortran` names the separate libquadmath SONAME in `DT_NEEDED`
  and uses an archive-relative `$ORIGIN` runtime path. The binary keeps the
  shared-library boundary and SONAME, so an interface-compatible replacement
  can be loaded without relinking the MiLaS executable. This is the factual
  basis for using the section 6(b) shared-library option; the generated
  manifest records the actual consumer and paths. Section 6 also requires a
  prominent notice with each copy that the library is used and covered by the
  LGPL, a copy of the LGPL, and—if the program displays copyright notices—a
  libquadmath copyright notice and direction to the license. The package
  README provides the prominent notice, and the complete text is in
  `LICENSES/GNU-LGPL-2.1.txt`. The current application does not display
  copyright notices at runtime; if that changes, the runtime notice condition
  must be met.
- **Section 6 terms:** the package includes
  `LGPL-2.1-Section-6-Additional-Permission.md`. It permits recipients to
  modify the MiLaS combined program for their own use and reverse engineer
  MiLaS only as reasonably necessary to debug modifications to libquadmath or
  the combined program, to the extent required by section 6. It adds no
  commercial-use permission or additional distribution/sublicensing rights;
  distribution remains subject to PolyForm Noncommercial and its other terms
  continue to apply.

The source RPM and LGPL text are hash-verified during staging. The generated
manifest records the ELF build ID and the hash of each archived library file;
the release archive validator checks that this evidence and the required files
match the completed Linux archive.

Matplotlib's bundled fonts include DejaVu (Bitstream Vera Font License) and
STIX (SIL Open Font License 1.1). Their license files are supplied with
Matplotlib and are copied into release `LICENSES/` when present in the
installed distribution. Pillow's distribution license file also contains
notices for its bundled image codecs; the stager copies distribution license
files rather than replacing their terms with this summary.

<!-- platform:linux:end -->
## Tcl/Tk and Tkinter

The source application uses Python's `tkinter` interface and Matplotlib's
TkAgg backend. Python documents `tkinter` as its standard interface to Tcl/Tk;
Tkinter is optional in some Python distributions and the Python build must
include both the `_tkinter` bridge and a usable Tcl/Tk runtime:
https://docs.python.org/3/library/tkinter.html

Tcl and Tk are separate upstream runtime components, each with its own
`license.terms` file. Their permissive terms require retaining copyright
notices and including the applicable notice in distributions. The upstream
texts are kept separately as `packaging/licenses/Tcl-license.terms` and
`packaging/licenses/Tk-license.terms`, and are copied into release `LICENSES/`:

- Tcl source and license terms: https://github.com/tcltk/tcl/blob/main/license.terms
- Tk source and license terms: https://github.com/tcltk/tk/blob/main/license.terms
- Tcl/Tk project licensing overview: https://www.tcl-lang.org/software/tcltk/

The Tcl/Tk version is provided by the Python interpreter used for each native
build, so it is not pinned in `uv.lock`. Release staging records the build
interpreter's Tcl/Tk versions and lists detected `_tkinter`, Tcl/Tk shared
libraries or DLLs, and Tcl/Tk script files in
`LICENSES/Tcl-Tk-runtime-files.txt`. Review those generated files against the
contents of each final archive. Nuitka's `tk-inter` standard plugin is enabled
to collect the Tcl/Tk libraries and data required by the application; the
exact native payload remains platform-build dependent.

## Build tools and platform libraries

Nuitka and its helper packages are pinned in
`packaging/requirements-build-*.txt`; they are packaging-only tools, not MiLaS
runtime dependencies.

<!-- platform:linux:start -->
The Linux standalone build does bundle native libraries. Its archive includes
the CPython extension modules, native libraries from the locked Python wheels,
and any system shared libraries Nuitka copied into that build's standalone
directory. `BUNDLED_RUNTIME_INVENTORY.md` lists each ELF payload found in the
actual archive and identifies its package/version, license, included notice,
and provenance evidence. Wheel files are matched by SHA-256 to installed
distribution `RECORD` entries; CPython files are matched to the selected
interpreter or to the source/destination entries in Nuitka's local compilation
report; remaining host libraries must match a loader-visible file owned by an
installed Debian package with a copied copyright notice. An unmatched file is
left unresolved and causes the archive validation to fail. The inspected
Linux release snapshot contains, among its native payload, `libX11.so.6`,
`libXft.so.2`, `libXrender.so.1`, `libxcb.so.1`, and native components from
Pillow, NumPy, Matplotlib, and CPython. These filenames identify payloads, not
their license or version; the generated evidence and included notices do that.

The Linux archive does not bundle the kernel, glibc or its dynamic loader,
the X11/Wayland display server or compositor, or graphics drivers. MiLaS still
requires a compatible Linux desktop environment and graphics stack. Other
shared libraries may be shipped or left as host requirements according to the
actual Nuitka build; consult that archive's inventory rather than assuming
that all Linux builds contain an identical set. Bundled system libraries
retain their upstream terms; this notice does not relicense them.

<!-- platform:linux:end -->
<!-- platform:windows:start -->
The Windows archive has its own platform-specific DLL inventory. Windows
system components not present in that archive remain host requirements and
are governed by their own terms.
<!-- platform:windows:end -->

