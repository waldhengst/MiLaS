#!/usr/bin/env python3
"""Use unmodified Visual Studio REDIST CRT files in the Windows release tree.

NumPy's repaired Windows wheel rewrites imports in its extension modules to a
hash-named copy of msvcp140.dll. This script rewrites those import-table names
to the canonical filename, then copies the original-named CRT DLLs directly
from the selected Visual Studio REDIST directory. It never renames or edits a
Microsoft DLL.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath


CRT_FILES = {
    "msvcp140.dll": Path("numpy.libs") / "msvcp140.dll",
    "vcruntime140.dll": Path("vcruntime140.dll"),
    "vcruntime140_1.dll": Path("vcruntime140_1.dll"),
}
HASHED_MSVCP = re.compile(r"msvcp140-[0-9a-f]{32}\.dll", re.I)
REDIST_PATH = re.compile(
    r"(?:^|[/\\])VC[/\\]Redist[/\\]MSVC[/\\][^/\\]+[/\\]"
    r"x64[/\\]Microsoft\.VC\d+\.CRT[/\\]?$",
    re.I,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dependencies(path: Path, dumpbin: str) -> list[str]:
    result = subprocess.run(
        [dumpbin, "/nologo", "/dependents", str(path)],
        check=True, capture_output=True, text=True, errors="replace",
    )
    dependencies = re.findall(
        r"(?im)^\s*(?:DLL Name:\s*)?([A-Za-z0-9_.+-]+\.dll)\s*$",
        result.stdout,
    )
    return dependencies


def _stager_module():
    script = Path(__file__).with_name("stage_release_licenses.py")
    spec = importlib.util.spec_from_file_location("milas_release_stager", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load release license staging helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _numpy_record_entry(executable: Path, dist_dir: Path) -> dict[str, str]:
    """Resolve a Nuitka-staged extension to its exact NumPy wheel RECORD file."""
    distribution = importlib.metadata.distribution("numpy")
    relative = PurePosixPath(executable.relative_to(dist_dir).as_posix())
    matches = []
    for record in distribution.files or ():
        record_path = PurePosixPath(record.as_posix().replace("\\", "/"))
        if (
            record_path.parent != relative.parent
            or record_path.suffix.casefold() != relative.suffix.casefold()
            or not record_path.name.casefold().startswith(relative.stem.casefold() + ".")
        ):
            continue
        source = Path(distribution.locate_file(record))
        if not source.is_file():
            continue
        source_hash = sha256_file(source)
        if source_hash == sha256_file(executable):
            matches.append((record_path.as_posix(), source_hash))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one exact NumPy RECORD match for {relative}, found {len(matches)}"
        )
    record_entry, source_hash = matches[0]
    return {
        "distribution": "numpy",
        "version": distribution.version,
        "record_entry": record_entry,
        "source_sha256": source_hash,
    }


def prepare(
    dist_dir: Path,
    redist_dir: Path,
    manifest_path: Path,
    dumpbin: str = "dumpbin.exe",
    delvewheel: str = "delvewheel.exe",
) -> dict[str, object]:
    """Patch NumPy import names and stage only direct REDIST copies of CRTs."""
    dist_dir = dist_dir.resolve(strict=True)
    redist_dir = redist_dir.resolve(strict=True)
    if not REDIST_PATH.search(str(redist_dir)):
        raise RuntimeError(
            "REDIST directory must be VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC*.CRT"
        )
    missing = [name for name in CRT_FILES if not (redist_dir / name).is_file()]
    if missing:
        raise RuntimeError("Visual Studio REDIST source is missing: " + ", ".join(missing))

    stager = _stager_module()
    sources: dict[str, Path] = {name: redist_dir / name for name in CRT_FILES}
    metadata: dict[str, dict[str, str]] = {}
    for name, source in sources.items():
        expected = Path(name).name
        company = stager._windows_file_company_name(source)
        original = stager._windows_file_original_filename(source)
        version = stager._windows_file_product_version(source)
        if company != "Microsoft Corporation" or original != expected or not version:
            raise RuntimeError(
                f"REDIST file identity mismatch for {name}: company={company!r}, "
                f"OriginalFilename={original!r}, ProductVersion={version!r}"
            )
        metadata[name] = {
            "product_version": version,
            "sha256": sha256_file(source),
            "redist_source": (
                "Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\"
                + redist_dir.name
            ),
        }

    native = sorted(
        path for path in dist_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".pyd", ".dll", ".exe"}
    )
    rewrites: list[tuple[Path, str]] = []
    for executable in native:
        for dependency in _dependencies(executable, dumpbin):
            found = HASHED_MSVCP.fullmatch(dependency.strip())
            if found:
                rewrites.append((executable, dependency.strip()))
    if not rewrites:
        raise RuntimeError(
            "No hash-named msvcp140.dll imports were found; refusing to replace the NumPy CRT blindly"
        )

    old_names = sorted({old for _path, old in rewrites}, key=str.casefold)
    if len(old_names) != 1:
        raise RuntimeError(
            "NumPy payload imports multiple hash-named msvcp140 DLLs: "
            + ", ".join(old_names)
        )
    old_name = old_names[0]
    numpy_patch_records = []
    for executable in sorted({path for path, _old in rewrites}):
        if not executable.relative_to(dist_dir).as_posix().casefold().startswith("numpy/"):
            raise RuntimeError(
                "The hash-named msvcp140 import is outside NumPy; refusing to rewrite "
                + executable.relative_to(dist_dir).as_posix()
            )
        record_info = _numpy_record_entry(executable, dist_dir)
        source_digest = sha256_file(executable)
        if record_info["source_sha256"] != source_digest:
            raise RuntimeError(
                f"NumPy extension does not match its wheel RECORD entry: "
                f"{executable.relative_to(dist_dir).as_posix()}"
            )
        numpy_patch_records.append({
            "archive_path": executable.relative_to(dist_dir).as_posix(),
            **record_info,
            "old_import": old_name,
            "new_import": "msvcp140.dll",
        })
    old_dlls = [path for path in dist_dir.rglob(old_name) if path.is_file()]
    if len(old_dlls) != 1:
        raise RuntimeError(
            f"Expected exactly one bundled {old_name}, found {len(old_dlls)}"
        )
    bundled_version = stager._windows_file_product_version(old_dlls[0])
    redist_version = metadata["msvcp140.dll"]["product_version"]
    if not bundled_version or tuple(map(int, redist_version.split("."))) < tuple(
        map(int, bundled_version.split("."))
    ):
        raise RuntimeError(
            f"Selected REDIST msvcp140.dll {redist_version} is older than the wheel copy {bundled_version}"
        )
    for name in ("vcruntime140.dll", "vcruntime140_1.dll"):
        existing = dist_dir / CRT_FILES[name]
        existing_version = stager._windows_file_product_version(existing) if existing.is_file() else None
        selected_version = metadata[name]["product_version"]
        if existing_version and tuple(map(int, selected_version.split("."))) < tuple(
            map(int, existing_version.split("."))
        ):
            raise RuntimeError(
                f"Selected REDIST {name} {selected_version} is older than the staged "
                f"CPython runtime {existing_version}"
            )

    for executable, old in rewrites:
        subprocess.run(
            [delvewheel, "replace-needed", str(executable), "-change", old, "msvcp140.dll"],
            check=True, capture_output=True, text=True, errors="replace",
        )

    for executable in native:
        remaining = [
            dependency.strip() for dependency in _dependencies(executable, dumpbin)
            if HASHED_MSVCP.fullmatch(dependency.strip())
        ]
        if remaining:
            raise RuntimeError(
                f"Hashed CRT import remains in {executable.relative_to(dist_dir)}: "
                + ", ".join(remaining)
            )
    if not any(
        dependency.strip().casefold() == "msvcp140.dll"
        for executable, _old in rewrites
        for dependency in _dependencies(executable, dumpbin)
    ):
        raise RuntimeError("Import-table rewrite did not produce msvcp140.dll dependencies")

    for entry in numpy_patch_records:
        patched_file = dist_dir / PurePosixPath(entry["archive_path"])
        entry["patched_sha256"] = sha256_file(patched_file)
        if entry["patched_sha256"] == entry["source_sha256"]:
            raise RuntimeError(
                f"NumPy extension hash did not change after import-table rewrite: {entry['archive_path']}"
            )

    for name, relative in CRT_FILES.items():
        target = dist_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources[name], target)
        staged_hash = sha256_file(target)
        if staged_hash != metadata[name]["sha256"]:
            raise RuntimeError(f"Staged {name} differs from its REDIST source bytes")
        metadata[name]["staged_sha256"] = staged_hash

    for old_dll in old_dlls:
        old_dll.unlink()
    leftovers = [
        path.relative_to(dist_dir).as_posix()
        for path in dist_dir.rglob("*")
        if path.is_file() and HASHED_MSVCP.fullmatch(path.name)
    ]
    if leftovers:
        raise RuntimeError("Hash-named msvcp140 payloads remain: " + ", ".join(leftovers))

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "redist_files": metadata,
        "numpy_patched_modules": numpy_patch_records,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--redist-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dumpbin", default="dumpbin.exe")
    parser.add_argument("--delvewheel", default="delvewheel.exe")
    args = parser.parse_args()
    prepared = prepare(
        args.dist_dir, args.redist_dir, args.manifest, args.dumpbin, args.delvewheel
    )
    for name, evidence in prepared["redist_files"].items():
        print(
            f"REDIST CRT {name}: version {evidence['product_version']}; "
            f"SHA-256 {evidence['sha256']} (source and staged bytes match)"
        )
    print("NumPy CRT import names use msvcp140.dll; no hash-named CRT DLL remains")
    for item in prepared["numpy_patched_modules"]:
        print(
            f"Patched NumPy import {item['archive_path']}: "
            f"RECORD source SHA-256 {item['source_sha256']}; "
            f"patched SHA-256 {item['patched_sha256']}"
        )


if __name__ == "__main__":
    main()
