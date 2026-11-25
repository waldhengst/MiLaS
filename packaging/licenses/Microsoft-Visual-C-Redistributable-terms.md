# Microsoft Visual C++ Redistributable terms

The Windows build rewrites NumPy extension-module import tables that name a
hash-suffixed `msvcp140` DLL so they request the canonical `msvcp140.dll` name.
It then copies `msvcp140.dll`, `vcruntime140.dll`, and `vcruntime140_1.dll`
directly from the selected x64 Visual Studio
`VC\Redist\MSVC\<version>\x64\Microsoft.VC*.CRT` directory. The Microsoft
DLL bytes are not renamed or modified. The generated runtime inventory records
each archive-relative path, PE `OriginalFilename`, `CompanyName`,
`ProductVersion`, the REDIST source class, and equal SHA-256 hashes for source
and staged copies. The local source path is not included.

Microsoft states that redistribution of the Visual C++ Runtime Redistributable
package, merge modules, and individual binaries is limited to licensed Visual
Studio users and subject to Microsoft's Software License Terms. This package
uses the Visual Studio 2026 VC145 x64 runtime DLLs, with PE ProductVersion
14.51.36247.0, copied from `VC\Redist\MSVC\14.51.36231\x64\Microsoft.VC145.CRT`.
The Visual Studio 2026 REDIST list permits, subject to those terms, distribution
of files within `[VisualStudioFolder]\VC\redist` and its subfolders in
unmodified form. NumPy's extension-module import tables are rewritten during
packaging; the Microsoft runtime DLLs themselves are copied byte-for-byte from
the selected REDIST directory.

This notice documents licensing conditions; it does not grant a redistribution
right. Before distributing, confirm that the distributor has a valid Visual
Studio license, that the selected source directory is covered by that license,
and that all other conditions in the applicable license terms are met.

Official Microsoft references:

- Redistribute Visual C++ files: <https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files>
- Visual Studio 2026 distributable-code list (VC145): <https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution>
- Visual Studio License Directory: <https://visualstudio.microsoft.com/license-terms/>
