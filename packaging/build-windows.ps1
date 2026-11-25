$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$UvVersion = (& uv --version).Trim()
Write-Output "Python package manager: $UvVersion"

if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Build must run on x86_64 Windows.'
}
if (-not (Get-Command dumpbin.exe -ErrorAction SilentlyContinue)) {
    throw 'Build requires the Visual Studio C++ build tools with dumpbin.exe on PATH.'
}

function Get-MilasRedistCrtDirectories {
    $found = [System.Collections.Generic.List[string]]::new()
    $direct = @($env:MILAS_VC_REDIST_CRT_DIR, $env:VCToolsRedistDir) |
        Where-Object { $_ -and (Test-Path $_ -PathType Container) }
    foreach ($directory in $direct) {
        $resolved = (Resolve-Path $directory).Path
        if ((Test-Path (Join-Path $resolved 'msvcp140.dll') -PathType Leaf)) {
            $found.Add($resolved)
        }
        $x64 = Join-Path $resolved 'x64'
        if (Test-Path $x64 -PathType Container) {
            Get-ChildItem -Path $x64 -Directory -Filter 'Microsoft.VC*.CRT' |
                ForEach-Object { $found.Add($_.FullName) }
        }
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere -PathType Leaf) {
        $instances = & $vswhere -all -products '*' `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath
        foreach ($instance in $instances) {
            $redistRoot = Join-Path $instance 'VC\Redist\MSVC'
            if (-not (Test-Path $redistRoot -PathType Container)) { continue }
            foreach ($versionDirectory in Get-ChildItem -Path $redistRoot -Directory) {
                $x64 = Join-Path $versionDirectory.FullName 'x64'
                if (Test-Path $x64 -PathType Container) {
                    Get-ChildItem -Path $x64 -Directory -Filter 'Microsoft.VC*.CRT' |
                        ForEach-Object { $found.Add($_.FullName) }
                }
            }
        }
    }

    $found | Select-Object -Unique | Where-Object {
        (Test-Path (Join-Path $_ 'msvcp140.dll') -PathType Leaf) -and
        (Test-Path (Join-Path $_ 'vcruntime140.dll') -PathType Leaf) -and
        (Test-Path (Join-Path $_ 'vcruntime140_1.dll') -PathType Leaf)
    } | Sort-Object {
        $versionDirectory = Split-Path (Split-Path $_ -Parent) -Parent
        try { [version](Split-Path $versionDirectory -Leaf) }
        catch { [version]'0.0' }
    } -Descending
}

$RedistCandidates = @(Get-MilasRedistCrtDirectories)
if (-not $RedistCandidates.Count) {
    $where = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    throw "Could not locate an x64 Microsoft.VC*.CRT folder containing msvcp140.dll, vcruntime140.dll, and vcruntime140_1.dll. Install the Visual C++ build tools/REDIST component or set MILAS_VC_REDIST_CRT_DIR explicitly. Checked MILAS_VC_REDIST_CRT_DIR, VCToolsRedistDir, and Visual Studio instances discovered by vswhere at '$where'."
}
$RedistCrt = $RedistCandidates[0]
Write-Output "Using Visual Studio REDIST CRT folder: $RedistCrt"

$Build = Join-Path $Root 'build\deploy'
$Dist = Join-Path $Build 'MiLaS.dist'
$Release = Join-Path $Root 'build\release\MiLaS-windows-x64'
$Archive = Join-Path $Root 'deliverables\MiLaS-windows-x64.zip'
# Clear compiler and release outputs so previous Tcl/Tk or Python payloads cannot
# survive a new Nuitka build.
foreach ($Path in @($Build, $Release)) {
    if (Test-Path $Path) { Remove-Item $Path -Recurse -Force }
}
New-Item -ItemType Directory -Path $Build -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root 'build\release') -Force | Out-Null

uv python install 3.12.14
uv sync --locked --python 3.12.14
uv pip install --python .venv\Scripts\python.exe -r packaging\requirements-build-windows.txt
uv run --no-sync python -c "import tkinter; from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg; print('Tcl', tkinter.Tcl().eval('info patchlevel'), 'Tk', tkinter.TkVersion, 'TkAgg', FigureCanvasTkAgg.__name__)"
uv run --no-sync python -m nuitka packaging\MiLaS.py `
    --mode=standalone `
    --enable-plugin=tk-inter `
    --include-package-data=milas `
    --output-dir="$Build" `
    --output-filename=MiLaS.exe `
    --windows-console-mode=disable `
    --quiet

if (-not (Test-Path (Join-Path $Dist 'MiLaS.exe'))) {
    throw "Expected standalone executable missing: $Dist\MiLaS.exe"
}
foreach ($RelativePath in @(
    'matplotlib\mpl-data\matplotlibrc',
    'matplotlib\mpl-data\fonts\ttf\DejaVuSans.ttf'
)) {
    $PayloadPath = Join-Path $Dist $RelativePath
    if (-not (Test-Path $PayloadPath -PathType Leaf) -or (Get-Item $PayloadPath).Length -eq 0) {
        throw "Nuitka did not stage required Matplotlib data: $PayloadPath"
    }
}
foreach ($Pattern in @(
    (Join-Path $Dist 'numpy\_core\_multiarray_umath.*'),
    (Join-Path $Dist 'numpy\linalg\_umath_linalg.*')
)) {
    if (-not (Get-ChildItem -Path $Pattern -File -ErrorAction SilentlyContinue)) {
        throw "Nuitka did not stage a required NumPy native extension without the obsolete plugin: $Pattern"
    }
}
$RedistManifest = Join-Path $Build 'msvc-redist-manifest.json'
uv run --no-sync python packaging\prepare_windows_crt.py $Dist `
    --redist-dir $RedistCrt `
    --manifest $RedistManifest `
    --dumpbin (Get-Command dumpbin.exe).Source `
    --delvewheel (Join-Path $Root '.venv\Scripts\delvewheel.exe')
if ($LASTEXITCODE -ne 0) {
    throw 'NumPy CRT import rewrite or Visual Studio REDIST byte verification failed.'
}
$NumpyCheck = Join-Path $Dist 'MiLaS.exe'
& $NumpyCheck --verify-numpy-runtime
if ($LASTEXITCODE -ne 0) {
    throw 'NumPy import from the patched Nuitka standalone executable failed.'
}
Write-Output 'NumPy import verified through the patched Nuitka standalone executable.'
if (Test-Path $Release) { Remove-Item $Release -Recurse -Force }
New-Item -ItemType Directory -Path $Release -Force | Out-Null
Copy-Item (Join-Path $Dist '*') $Release -Recurse -Force
Copy-Item LICENSE, NOTICE $Release
uv run --no-sync python packaging\stage_release_licenses.py $Dist $Release `
    --python-source 'uv-managed CPython 3.12.14 (Astral python-build-standalone distribution)' `
    --msvc-redist-manifest $RedistManifest
$RequiredReleaseFiles = @(
    'BUNDLED_RUNTIME_INVENTORY.md',
    'LICENSES\Tcl-Tk-runtime-files.txt',
    'LICENSES\Tcl-license.terms',
    'LICENSES\Tk-license.terms',
    'LICENSES\OpenSSL-Apache-2.0.txt'
)
foreach ($RelativePath in $RequiredReleaseFiles) {
    $RequiredPath = Join-Path $Release $RelativePath
    if (-not (Test-Path $RequiredPath -PathType Leaf) -or (Get-Item $RequiredPath).Length -eq 0) {
        throw "Required release notice or inventory was not staged: $RequiredPath"
    }
}
New-Item -ItemType Directory -Path (Split-Path $Archive) -Force | Out-Null
if (Test-Path $Archive) { Remove-Item $Archive -Force }
Compress-Archive -Path $Release -DestinationPath $Archive
uv run --no-sync python packaging\verify_release_archive.py $Archive MiLaS-windows-x64
Write-Output "Created $Archive"
