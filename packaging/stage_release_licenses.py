#!/usr/bin/env python3
"""Stage upstream license texts and a file-based runtime inventory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import ssl
import sysconfig
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from packaging.markers import Marker
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
STATIC_LICENSES = ROOT / "packaging" / "licenses"

# Provenance verified from the Linux release ELF build ID, AlmaLinux's exact
# binary RPM, and the matching source RPM. Do not infer this from its filename.
LIBQUADMATH_BUILD_ID = "c1c5f8d61f17e5579c4e85718845b74e4b94116c"
LIBQUADMATH_SOURCE_NAME = "gcc-8.5.0-28.el8_10.alma.1.src.rpm"
LIBQUADMATH_SOURCE_URL = (
    "https://vault.almalinux.org/8/BaseOS/Source/Packages/"
    + LIBQUADMATH_SOURCE_NAME
)
LIBQUADMATH_SOURCE_SHA256 = "c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0"
LIBQUADMATH_BINARY_RPM = "libquadmath-8.5.0-28.el8_10.alma.1.x86_64.rpm"
LIBQUADMATH_BINARY_RPM_SHA256 = "e50ce3215fc8c5fcabb60664fe4933dc84db9f7b27d304e3168fafacb6016a9e"
LIBQUADMATH_ORIGINAL_BINARY_SHA256 = "2284e5833f5ea62b141499d32373fc207878c1778fe004b4867bc41d75086be2"
LIBQUADMATH_LGPL_SHA256 = "32434afcc8666ba060e111d715bfdb6c2d5dd8a35fa4d3ab8ad67d8f850d2f2b"
LIBGFORTRAN_BUILD_ID = "04dbe32dceb80654422140fb7641f732065999c8"
LIBGFORTRAN_BINARY_SHA256 = "a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160"
LIBGFORTRAN_SOURCE_NAME = LIBQUADMATH_SOURCE_NAME
LIBGFORTRAN_SOURCE_URL = LIBQUADMATH_SOURCE_URL
LIBGFORTRAN_SOURCE_SHA256 = LIBQUADMATH_SOURCE_SHA256
LIBGFORTRAN_GPL_SHA256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
LIBGFORTRAN_EXCEPTION_SHA256 = "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74"

LICENSE_LABELS = {
    "cycler": "BSD-3-Clause",
    "kiwisolver": "BSD-3-Clause",
    "matplotlib": "Matplotlib License Agreement; bundled upstream notices",
    "python-dateutil": "Dual license; see bundled LICENSE",
}


def lock_runtime_distributions() -> list[tuple[str, str, str]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    records = lock["package"]
    by_name: dict[str, list[dict]] = {}
    for record in records:
        by_name.setdefault(canonicalize_name(record["name"]), []).append(record)

    installed: dict[str, importlib.metadata.Distribution] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            installed[canonicalize_name(name)] = dist

    root_record = next(
        record for record in records if canonicalize_name(record["name"]) == "milas"
    )
    pending = [dependency for dependency in root_record.get("dependencies", [])]
    seen: set[str] = set()
    selected: list[tuple[str, str, str]] = []
    marker_env = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_full_version": ".".join(map(str, sys.version_info[:3])),
        "sys_platform": sys.platform,
        "platform_system": {"win32": "Windows", "darwin": "Darwin"}.get(
            sys.platform, "Linux"
        ),
        "platform_machine": os.uname().machine if hasattr(os, "uname") else "AMD64",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }

    while pending:
        dependency = pending.pop()
        marker = dependency.get("marker")
        if marker and not Marker(marker).evaluate(marker_env):
            continue
        name = canonicalize_name(dependency["name"])
        if name in seen:
            continue
        seen.add(name)
        dist = installed.get(name)
        if dist is None:
            raise RuntimeError(f"Runtime dependency {name!r} is not installed")
        version = dist.version
        record = next(
            (r for r in by_name.get(name, []) if r["version"] == version), None
        )
        if record is None:
            raise RuntimeError(f"Installed {name}=={version} is not in uv.lock")
        license_text = LICENSE_LABELS.get(name) or (
            dist.metadata.get("License-Expression")
            or dist.metadata.get("License")
            or "See bundled upstream license files"
        )
        selected.append((name, version, license_text.splitlines()[0]))
        pending.extend(record.get("dependencies", []))

    return sorted(selected)


def copy_dist_license_files(destination: Path, package_names: set[str]) -> list[str]:
    """Copy license texts recorded in wheel files, RECORD, or core metadata.

    Wheel installers and importlib.metadata versions do not expose license
    files identically: ``Distribution.files`` can be missing, incomplete, or
    contain paths that differ from the PEP 639 ``License-File`` resources.
    Try all three sources, while retaining per-distribution diagnostics.
    """
    copied: list[str] = []
    package_names = {canonicalize_name(name) for name in package_names}
    matched_names: set[str] = set()
    diagnostics: list[str] = []

    def safe_relative(value: object) -> str | None:
        relative = str(value).replace("\\", "/").strip()
        path = Path(relative)
        parts = relative.split("/")
        if (
            not relative
            or relative.startswith("/")
            or re.match(r"^[A-Za-z]:", relative)
            or ".." in parts
            or path.is_absolute()
        ):
            return None
        while relative.startswith("./"):
            relative = relative[2:]
        return relative

    def metadata_values(metadata, field: str) -> list[str]:
        try:
            values = metadata.get_all(field, [])
        except (AttributeError, TypeError):
            value = metadata.get(field)
            values = [value] if value else []
        if isinstance(values, str):
            values = [values]
        return [str(value).strip() for value in values if str(value).strip()]

    def is_license_path(relative: str, declared: list[str]) -> bool:
        lower = relative.lower()
        base = lower.rsplit("/", 1)[-1]
        if (
            ".dist-info/licenses/" in lower
            or (
                ".dist-info/" in lower
                and base.startswith(("license", "copying", "notice", "copyright"))
            )
            or ("mpl-data/fonts/" in lower and "license" in base)
        ):
            return True
        if lower.startswith("licenses/") and base.startswith(
            ("license", "copying", "notice", "copyright")
        ):
            return True
        for value in declared:
            license_path = value.replace("\\", "/").strip()
            while license_path.startswith("./"):
                license_path = license_path[2:]
            license_path = license_path.lower()
            if not license_path:
                continue
            forms = {license_path}
            if not license_path.startswith("licenses/"):
                forms.add("licenses/" + license_path)
            if any(lower == form or lower.endswith("/" + form) for form in forms):
                return True
        return False

    def output_relative(relative: str) -> str:
        match = re.search(
            r"(?:\.dist-info/licenses/|\.dist-info/|(?:^|/)licenses/)(.+)$",
            relative,
            re.I,
        )
        if match:
            return match.group(1)
        return relative.rsplit("/", 1)[-1]

    for dist in importlib.metadata.distributions():
        try:
            name = dist.metadata.get("Name")
        except (AttributeError, TypeError):
            name = None
        if not name:
            continue
        canonical_name = canonicalize_name(name)
        if canonical_name not in package_names:
            continue
        matched_names.add(canonical_name)
        try:
            version = dist.version
        except Exception:
            version = dist.metadata.get("Version", "unknown")

        declared = metadata_values(dist.metadata, "License-File")
        entries: dict[str, object] = {}
        files_error = None
        try:
            dist_files = dist.files
        except Exception as exc:  # metadata providers may fail while reading RECORD
            dist_files = None
            files_error = f"{type(exc).__name__}: {exc}"
        for item in dist_files or ():
            relative = safe_relative(item)
            if relative:
                entries.setdefault(relative, item)

        record_count = 0
        record_error = None
        try:
            record_text = dist.read_text("RECORD")
        except (OSError, UnicodeError, AttributeError, TypeError) as exc:
            record_text = None
            record_error = f"{type(exc).__name__}: {exc}"
        if record_text:
            try:
                for row in csv.reader(io.StringIO(record_text)):
                    if row:
                        relative = safe_relative(row[0])
                        if relative:
                            entries.setdefault(relative, row[0])
                            record_count += 1
            except csv.Error as exc:
                record_error = f"{type(exc).__name__}: {exc}"

        candidates: dict[str, Path] = {}
        lookup_errors: list[str] = []
        for relative, locator in entries.items():
            if not is_license_path(relative, declared):
                continue
            try:
                source = Path(dist.locate_file(locator))
                if source.is_file():
                    candidates.setdefault(relative, source)
            except (OSError, TypeError, ValueError) as exc:
                lookup_errors.append(f"{relative}: {type(exc).__name__}: {exc}")

        target_root = destination / "Python" / f"{name}-{version}"
        copied_for_dist = 0
        for relative, source in candidates.items():
            target = target_root / output_relative(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(str(target.relative_to(destination)))
            copied_for_dist += 1

        # Core Metadata 2.4's License-File headers identify resources below
        # .dist-info/licenses. Distribution.read_text() resolves resources
        # relative to its metadata directory, even when RECORD is unavailable.
        read_text_errors: list[str] = []
        for value in declared:
            normalized = value.replace("\\", "/").strip()
            while normalized.startswith("./"):
                normalized = normalized[2:]
            if not normalized or safe_relative(normalized) is None:
                lookup_errors.append(f"unsafe License-File metadata path: {value!r}")
                continue
            resources = [normalized]
            if not normalized.lower().startswith("licenses/"):
                resources.insert(0, "licenses/" + normalized)
            for resource in dict.fromkeys(resources):
                try:
                    text = dist.read_text(resource)
                except (OSError, UnicodeError, AttributeError, TypeError) as exc:
                    read_text_errors.append(f"{resource}: {type(exc).__name__}: {exc}")
                    continue
                if text is None:
                    continue
                relative_output = output_relative(resource)
                target = target_root / relative_output
                if target.exists():
                    break
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="")
                copied.append(str(target.relative_to(destination)))
                copied_for_dist += 1
                break

        diagnostics.append(
            f"{name}=={version}: copied={copied_for_dist}, importlib.files={len(dist_files or ())}, "
            f"RECORD entries={record_count}, License-File={declared or 'none'}"
            + (f", files error={files_error}" if files_error else "")
            + (f", RECORD error={record_error}" if record_error else "")
            + (
                f", unresolved metadata resources={read_text_errors}"
                if read_text_errors and not copied_for_dist else ""
            )
            + (f", lookup errors={lookup_errors}" if lookup_errors else "")
        )

    missing_distributions = sorted(package_names - matched_names)
    print(
        f"Python license staging: copied {len(copied)} license file(s) from "
        f"{len(matched_names)} of {len(package_names)} locked runtime distributions."
    )
    for line in diagnostics:
        print(f"  {line}")
    if missing_distributions:
        print("  No installed metadata entry for locked package(s): " + ", ".join(missing_distributions))
    return copied


_PLATFORM_MARKER = re.compile(r"^<!-- platform:(linux|windows):(start|end) -->$")


def render_platform_document(text: str, platform_name: str) -> str:
    """Remove non-target platform blocks and their marker lines."""
    target = platform_name.casefold()
    if target not in {"linux", "windows"}:
        raise ValueError(f"Unsupported documentation platform: {platform_name!r}")

    output: list[str] = []
    active: str | None = None
    include = True
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        marker = _PLATFORM_MARKER.fullmatch(line.rstrip("\r\n"))
        if marker is None:
            if include:
                output.append(line)
            continue
        platform, boundary = marker.groups()
        if boundary == "start":
            if active is not None:
                raise RuntimeError(f"Nested platform documentation marker at line {line_number}")
            active = platform
            include = platform == target
        else:
            if active is None:
                raise RuntimeError(f"Unmatched platform documentation end marker at line {line_number}")
            if active != platform:
                raise RuntimeError(f"Mismatched platform documentation marker at line {line_number}")
            active = None
            include = True
    if active is not None:
        raise RuntimeError(f"Unterminated {active} documentation block")
    return "".join(output)


def stage_platform_documentation(release_dir: Path, platform_name: str) -> None:
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        source = ROOT / name
        rendered = render_platform_document(source.read_text(encoding="utf-8"), platform_name)
        (release_dir / name).write_text(rendered, encoding="utf-8", newline="\n")


def copy_static_license_files(licenses_dir: Path, platform_name: str) -> list[str]:
    """Copy platform-applicable static texts while retaining shared notices."""
    windows_excluded = {
        "GNU-LGPL-2.1.txt",
        "GPL-3.0.txt",
        "GCC-Runtime-Library-Exception-3.1.txt",
    }
    linux_excluded = {"Microsoft-Visual-C-Redistributable-terms.md"}
    copied: list[str] = []
    for source in STATIC_LICENSES.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(STATIC_LICENSES)
        if platform_name == "Windows" and len(relative.parts) == 1 and source.name in windows_excluded:
            continue
        if platform_name == "Linux" and len(relative.parts) == 1 and source.name in linux_excluded:
            continue
        target = licenses_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative.as_posix())
    return copied


def verify_staged_python_license_files(
    licenses_dir: Path, copied: list[str]
) -> None:
    """Verify returned dependency-license paths exist under LICENSES/Python.

    ``str(Path)`` uses backslashes on Windows, while the staging paths are
    archive-relative and use forward slashes. Normalize both forms before
    checking the actual files so a successful Windows copy is not mistaken
    for an empty result.
    """
    if not copied:
        raise RuntimeError("No Python dependency license files were collected")

    invalid: list[str] = []
    missing: list[str] = []
    verified = 0
    for returned_path in copied:
        normalized = str(returned_path).replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) < 3
            or relative.parts[0] != "Python"
        ):
            invalid.append(returned_path)
            continue
        path = licenses_dir.joinpath(*relative.parts)
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(returned_path)
            continue
        verified += 1

    if invalid or missing or verified == 0:
        details = []
        if invalid:
            details.append("invalid returned path(s): " + ", ".join(invalid))
        if missing:
            details.append("missing or empty staged file(s): " + ", ".join(missing))
        raise RuntimeError(
            "Python dependency license staging did not produce verified files under "
            "LICENSES/Python/" + (" (" + "; ".join(details) + ")" if details else "")
        )


def find_tk_payloads(dist_dir: Path, release_dir: Path) -> list[str]:
    """List Tcl/Tk libraries, bridge files, and scripts present in the package."""
    found = []
    for path in release_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(release_dir)).replace("\\", "/")
        lower_path = relative.lower()
        filename = path.name.lower()
        is_bridge = filename.startswith("_tkinter")
        is_tcltk_library = (
            re.search(r"(?:^|lib)(?:tcl|tk)[^/\\]*\.(?:dll|pyd|so(?:\.[0-9]+)*)$", filename)
            or re.search(r"(?:^|lib)tcl[^/\\]*tk[^/\\]*\.(?:dll|so(?:\.[0-9]+)*)$", filename)
        )
        is_script_tree = re.search(
            r"(?:^|/)(?:tcl|tk)[0-9.]+/.*\.tcl$", lower_path,
        )
        is_script_archive = re.search(
            r"(?:^|/)(?:lib)?(?:tcl|tk)[0-9.]*\.zip$", lower_path,
        )
        if is_bridge or is_tcltk_library or is_script_tree or is_script_archive:
            found.append(f"release package/{relative}")
    return sorted(set(found))


def tkinter_runtime_versions() -> tuple[str, str]:
    import tkinter

    tcl_version = tkinter.Tcl().eval("info patchlevel")
    tk_version = str(tkinter.TkVersion)
    return tcl_version, tk_version


def find_openssl_payloads(dist_dir: Path, release_dir: Path) -> list[str]:
    """Find the OpenSSL libraries included in this platform's build."""
    pattern = re.compile(r"^(?:lib)?(?:ssl|crypto)(?:[-.]|$)", re.IGNORECASE)
    return sorted(
        f"{str(path.relative_to(release_dir)).replace(os.sep, '/')}"
        for path in release_dir.rglob("*")
        if path.is_file() and pattern.match(path.name)
    )


def linux_openssl_provenance(dist_dir: Path) -> str:
    """Resolve the OpenSSL libraries used by the bundled _ssl extension."""
    if os.name == "nt":
        return ""
    provenance = []
    try:
        import _ssl
        interpreter_extension = Path(_ssl.__file__)
    except Exception:
        interpreter_extension = None
    extensions = ([interpreter_extension] if interpreter_extension else []) + [
        path for path in dist_dir.rglob("_ssl*.so") if path.is_file()
    ]
    for extension in extensions:
        if not extension.is_file():
            continue
        try:
            result = subprocess.run(
                ["ldd", str(extension)], check=False, capture_output=True, text=True
            )
        except OSError:
            continue
        for line in result.stdout.splitlines():
            if not any(name in line for name in ("libssl.so", "libcrypto.so")):
                continue
            match = re.search(r"=>\s+(/\S+)", line)
            if not match:
                continue
            resolved = Path(match.group(1))
            detail = resolved.name
            try:
                package_record = _dpkg_package_record(resolved)
                if package_record:
                    package = str(package_record["package"])
                    version = package_record["version"]
                    detail += (
                        f" (Debian package {package} {version})"
                        if version else f" (Debian package {package})"
                    )
            except OSError:
                pass
            provenance.append(detail)
    if provenance:
        return "Resolved by the deployment _ssl extension: " + "; ".join(sorted(set(provenance)))
    return "Provided through the build interpreter's OpenSSL-linked _ssl extension; distro package provenance was unavailable."


class NativeProvenance:
    def __init__(
        self, component: str, version: str, license: str, notice: str, evidence: str
    ) -> None:
        self.component = component
        self.version = version
        self.license = license
        self.notice = notice
        self.evidence = evidence


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elf_build_id(path: Path) -> str | None:
    """Return the GNU ELF build ID when present, without using names as proof."""
    try:
        result = subprocess.run(
            ["readelf", "-n", str(path)], check=False, capture_output=True,
            text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", result.stdout)
    return match.group(1).lower() if match else None


def elf_dynamic_metadata(path: Path) -> tuple[str | None, list[str], list[str]]:
    """Return ELF SONAME, NEEDED entries, and runtime search paths."""
    try:
        result = subprocess.run(
            ["readelf", "-d", str(path)], check=False, capture_output=True,
            text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, [], []
    soname_match = re.search(r"\(SONAME\).*\[([^]]+)\]", result.stdout)
    needed = re.findall(r"\(NEEDED\).*\[([^]]+)\]", result.stdout)
    paths = re.findall(r"\((?:RPATH|RUNPATH)\).*\[([^]]+)\]", result.stdout)
    return soname_match.group(1) if soname_match else None, needed, paths


def _looks_native(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in (".dll", ".pyd", ".so") or ".so." in name


def native_payload_files(root: Path) -> list[Path]:
    """Find native payload by ELF/PE signature, not just filename suffix."""
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                signature = stream.read(4)
        except OSError:
            continue
        if (
            signature == b"\x7fELF" or signature[:2] == b"MZ"
            or _looks_native(path) or path.suffix.lower() == ".exe"
        ):
            found.append(path)
    return sorted(found)


def stage_libquadmath_source(release_dir: Path, licenses_dir: Path) -> list[Path]:
    """Verify and document the exact source for the recognized libquadmath ELF.

    The build ID is the primary binary identity. A name alone is never used to
    assign this provenance. If the known build appears, the pinned source RPM
    and complete LGPL text are mandatory; otherwise staging fails closed.
    """
    matches = [
        path for path in native_payload_files(release_dir)
        if elf_build_id(path) == LIBQUADMATH_BUILD_ID
    ]
    if not matches:
        return []
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one ELF with the verified libquadmath build ID; "
            f"found {len(matches)}"
        )
    soname, _needed, _search_paths = elf_dynamic_metadata(matches[0])
    if not soname:
        raise RuntimeError("Could not verify the SONAME of the libquadmath payload")
    consumers = []
    for candidate in native_payload_files(release_dir):
        if candidate == matches[0]:
            continue
        _candidate_soname, needed, search_paths = elf_dynamic_metadata(candidate)
        if soname not in needed:
            continue
        origin_paths = [
            item
            for path_entry in search_paths
            for item in path_entry.split(":")
            if "$ORIGIN" in item
        ]
        if not origin_paths:
            raise RuntimeError(
                "libquadmath consumer does not use a verified $ORIGIN shared-library path: "
                + candidate.relative_to(release_dir).as_posix()
            )
        consumers.append((candidate, origin_paths))
    if not consumers:
        raise RuntimeError(
            "No bundled ELF consumer with a verified DT_NEEDED reference to "
            "libquadmath was found; cannot verify the LGPL shared-library mechanism"
        )
    source_rpm = release_dir / "SOURCE" / LIBQUADMATH_SOURCE_NAME
    if not source_rpm.is_file():
        raise RuntimeError(
            "The verified libquadmath payload requires the exact GCC source RPM "
            f"at SOURCE/{LIBQUADMATH_SOURCE_NAME}"
        )
    source_digest = sha256_file(source_rpm)
    if source_digest != LIBQUADMATH_SOURCE_SHA256:
        raise RuntimeError(
            "GCC source RPM SHA-256 does not match the verified AlmaLinux source: "
            f"{source_digest}"
        )
    lgpl_text = licenses_dir / "GNU-LGPL-2.1.txt"
    if not lgpl_text.is_file() or sha256_file(lgpl_text) != LIBQUADMATH_LGPL_SHA256:
        raise RuntimeError(
            "Missing or modified complete GNU LGPL 2.1 text for libquadmath"
        )

    manifest = [
        "# libquadmath source and binary provenance",
        "",
        "This record is generated from the actual Linux release payload. The library is bundled as a separate ELF shared object in the NumPy runtime tree; it is not covered by NumPy's BSD license metadata.",
        "",
        f"- Component: GCC libquadmath from AlmaLinux `libquadmath` runtime RPM `{LIBQUADMATH_BINARY_RPM}`.",
        "- Runtime package version: `8.5.0-28.el8_10.alma.1` (GCC `8.5.0`).",
        f"- Binary RPM SHA-256: `{LIBQUADMATH_BINARY_RPM_SHA256}`.",
        f"- Unmodified RPM payload SHA-256 (`/usr/lib64/libquadmath.so.0.0.0`): `{LIBQUADMATH_ORIGINAL_BINARY_SHA256}`.",
        "- The standalone build may rewrite ELF dynamic metadata and rename the shared object. The GNU build ID below ties the staged file to the RPM payload; its current archive SHA-256 is recorded separately for every file.",
        f"- Matching ELF build ID: `{LIBQUADMATH_BUILD_ID}`.",
        f"- Corresponding source package: `SOURCE/{LIBQUADMATH_SOURCE_NAME}`.",
        f"- Source RPM SHA-256: `{source_digest}`.",
        f"- Exact source RPM download: {LIBQUADMATH_SOURCE_URL}",
        "- The source RPM contains `gcc-8.5.0-20210514.tar.xz`, the AlmaLinux `gcc.spec`, and the release's build patches. The source tar contains `gcc-8.5.0-20210514/libquadmath/`, including its implementation, headers, build files, and `COPYING.LIB`.",
        "- Applicable library license: GNU LGPL 2.1 or later. The complete text extracted verbatim from that matching source tar is `LICENSES/GNU-LGPL-2.1.txt` (SHA-256: `" + LIBQUADMATH_LGPL_SHA256 + "`).",
        f"- ELF SONAME: `{soname}`.",
        "- Direct bundled consumers verified from ELF `DT_NEEDED` and runtime-path entries:",
        *[
            f"  - `{candidate.relative_to(release_dir).as_posix()}` requests `{soname}` and has `{', '.join(paths)}` in its RPATH/RUNPATH."
            for candidate, paths in consumers
        ],
        "- This is a separate dynamically loaded shared library. The package preserves a `$ORIGIN` lookup to the archive-relative library path; a compatible modified library can therefore be substituted at that path while preserving the recorded SONAME. The build verifies those ELF references, but does not claim to test arbitrary modified builds.",
        "",
        "## LGPL 2.1 distribution conditions recorded for this build",
        "",
        "- Sections 1 and 2: the matching source RPM carries the source package's copyright and license notices. This build does not modify libquadmath. Any future modified libquadmath must be re-audited, identify its changes and dates, preserve applicable notices, and remain under LGPL 2.1-or-later.",
        "- Section 4: this release archive includes the complete corresponding machine-readable source RPM and the complete LGPL text beside the bundled library. The source RPM SHA-256 above identifies the exact included source package.",
        "- Section 6 linkage option: the ELF consumer(s) and `$ORIGIN` runtime paths listed above preserve a replaceable, interface-compatible shared-library mechanism. This is the technical basis for relying on section 6(b); it is not inferred from the license label.",
        "- Section 6 notices: the package README prominently identifies libquadmath and the LGPL, and `LICENSES/GNU-LGPL-2.1.txt` supplies the license. The current application has no runtime copyright display; if that changes, show the libquadmath copyright notice and direct users to the included license.",
        "- Section 6 user rights: the package includes `LGPL-2.1-Section-6-Additional-Permission.md`. It permits recipients to modify the MiLaS combined program for their own use and reverse engineer MiLaS only as reasonably necessary to debug modifications to libquadmath or that combined program, to the extent required by section 6. It grants no commercial-use or additional distribution rights; other PolyForm Noncommercial terms continue to apply.",
        "",
        "## Bundled binary files",
        "",
    ]
    for path in matches:
        relative = path.relative_to(release_dir).as_posix()
        manifest.extend([
            f"### `{relative}`",
            "",
            f"- Archive-relative binary: `{relative}`",
            f"- SHA-256 of this staged binary: `{sha256_file(path)}`",
            f"- ELF build ID: `{elf_build_id(path)}`",
            "",
        ])
    manifest.extend([
        "## Source evidence status",
        "",
        "The matching source RPM and LGPL text were hash-verified during staging. The ELF build ID ties the runtime payload to that source, and the SHA-256 values above identify the archived binary files in this release.",
        "",
    ])
    source_dir = release_dir / "SOURCE"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "LIBQUADMATH-SOURCE.md").write_text(
        "\n".join(manifest), encoding="utf-8"
    )
    return matches


def stage_libgfortran_source(release_dir: Path, licenses_dir: Path) -> list[Path]:
    """Verify and document the GCC 8.5 libgfortran binary and license source."""
    matches = [
        path for path in native_payload_files(release_dir)
        if elf_build_id(path) == LIBGFORTRAN_BUILD_ID
    ]
    named = [
        path for path in native_payload_files(release_dir)
        if path.name == "libgfortran-83c28eba-468e71e5.so.5.0.0"
    ]
    if not matches and not named:
        return []
    if len(matches) != 1 or len(named) != 1 or matches[0] != named[0]:
        raise RuntimeError(
            "Expected exactly one libgfortran payload matching the pinned ELF build ID and filename"
        )
    binary = matches[0]
    if sha256_file(binary) != LIBGFORTRAN_BINARY_SHA256:
        raise RuntimeError("libgfortran payload SHA-256 does not match the inspected GCC 8.5 binary")
    source_rpm = release_dir / "SOURCE" / LIBGFORTRAN_SOURCE_NAME
    if not source_rpm.is_file() or sha256_file(source_rpm) != LIBGFORTRAN_SOURCE_SHA256:
        raise RuntimeError("libgfortran requires the pinned GCC 8.5.0 source RPM in SOURCE/")
    gpl = licenses_dir / "GPL-3.0.txt"
    exception = licenses_dir / "GCC-Runtime-Library-Exception-3.1.txt"
    if not gpl.is_file() or sha256_file(gpl) != LIBGFORTRAN_GPL_SHA256:
        raise RuntimeError("Missing or modified complete GPL-3.0 text from the matching GCC source")
    if not exception.is_file() or sha256_file(exception) != LIBGFORTRAN_EXCEPTION_SHA256:
        raise RuntimeError("Missing or modified complete GCC Runtime Library Exception 3.1 text")
    manifest = [
        "# libgfortran source and binary provenance", "",
        "This record is generated from the actual Linux release payload. `libgfortran` is a separate GCC runtime component and is not covered by NumPy's aggregate wheel license metadata.", "",
        "- Component: GCC `libgfortran` from the AlmaLinux GCC source package.",
        "- Source package version: `8.5.0-28.el8_10.alma.1` (upstream GCC `8.5.0`); shared-library ABI major: `5`.",
        f"- Matching ELF build ID: `{LIBGFORTRAN_BUILD_ID}`.",
        f"- Staged binary SHA-256: `{sha256_file(binary)}`.",
        f"- Corresponding source package: `SOURCE/{LIBGFORTRAN_SOURCE_NAME}`.",
        f"- Source RPM SHA-256: `{LIBGFORTRAN_SOURCE_SHA256}`.",
        f"- Source RPM URL: {LIBGFORTRAN_SOURCE_URL}",
        "- The source RPM contains `gcc-8.5.0-20210514.tar.xz`; `libgfortran/runtime/error.c` identifies GPL version 3 or later and the additional permissions in section 7 of GCC Runtime Library Exception 3.1. The complete license texts are extracted verbatim from the source tar's `COPYING3` and `COPYING.RUNTIME`.",
        f"- Applicable license: `GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1`; GPL text `LICENSES/GPL-3.0.txt` (SHA-256 `{LIBGFORTRAN_GPL_SHA256}`), exception text `LICENSES/GCC-Runtime-Library-Exception-3.1.txt` (SHA-256 `{LIBGFORTRAN_EXCEPTION_SHA256}`).", "",
        f"### `{binary.relative_to(release_dir).as_posix()}`", "",
        f"- Archive-relative binary SHA-256: `{sha256_file(binary)}`",
        f"- ELF build ID: `{LIBGFORTRAN_BUILD_ID}`", "",
    ]
    source_dir = release_dir / "SOURCE"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "LIBGFORTRAN-SOURCE.md").write_text("\n".join(manifest), encoding="utf-8")
    return matches


def _license_for_distribution(dist: importlib.metadata.Distribution) -> str:
    name = dist.metadata.get("Name", "")
    curated = LICENSE_LABELS.get(canonicalize_name(name))
    if curated:
        return curated
    return (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or "UNRESOLVED: installed distribution has no license metadata"
    ).splitlines()[0]


def _hash_index(paths: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in paths:
        try:
            if path.is_file():
                index.setdefault(sha256_file(path), []).append(path)
        except OSError:
            continue
    return index


def _path_free_text(value: str) -> str:
    """Remove machine-specific absolute paths before writing package evidence."""
    # Require a path component after the leading slash; otherwise ordinary
    # prose such as "license text / notice" would be mistaken for a root path.
    value = re.sub(r"(?<![A-Za-z0-9:/])/(?![/\s])[^\s|;,)]*", "[absolute path omitted]", value)
    return re.sub(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s|;,)]*", "[absolute path omitted]", value)


def _elf_linkage_evidence(path: Path, release_dir: Path) -> str:
    """Describe an ELF file's resolved dependencies without emitting host paths.

    Bundled dependencies are named by their archive-relative path. External
    dependencies are explicitly marked as system-provided; they are not
    assigned a component license by this evidence helper.
    """
    if os.name == "nt":
        return ""
    try:
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return ""
        result = subprocess.run(
            ["ldd", str(path)], check=False, capture_output=True, text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "ELF dynamic dependencies could not be read from this build payload"
    dependencies: list[str] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\S+)\s+=>\s+(/\S+)", line)
        if not match:
            continue
        soname, raw_target = match.groups()
        target = Path(raw_target)
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            dependencies.append(f"{soname} (unresolved by ldd)")
            continue
        try:
            relative = resolved.relative_to(release_dir.resolve()).as_posix()
        except ValueError:
            dependencies.append(f"{soname} (system-provided; not bundled at this path)")
        else:
            dependencies.append(f"{soname} (bundled as `{relative}`)")
    if not dependencies:
        return "ELF dependencies: no resolved shared-library dependencies reported"
    return "ELF dependencies: " + "; ".join(sorted(set(dependencies)))


def _python_provenance_index(
    runtime_names: set[str], python_source: str = ""
) -> dict[str, NativeProvenance]:
    """Match packaged binaries to installed distribution RECORDs or CPython."""
    index: dict[str, NativeProvenance] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if not name or canonicalize_name(name) not in runtime_names:
            continue
        notice = f"LICENSES/Python/{name}-{dist.version}/"
        for record in dist.files or ():
            path = Path(dist.locate_file(record))
            try:
                if path.is_file() and _looks_native(path):
                    digest = sha256_file(path)
                    is_wheel_msvc_runtime = (
                        sys.platform == "win32"
                        and re.fullmatch(
                            r"msvcp140(?:-[0-9a-f]{32})?\.dll", path.name, re.I,
                        ) is not None
                    )
                    microsoft_runtime = (
                        _windows_microsoft_runtime_provenance(path, record.as_posix())
                        if is_wheel_msvc_runtime else None
                    )
                    if microsoft_runtime is not None:
                        microsoft_runtime.evidence = (
                            f"SHA-256 match to installed {name} {dist.version} "
                            f"RECORD entry {record.as_posix()}; "
                            f"{microsoft_runtime.evidence}; source SHA-256 {digest}"
                        )
                        index.setdefault(digest, microsoft_runtime)
                    else:
                        index.setdefault(
                            digest,
                            NativeProvenance(
                                f"Python distribution {name}", dist.version,
                                _license_for_distribution(dist), notice,
                                f"SHA-256 match to installed {name} {dist.version} RECORD entry {record.as_posix()}",
                            ),
                        )
            except OSError:
                continue

    # CPython's stdlib extension modules and interpreter libraries are matched
    # by bytes to the selected build interpreter, not inferred from basenames.
    python_version = sys.version.split()[0]
    stdlib_native = []
    shared_dir = Path(sysconfig.get_config_var("DESTSHARED") or "")
    if shared_dir.is_dir():
        stdlib_native.extend(
            path for path in shared_dir.iterdir()
            if path.is_file() and (
                path.suffix.lower() in (".so", ".pyd", ".dll")
                or ".so." in path.name
            )
        )
    library_dir = Path(sysconfig.get_config_var("LIBDIR") or "")
    if library_dir.is_dir():
        stdlib_native.extend(
            path for path in library_dir.glob("libpython*") if path.is_file()
        )
    # python-build-standalone/uv installs Windows extension modules in
    # ``DLLs`` and the shared interpreter DLL at the installation root. These
    # locations are not necessarily reflected by DESTSHARED or LIBDIR. Include
    # only CPython extension modules and the interpreter DLL here: unrelated
    # native DLLs such as libffi, zlib, and the Microsoft CRT need their own
    # component and license evidence.
    stdlib_native.extend(_windows_cpython_native_sources())
    psf = NativeProvenance(
        "CPython runtime", python_version, "PSF-2.0",
        "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
        f"SHA-256 match to the selected CPython {python_version} runtime",
    )
    for digest, paths in _hash_index(stdlib_native).items():
        # Preserve a more specific installed wheel provenance if one exists.
        index.setdefault(digest, psf)
    for digest, provenance in _windows_runtime_library_provenance(python_source).items():
        index.setdefault(digest, provenance)
    if ssl.OPENSSL_VERSION.startswith("OpenSSL 3.") and os.name != "nt":
        try:
            import _ssl
            linked = subprocess.run(
                ["ldd", str(Path(_ssl.__file__))], check=False,
                capture_output=True, text=True,
            ).stdout
            for line in linked.splitlines():
                if "libssl.so" not in line and "libcrypto.so" not in line:
                    continue
                match = re.search(r"=>\s+(/\S+)", line)
                if not match:
                    continue
                source = Path(match.group(1))
                if source.is_file():
                    index.setdefault(
                        sha256_file(source),
                        NativeProvenance(
                            "OpenSSL", ssl.OPENSSL_VERSION, "Apache-2.0",
                            "LICENSES/OpenSSL-Apache-2.0.txt",
                            f"SHA-256 match to {source.name} linked by the selected CPython _ssl extension",
                        ),
                    )
        except (OSError, subprocess.SubprocessError):
            pass
    elif ssl.OPENSSL_VERSION.startswith("OpenSSL 3.") and os.name == "nt":
        openssl = NativeProvenance(
            "OpenSSL", ssl.OPENSSL_VERSION, "Apache-2.0",
            "LICENSES/OpenSSL-Apache-2.0.txt",
            "Hash match to OpenSSL DLL shipped with selected CPython; version reported by CPython _ssl",
        )
        for base in {Path(sys.base_prefix), Path(sys.prefix)}:
            for pattern in ("libssl*.dll", "libcrypto*.dll", "ssl*.dll", "crypto*.dll"):
                for source in base.rglob(pattern):
                    try:
                        index.setdefault(sha256_file(source), openssl)
                    except OSError:
                        continue
    return index


def _windows_cpython_native_sources() -> list[Path]:
    """Return only CPython-owned Windows extension and interpreter files."""
    if sys.platform != "win32":
        return []
    base_prefix = Path(sys.base_prefix)
    dlls_dir = base_prefix / "DLLs"
    sources = list(dlls_dir.glob("*.pyd")) if dlls_dir.is_dir() else []
    for name in (
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        f"python{sys.version_info.major}.dll",
    ):
        candidate = base_prefix / name
        if candidate.is_file():
            sources.append(candidate)
    return [source for source in sources if source.is_file()]


def _windows_file_version_string(path: Path, value_name: str) -> str | None:
    """Read a PE version-resource string using Windows' version API."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    version_api = ctypes.WinDLL("version", use_last_error=True)
    get_size = version_api.GetFileVersionInfoSizeW
    get_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    get_size.restype = wintypes.DWORD
    get_info = version_api.GetFileVersionInfoW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    get_info.restype = wintypes.BOOL
    query = version_api.VerQueryValueW
    query.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL

    ignored = wintypes.DWORD()
    size = get_size(str(path), ctypes.byref(ignored))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not get_info(str(path), 0, size, data):
        return None
    translation_ptr = ctypes.c_void_p()
    translation_size = wintypes.UINT()
    if not query(data, r"\VarFileInfo\Translation",
                 ctypes.byref(translation_ptr), ctypes.byref(translation_size)):
        return None
    if translation_size.value < 4:
        return None
    language, codepage = ctypes.cast(
        translation_ptr, ctypes.POINTER(wintypes.WORD * 2)
    ).contents
    key = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{value_name}"
    value_ptr = ctypes.c_void_p()
    value_size = wintypes.UINT()
    if not query(data, key, ctypes.byref(value_ptr), ctypes.byref(value_size)):
        return None
    value = ctypes.wstring_at(value_ptr, max(0, value_size.value - 1)).strip()
    return value or None


def _windows_file_product_version(path: Path) -> str | None:
    return _windows_file_version_string(path, "ProductVersion")


def _windows_file_original_filename(path: Path) -> str | None:
    return _windows_file_version_string(path, "OriginalFilename")


def _windows_file_company_name(path: Path) -> str | None:
    return _windows_file_version_string(path, "CompanyName")


def _windows_microsoft_runtime_provenance(
    path: Path, packaged_name: str | None = None,
) -> NativeProvenance | None:
    """Identify Microsoft CRT files from PE metadata, never from basename alone."""
    if sys.platform != "win32":
        return None
    original_name = _windows_file_original_filename(path)
    company = _windows_file_company_name(path)
    version = _windows_file_product_version(path)
    allowed_original_names = {
        "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    }
    if (
        company != "Microsoft Corporation"
        or not original_name
        or original_name.casefold() not in allowed_original_names
        or not version
        or not re.fullmatch(r"\d+(?:\.\d+){2,3}", version)
    ):
        return None
    delivered_as = packaged_name or path.name
    rename_note = (
        f"; packaged basename {delivered_as!r} differs from PE OriginalFilename"
        if delivered_as.casefold() != original_name.casefold() else ""
    )
    return NativeProvenance(
        "Microsoft Visual C++ Runtime", version,
        "Microsoft Visual C++ Redistributable terms",
        "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
        f"PE CompanyName={company}; OriginalFilename={original_name}; "
        f"ProductVersion={version}; Microsoft Visual Studio REDIST scope "
        "permits unmodified files from VC\\redist and its subfolders subject "
        "to its license terms; source identity against a particular REDIST "
        "package was not verified"
        f"{rename_note}",
    )


def _windows_runtime_library_provenance(
    python_source: str,
) -> dict[str, NativeProvenance]:
    """Classify Windows runtime DLLs only after exact source-byte matching."""
    if sys.platform != "win32":
        return {}
    base_prefix = Path(sys.base_prefix)
    dlls_dir = base_prefix / "DLLs"
    candidates: list[Path] = []
    for directory in (base_prefix, dlls_dir):
        if directory.is_dir():
            for pattern in ("libffi-8.dll", "zlib1.dll", "msvcp140.dll",
                            "vcruntime140.dll", "vcruntime140_1.dll"):
                candidates.extend(path for path in directory.glob(pattern) if path.is_file())
    output: dict[str, NativeProvenance] = {}
    for path in sorted(set(candidates)):
        name = path.name.casefold()
        if name == "libffi-8.dll":
            provenance = NativeProvenance(
                "libffi", "Windows ABI 8; upstream release version not encoded",
                "MIT",
                "LICENSES/libffi-LICENSE.txt",
                "SHA-256 match to the selected CPython 3.12.14 Windows DLLs/libffi-8.dll; "
                "the PE image identifies the libffi-8 ABI but does not record the upstream release version",
            )
        elif name == "zlib1.dll":
            version = _windows_file_product_version(path)
            if version != "1.3.1":
                continue
            provenance = NativeProvenance(
                "zlib", version, "zlib License",
                "LICENSES/zlib-1.3.1-LICENSE.txt",
                "SHA-256 match to the selected CPython Windows zlib1.dll; "
                "PE ProductVersion resource reports zlib 1.3.1",
            )
        else:
            provenance = _windows_microsoft_runtime_provenance(path)
            if provenance is None:
                continue
        digest = sha256_file(path)
        provenance.evidence = (
            f"SHA-256 match to selected CPython Windows runtime {path.name}; "
            f"{provenance.evidence}; source SHA-256 {digest}"
        )
        output[digest] = provenance
    return output


def _debian_copyright_license(path: Path) -> str | None:
    """Extract applicable declared or explicitly identifiable license terms.

    Debian copyright files may use DEP-5 control fields or older upstream
    notice prose. The fallback labels only recognizable license language in
    the copyright text itself; it never derives a license from a package or
    library filename.
    """
    try:
        paragraphs = path.read_text(encoding="utf-8", errors="replace").split("\n\n")
    except OSError:
        return None
    file_licenses: list[str] = []
    for paragraph in paragraphs:
        fields: dict[str, list[str]] = {}
        current = None
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")) and current:
                fields[current][-1] += " " + line.strip()
            elif ":" in line:
                key, value = line.split(":", 1)
                current = key
                fields.setdefault(key, []).append(value.strip())
        if "Files" not in fields or "License" not in fields:
            continue
        patterns = " ".join(fields.get("Files", []))
        # DEP-5 uses the first token as the license short name; any following
        # continuation text is the license body, not part of the identifier.
        label = fields["License"][0].split()[0].strip()
        if label:
            declaration = f"Files {patterns or '(pattern omitted)'}: {label}"
            if declaration not in file_licenses:
                file_licenses.append(declaration)
    if file_licenses:
        return "Per-file license declarations in included Debian copyright record: " + "; ".join(file_licenses)

    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.casefold()
    if "x.org foundation" in lower and "slight variant of the common mit license" in lower:
        return "X.Org standard MIT-style notice plus source-specific variants; see included package copyright record"
    if "keith packard" in lower and "permission to use, copy, modify, distribute, and sell this software" in lower:
        return "Keith Packard permissive license terms plus any source-specific notices; see included package copyright record"
    if ("x consortium" in lower or "the open group" in lower or "xfree86 project" in lower) and (
        "permission to use, copy, modify, distribute, and sell" in lower
        or "permission is hereby granted, free of charge" in lower
    ):
        return "X Consortium/Open Group/XFree86 permissive notice(s); see included package copyright record for source-specific terms"
    if "libffi - copyright" in lower and "permission is hereby granted" in lower:
        return "libffi permissive license for the runtime library; documentation/packaging terms are also stated in the bundled record"
    if "bart massey" in lower and "xcb developers" in lower and "permission is hereby granted" in lower:
        return "XCB permissive license notice(s); see included package copyright record for source-specific terms"
    return None


def _dpkg_path_spellings(path: Path) -> list[Path]:
    """Return lexical, resolved, and usr-merged spellings for a host file."""
    spellings: list[Path] = [Path(os.path.abspath(path))]
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        resolved = None
    if resolved is not None:
        spellings.append(resolved)
    for candidate in tuple(spellings):
        rendered = str(candidate)
        if rendered.startswith("/usr/lib/"):
            spellings.append(Path("/lib/" + rendered[len("/usr/lib/"):]))
        elif rendered.startswith("/lib/"):
            spellings.append(Path("/usr/lib/" + rendered[len("/lib/"):]))
        if rendered.startswith("/usr/"):
            spellings.append(Path(rendered[len("/usr"):]))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in spellings:
        rendered = str(candidate)
        if rendered not in seen:
            seen.add(rendered)
            unique.append(candidate)
    return unique


def _dpkg_package_lookup(path: Path) -> tuple[dict[str, object] | None, list[dict[str, str]]]:
    """Find dpkg ownership and retain failed path queries for diagnostics."""
    try:
        attempts: list[dict[str, str]] = []
        for candidate in _dpkg_path_spellings(path):
            rendered = str(candidate)
            query = subprocess.run(
                ["dpkg-query", "-S", rendered], check=False,
                capture_output=True, text=True,
            )
            if query.returncode != 0:
                attempts.append({
                    "path": rendered,
                    "returncode": str(query.returncode),
                    "stderr": query.stderr.strip(),
                })
                continue
            for owner_line in query.stdout.splitlines():
                match = re.match(r"^([^\s:]+(?::[^\s:]+)?):\s+", owner_line)
                if not match:
                    continue
                package = match.group(1)
                base_package = package.split(":", 1)[0]
                version_query = subprocess.run(
                    # Keep the architecture from dpkg-query -S. Querying a
                    # multi-arch package by its unqualified name can return
                    # one version per installed architecture, concatenated
                    # into an invalid but nonempty inventory value.
                    ["dpkg-query", "-W", "-f=${Version}\\n", package],
                    check=False, capture_output=True, text=True,
                )
                version_lines = [
                    line.strip() for line in version_query.stdout.splitlines()
                    if line.strip()
                ]
                version = (
                    version_lines[0]
                    if version_query.returncode == 0 and len(version_lines) == 1
                    else ""
                )
                copyright_file = Path("/usr/share/doc") / base_package / "copyright"
                license_label = (
                    _debian_copyright_license(copyright_file)
                    if copyright_file.is_file() else None
                )
                return ({
                    "package": base_package,
                    "version": version or None,
                    "copyright_file": copyright_file if copyright_file.is_file() else None,
                    "license": license_label,
                    "owner_path": rendered,
                    "owner_line": owner_line,
                }, attempts)
        return None, attempts
    except (OSError, subprocess.SubprocessError):
        return None, []


def _dpkg_package_record(path: Path) -> dict[str, object] | None:
    """Find dpkg ownership using original and usr-merged path spellings."""
    record, _attempts = _dpkg_package_lookup(path)
    return record


def _dpkg_package_for_path(path: Path) -> tuple[str, str, Path, str] | None:
    """Resolve exact component/license evidence from dpkg plus copyright."""
    record = _dpkg_package_record(path)
    if not record:
        return None
    package, version = record["package"], record["version"]
    copyright_file, license_label = record["copyright_file"], record["license"]
    if not all((package, version, copyright_file, license_label)):
        return None
    return str(package), str(version), Path(copyright_file), str(license_label)


def _host_library_candidates() -> list[Path]:
    """Return system libraries visible through the dynamic loader cache.

    Keep both SONAME aliases and their resolved targets. Nuitka may copy a
    versioned target (for example ``libX11.so.6.4.0``) while ldconfig exposes
    only the SONAME alias (``libX11.so.6``); matching by the copied basename
    would miss valid package provenance.
    """
    try:
        result = subprocess.run(
            ["ldconfig", "-p"], check=False, capture_output=True, text=True
        )
    except OSError:
        return []
    candidates: set[Path] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+(/\S+)$", line)
        if match:
            candidate = Path(match.group(1))
            candidates.add(candidate)
            try:
                candidates.add(candidate.resolve(strict=True))
            except OSError:
                pass
    return sorted(candidates)


def _native_provenance_index(
    licenses_dir: Path,
    required_hashes: set[str],
    required_names: set[str] | None = None,
    runtime_names: set[str] | None = None,
    required_payloads: list[Path] | None = None,
    python_source: str = "",
) -> dict[str, NativeProvenance]:
    index = _python_provenance_index(runtime_names or set(), python_source)
    if os.name == "nt":
        return index
    # The actual deployment name can be the versioned ELF target while
    # ldconfig exposes a SONAME alias. Do not prefilter by basename: require
    # an exact content hash, then verify dpkg ownership and its copyright
    # metadata before assigning a package/license.
    host_candidates = _host_library_candidates()
    for candidate in host_candidates:
        try:
            digest = sha256_file(candidate)
        except OSError:
            continue
        if digest not in required_hashes or digest in index:
            continue
        dpkg = _dpkg_package_for_path(candidate)
        if not dpkg:
            continue
        package, version, copyright_file, license_label = dpkg
        output = licenses_dir / "Linux-packages" / f"{package}_{version}_copyright"
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file, output)
        index[digest] = NativeProvenance(
            f"Debian package {package}", version, license_label,
            output.relative_to(licenses_dir.parent).as_posix(),
            f"SHA-256 match to dpkg-owned {candidate.name}; dpkg-query reports {package} {version}",
        )
    # Nuitka may patch ELF dynamic metadata (for example, adding $ORIGIN to
    # RUNPATH), which changes the file hash while preserving the linker's GNU
    # build ID. In that case require an exact build-ID match to a host file
    # owned by dpkg and include that package's copyright record. A filename or
    # SONAME match alone is never sufficient.
    unresolved_payloads = [
        path for path in (required_payloads or [])
        if sha256_file(path) in required_hashes
        and sha256_file(path) not in index
    ]
    wanted_build_ids = {
        build_id for path in unresolved_payloads
        if (build_id := elf_build_id(path)) is not None
    }
    if wanted_build_ids:
        candidates_by_build_id: dict[str, list[Path]] = {}
        for candidate in host_candidates:
            build_id = elf_build_id(candidate)
            if build_id in wanted_build_ids:
                candidates_by_build_id.setdefault(build_id, []).append(candidate)
        for payload in unresolved_payloads:
            digest = sha256_file(payload)
            if digest in index:
                continue
            build_id = elf_build_id(payload)
            if not build_id:
                continue
            for candidate in candidates_by_build_id.get(build_id, []):
                dpkg = _dpkg_package_for_path(candidate)
                if not dpkg:
                    continue
                package, version, copyright_file, license_label = dpkg
                output = licenses_dir / "Linux-packages" / f"{package}_{version}_copyright"
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copyright_file, output)
                index[digest] = NativeProvenance(
                    f"Debian package {package}", version, license_label,
                    output.relative_to(licenses_dir.parent).as_posix(),
                    f"ELF build ID {build_id} matches dpkg-owned {candidate.name}; dpkg-query reports {package} {version}",
                )
                break
    return index


def _python_provenance_paths(runtime_names: set[str]) -> dict[str, NativeProvenance]:
    """Index native source paths from wheel RECORDs and CPython's stdlib."""
    paths: dict[str, NativeProvenance] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if not name or canonicalize_name(name) not in runtime_names:
            continue
        provenance = NativeProvenance(
            f"Python distribution {name}", dist.version,
            _license_for_distribution(dist), f"LICENSES/Python/{name}-{dist.version}/",
            f"Nuitka report source resolves to installed {name} {dist.version} distribution metadata",
        )
        for record in dist.files or ():
            if not _looks_native(Path(str(record))):
                continue
            try:
                paths[str(Path(dist.locate_file(record)).resolve())] = provenance
            except OSError:
                continue
    shared_dir_value = sysconfig.get_config_var("DESTSHARED")
    shared_dir = Path(shared_dir_value) if shared_dir_value else None
    if shared_dir and shared_dir.is_dir():
        provenance = NativeProvenance(
            "CPython runtime", sys.version.split()[0], "PSF-2.0",
            "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
            f"Nuitka report source resolves to CPython {sys.version.split()[0]} standard-library extension directory",
        )
        for source in shared_dir.iterdir():
            if _looks_native(source):
                try:
                    paths[str(source.resolve())] = provenance
                except OSError:
                    continue
    windows_runtime_sources = _windows_cpython_native_sources()
    if windows_runtime_sources:
        provenance = NativeProvenance(
            "CPython runtime", sys.version.split()[0], "PSF-2.0",
            "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
            f"Nuitka report source resolves to CPython {sys.version.split()[0]} Windows runtime files",
        )
        for source in windows_runtime_sources:
            try:
                paths[str(source.resolve())] = provenance
            except OSError:
                continue
    if ssl.OPENSSL_VERSION.startswith("OpenSSL 3.") and os.name != "nt":
        try:
            import _ssl
            linked = subprocess.run(
                ["ldd", str(Path(_ssl.__file__))], check=False,
                capture_output=True, text=True,
            ).stdout
            for line in linked.splitlines():
                if "libssl.so" not in line and "libcrypto.so" not in line:
                    continue
                match = re.search(r"=>\s+(/\S+)", line)
                if match:
                    source = Path(match.group(1)).resolve()
                    paths[str(source)] = NativeProvenance(
                        "OpenSSL", ssl.OPENSSL_VERSION, "Apache-2.0",
                        "LICENSES/OpenSSL-Apache-2.0.txt",
                        f"Nuitka report source resolves to {source.name} linked by CPython _ssl",
                    )
        except (OSError, subprocess.SubprocessError):
            pass
    elif ssl.OPENSSL_VERSION.startswith("OpenSSL 3.") and os.name == "nt":
        openssl = NativeProvenance(
            "OpenSSL", ssl.OPENSSL_VERSION, "Apache-2.0",
            "LICENSES/OpenSSL-Apache-2.0.txt",
            "Nuitka report source resolves to OpenSSL DLL shipped with selected CPython; version reported by CPython _ssl",
        )
        for base in {Path(sys.base_prefix), Path(sys.prefix)}:
            for pattern in ("libssl*.dll", "libcrypto*.dll", "ssl*.dll", "crypto*.dll"):
                for source in base.rglob(pattern):
                    try:
                        paths[str(source.resolve())] = openssl
                    except OSError:
                        continue
    return paths


def _expand_nuitka_source_path(value: str) -> Path | None:
    value = value.replace("\\", os.sep)
    # Nuitka 2.x reports sys.real_prefix for some virtualenv/pyenv builds.
    # In a venv this is the same interpreter installation represented by
    # sys.base_prefix; resolve it before the shorter ${sys.prefix} token.
    value = value.replace("${sys.real_prefix}", sys.base_prefix)
    value = value.replace("${sys.base_prefix}", sys.base_prefix)
    value = value.replace("${sys.prefix}", sys.prefix)
    value = value.replace("${cwd}", str(ROOT))
    candidate = Path(value)
    if not candidate.is_absolute() or not candidate.is_file():
        return None
    # Keep Nuitka's lexical /lib spelling. On usr-merged Debian systems,
    # resolving this path can produce /usr/lib even though dpkg records /lib.
    return Path(os.path.abspath(candidate))


def _nuitka_report_provenance(
    report: Path | None, licenses_dir: Path, runtime_names: set[str]
) -> dict[str, NativeProvenance]:
    """Resolve Nuitka's packaged destination names back to actual source files."""
    if report is None:
        return {}
    path_provenance = _python_provenance_paths(runtime_names)
    output: dict[str, NativeProvenance] = {}
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"Could not read Nuitka compilation report: {exc}") from exc
    source_matches: dict[str, NativeProvenance] = {}
    for element in root.iter():
        source_text = element.attrib.get("source_path")
        if not source_text:
            continue
        source_path = _expand_nuitka_source_path(source_text)
        provenance = None
        if source_path:
            provenance = path_provenance.get(str(source_path))
            if provenance is None:
                try:
                    provenance = path_provenance.get(str(source_path.resolve(strict=True)))
                except OSError:
                    pass
        if provenance is None and source_path:
            dpkg = _dpkg_package_record(source_path)
            if dpkg and all(dpkg.get(key) for key in ("package", "version", "copyright_file", "license")):
                package = str(dpkg["package"])
                version = str(dpkg["version"])
                copyright_file = Path(dpkg["copyright_file"])
                license_label = str(dpkg["license"])
                notice = licenses_dir / "Linux-packages" / f"{package}_{version}_copyright"
                notice.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copyright_file, notice)
                provenance = NativeProvenance(
                    f"Debian package {package}", version, license_label,
                    notice.relative_to(licenses_dir.parent).as_posix(),
                    f"Nuitka report source matched dpkg-owned {source_path.name}; dpkg-query reports {package} {version}",
                )
        if not provenance:
            continue
        if element.tag == "module" and "extension" in element.attrib.get("kind", "").lower():
            source_matches[element.attrib.get("name", "").replace(".", "/")] = provenance
        destination = element.attrib.get("dest_path")
        if not destination:
            continue
        destination = destination.replace("\\", "/").removeprefix("./")
        prior = output.get(destination)
        if prior and (
            prior.component, prior.version, prior.license, prior.notice
        ) != (
            provenance.component, provenance.version, provenance.license, provenance.notice
        ):
            output[destination] = NativeProvenance(
                "UNRESOLVED ambiguous Nuitka source", "UNRESOLVED", "UNRESOLVED",
                "UNRESOLVED", "Nuitka report maps this destination to conflicting source components",
            )
        else:
            output[destination] = provenance
    # Nuitka's module records identify compiled extension modules by dotted
    # import name and exact source path. Match that module path against the
    # standalone tree's same relative package path and a native suffix.
    for module_path, provenance in source_matches.items():
        if module_path:
            output[f"module:{module_path}"] = provenance
    return output


def native_component_rows(
    release_dir: Path,
    runtime: list[tuple[str, str, str]],
    python_source: str,
    openssl_version: str,
    openssl_provenance: str,
    tcl_version: str,
    tk_version: str,
    licenses_dir: Path,
    project_version: str,
    nuitka_report: Path | None = None,
    msvc_redist_manifest: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Account for each native payload using hash-backed provenance evidence."""
    runtime_names = {canonicalize_name(name) for name, _version, _license in runtime}
    rows = []
    unresolved = []
    payloads = native_payload_files(release_dir)
    hashes = {sha256_file(path) for path in payloads}
    report_index = _nuitka_report_provenance(nuitka_report, licenses_dir, runtime_names)
    source_index = _native_provenance_index(
        licenses_dir, hashes, {path.name for path in payloads}, runtime_names,
        payloads, python_source,
    )
    redist_index: dict[str, dict[str, str]] = {}
    numpy_patched_modules: dict[str, dict[str, str]] = {}
    if msvc_redist_manifest is not None:
        try:
            manifest = json.loads(msvc_redist_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read Visual Studio REDIST manifest: {exc}") from exc
        required_redist_files = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
        if not isinstance(manifest, dict) or set(manifest) != {
            "redist_files", "numpy_patched_modules",
        }:
            raise RuntimeError("Visual Studio REDIST manifest has an unexpected structure")
        redist_index = manifest["redist_files"]
        module_entries = manifest["numpy_patched_modules"]
        if not isinstance(redist_index, dict) or set(redist_index) != required_redist_files:
            raise RuntimeError("Visual Studio REDIST manifest has an unexpected file set")
        if not isinstance(module_entries, list):
            raise RuntimeError("Visual Studio REDIST manifest has invalid NumPy module evidence")
        for item in module_entries:
            if not isinstance(item, dict):
                raise RuntimeError("Visual Studio REDIST manifest has invalid NumPy module entry")
            archive_path = item.get("archive_path")
            record_entry = item.get("record_entry")
            archive_posix = PurePosixPath(archive_path) if isinstance(archive_path, str) else None
            record_posix = PurePosixPath(record_entry) if isinstance(record_entry, str) else None
            if (
                not isinstance(archive_path, str)
                or not isinstance(record_entry, str)
                or "\\" in archive_path
                or "\\" in record_entry
                or archive_posix.is_absolute()
                or record_posix.is_absolute()
                or archive_posix.as_posix() != archive_path
                or record_posix.as_posix() != record_entry
                or ".." in archive_posix.parts
                or ".." in record_posix.parts
                or not archive_path.startswith("numpy/")
                or not record_entry.startswith("numpy/")
                or not archive_path.casefold().endswith(".pyd")
                or not record_entry.casefold().endswith(".pyd")
                or archive_posix.parent != record_posix.parent
                or not record_posix.stem.casefold().startswith(archive_posix.stem.casefold())
                or archive_path in numpy_patched_modules
                or item.get("distribution") != "numpy"
                or not re.fullmatch(
                    r"msvcp140-[0-9a-f]{32}\.dll",
                    str(item.get("old_import", "")), re.I,
                )
                or item.get("new_import", "").casefold() != "msvcp140.dll"
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("source_sha256", "")), re.I)
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("patched_sha256", "")), re.I)
                or item.get("source_sha256", "").casefold()
                    == item.get("patched_sha256", "").casefold()
            ):
                raise RuntimeError(f"Invalid NumPy import-patch provenance for {archive_path!r}")
            numpy_patched_modules[archive_path] = item
    source_rpm = release_dir / "SOURCE" / LIBQUADMATH_SOURCE_NAME
    source_rpm_digest = sha256_file(source_rpm) if source_rpm.is_file() else None
    for path in payloads:
        relative = path.relative_to(release_dir).as_posix()
        digest = sha256_file(path)
        name = path.name.lower()
        if name in {"milas.bin", "milas.exe"}:
            provenance = NativeProvenance(
                "MiLaS standalone executable", project_version,
                "PolyForm Noncommercial License 1.0.0", "LICENSE; NOTICE",
                "Project release executable produced by this build's Nuitka command",
            )
        elif relative in numpy_patched_modules:
            patch = numpy_patched_modules[relative]
            source_hash = str(patch["source_sha256"]).casefold()
            patched_hash = str(patch["patched_sha256"]).casefold()
            wheel_source = source_index.get(source_hash)
            record_path = patch["record_entry"]
            if (
                digest == patched_hash
                and wheel_source is not None
                and wheel_source.component == "Python distribution numpy"
                and wheel_source.version == patch.get("version")
                and record_path in wheel_source.evidence
                and patch.get("old_import", "").casefold().startswith("msvcp140-")
                and patch.get("old_import", "").casefold().endswith(".dll")
                and patch.get("new_import", "").casefold() == "msvcp140.dll"
            ):
                provenance = NativeProvenance(
                    "Python distribution numpy", wheel_source.version,
                    wheel_source.license, wheel_source.notice,
                    f"NumPy import table changed from {patch['old_import']} to msvcp140.dll; "
                    f"original bytes match NumPy {patch['version']} RECORD entry {record_path} "
                    f"with source SHA-256 {source_hash}; patched payload SHA-256 {digest}; "
                    "verified by packaging/prepare_windows_crt.py",
                )
            else:
                provenance = None
        elif (
            relative.casefold() in {
                "numpy.libs/msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
            }
            and relative.rsplit("/", 1)[-1].casefold() in redist_index
        ):
            basename = relative.rsplit("/", 1)[-1].casefold()
            manifest_entry = redist_index[basename]
            expected_hash = manifest_entry.get("sha256")
            staged_hash = manifest_entry.get("staged_sha256")
            source_label = manifest_entry.get("redist_source")
            version = manifest_entry.get("product_version")
            original_name = _windows_file_original_filename(path)
            company = _windows_file_company_name(path)
            if (
                expected_hash != digest or staged_hash != digest
                or not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash), re.I)
                or not source_label
                or version != _windows_file_product_version(path)
                or original_name != basename
                or company != "Microsoft Corporation"
            ):
                provenance = None
            else:
                provenance = NativeProvenance(
                    "Microsoft Visual C++ Runtime", version,
                    "Microsoft Visual C++ Redistributable terms",
                    "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
                    f"Unmodified file copied directly from {source_label}; "
                    f"PE CompanyName={company}; OriginalFilename={original_name}; "
                    f"ProductVersion={version}; source SHA-256 {expected_hash}; "
                    f"staged payload SHA-256 {digest}",
                )
        elif os.name != "nt" and elf_build_id(path) == LIBGFORTRAN_BUILD_ID:
            if (
                digest == LIBGFORTRAN_BINARY_SHA256
                and source_rpm_digest == LIBGFORTRAN_SOURCE_SHA256
                and (licenses_dir / "GPL-3.0.txt").is_file()
                and sha256_file(licenses_dir / "GPL-3.0.txt") == LIBGFORTRAN_GPL_SHA256
                and (licenses_dir / "GCC-Runtime-Library-Exception-3.1.txt").is_file()
                and sha256_file(licenses_dir / "GCC-Runtime-Library-Exception-3.1.txt") == LIBGFORTRAN_EXCEPTION_SHA256
                and (release_dir / "SOURCE" / "LIBGFORTRAN-SOURCE.md").is_file()
            ):
                provenance = NativeProvenance(
                    "GCC libgfortran (AlmaLinux GCC source RPM)",
                    "8.5.0-28.el8_10.alma.1 (GCC 8.5.0; ABI 5)",
                    "GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1",
                    "LICENSES/GPL-3.0.txt; LICENSES/GCC-Runtime-Library-Exception-3.1.txt; "
                    f"SOURCE/{LIBGFORTRAN_SOURCE_NAME}; SOURCE/LIBGFORTRAN-SOURCE.md",
                    f"ELF build ID {LIBGFORTRAN_BUILD_ID}; staged binary SHA-256 {digest}; "
                    f"GCC source RPM SHA-256 {source_rpm_digest}; source tar `gcc-8.5.0-20210514.tar.xz`, "
                    "`libgfortran/runtime/error.c`, `COPYING3` and `COPYING.RUNTIME`",
                )
            else:
                provenance = None
        elif os.name != "nt" and "libgfortran" in name:
            # Never attribute an unknown GCC Fortran runtime to NumPy's wheel.
            provenance = None
        elif os.name != "nt" and elf_build_id(path) == LIBQUADMATH_BUILD_ID:
            if (
                source_rpm_digest == LIBQUADMATH_SOURCE_SHA256
                and (licenses_dir / "GNU-LGPL-2.1.txt").is_file()
                and (release_dir / "SOURCE" / "LIBQUADMATH-SOURCE.md").is_file()
            ):
                provenance = NativeProvenance(
                    "GCC libquadmath (AlmaLinux runtime RPM)",
                    "8.5.0-28.el8_10.alma.1 (GCC 8.5.0)",
                    "LGPL-2.1-or-later",
                    "LICENSES/GNU-LGPL-2.1.txt; "
                    f"SOURCE/{LIBQUADMATH_SOURCE_NAME}; "
                    "SOURCE/LIBQUADMATH-SOURCE.md",
                    f"ELF build ID {LIBQUADMATH_BUILD_ID}; staged binary SHA-256 {digest}; "
                    f"matches verified AlmaLinux binary RPM payload SHA-256 "
                    f"{LIBQUADMATH_ORIGINAL_BINARY_SHA256}; binary RPM SHA-256 "
                    f"{LIBQUADMATH_BINARY_RPM_SHA256}; source RPM SHA-256 "
                    f"{source_rpm_digest}",
                )
            else:
                provenance = None
        elif os.name != "nt" and "libquadmath" in name:
            # Similar names are not proof of the verified GCC build. Keep a
            # changed or unknown build unresolved instead of assigning NumPy's
            # aggregate wheel license to it.
            provenance = None
        elif re.match(r"^(?:lib)?(?:ssl|crypto)(?:[-.]|$)", name):
            source_evidence = report_index.get(relative) or source_index.get(digest)
            if source_evidence and openssl_version.startswith("OpenSSL 3."):
                provenance = NativeProvenance(
                    "OpenSSL", openssl_version,
                    "Apache-2.0", "LICENSES/OpenSSL-Apache-2.0.txt",
                    f"{source_evidence.evidence}; build interpreter reports {openssl_version}; {openssl_provenance}",
                )
            else:
                provenance = None
        elif re.search(r"(?:^|/)(?:tcl|tk)[0-9.]+/", relative.lower()) or re.match(r"^(?:lib)?(?:tcl|tk)[^/]*\.(?:dll|so(?:\..*)?)$", name):
            provenance = NativeProvenance(
                "Tcl/Tk", f"Tcl {tcl_version}; Tk {tk_version}",
                "Tcl and Tk separate license terms",
                "LICENSES/Tcl-license.terms; LICENSES/Tk-license.terms",
                "Nuitka tk-inter payload; versions reported by this build interpreter",
            )
        elif re.fullmatch(r"numpy\.libs/libscipy_openblas64_.*\.dll", relative, re.I):
            wheel_source = source_index.get(digest)
            numpy_version = next(
                (version for name_, version, _license in runtime
                 if canonicalize_name(name_) == "numpy"),
                "unknown",
            )
            if (
                wheel_source
                and wheel_source.component == "Python distribution numpy"
                and (licenses_dir / "OpenBLAS-BSD-3-Clause.txt").is_file()
                and (licenses_dir / "OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt").is_file()
            ):
                provenance = NativeProvenance(
                    "OpenBLAS with LAPACK bundled in the NumPy wheel",
                    f"NumPy {numpy_version} wheel; OpenBLAS release version not verified",
                    "BSD-3-Clause AND BSD-3-Clause-Attribution",
                    "LICENSES/OpenBLAS-BSD-3-Clause.txt; "
                    "LICENSES/OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt",
                    f"{wheel_source.evidence}; SHA-256 match to the installed NumPy wheel's "
                    "numpy.libs/libscipy_openblas64_*.dll; NumPy's Windows wheel notice "
                    "identifies OpenBLAS under BSD-3-Clause and bundled LAPACK under "
                    "BSD-3-Clause-Attribution",
                )
            else:
                provenance = None
        elif re.fullmatch(
            r"kiwisolver/_cext(?:\.[^/]+)?(?:\.pyd|\.so(?:\..*)?)",
            relative, re.I,
        ):
            locked_version = next(
                (version for name_, version, _license in runtime
                 if canonicalize_name(name_) == "kiwisolver"),
                None,
            )
            hash_source = source_index.get(digest)
            report_source = report_index.get(relative)
            wheel_source = None
            if (
                hash_source is not None
                and hash_source.component == "Python distribution kiwisolver"
                and hash_source.version == locked_version
            ):
                wheel_source = hash_source
            elif (
                report_source is not None
                and report_source.component == "Python distribution kiwisolver"
                and report_source.version == locked_version
                and "Nuitka report source resolves to installed kiwisolver"
                in report_source.evidence
            ):
                wheel_source = report_source
            license_file = (
                licenses_dir / "Python" / f"kiwisolver-{wheel_source.version}" / "LICENSE"
                if wheel_source else licenses_dir / "Python" / "kiwisolver-unknown" / "LICENSE"
            )
            if (
                wheel_source
                and wheel_source.component == "Python distribution kiwisolver"
                and license_file.is_file()
            ):
                provenance = NativeProvenance(
                    "Python distribution kiwisolver", wheel_source.version,
                    "BSD-3-Clause",
                    f"LICENSES/Python/kiwisolver-{wheel_source.version}/LICENSE",
                    f"{wheel_source.evidence}; actual archive payload `{relative}`; "
                    "applicable three-clause BSD terms are in the referenced LICENSE file",
                )
            else:
                provenance = None
        else:
            provenance = report_index.get(relative)
            if provenance is None:
                module_matches = []
                target = Path(relative)
                for key, candidate in report_index.items():
                    if not key.startswith("module:"):
                        continue
                    module_path = Path(key.removeprefix("module:"))
                    # Nuitka can place CPython extension modules in a
                    # standalone subdirectory even when their source import
                    # name is top-level (_ssl, _hashlib, _tkinter, etc.). The
                    # basename association is safe here because the module
                    # name is already tied to a verified source path by the
                    # Nuitka report; it is not used as license provenance.
                    if (
                        (target.name == module_path.name or target.name.startswith(module_path.name + "."))
                        and (target.name.endswith((".so", ".pyd", ".dll")) or ".so." in target.name)
                    ):
                        module_matches.append(candidate)
                if len(module_matches) == 1:
                    provenance = module_matches[0]
            provenance = provenance or source_index.get(digest)
            if provenance and provenance.component.startswith("UNRESOLVED"):
                provenance = None
        if provenance is None:
            unresolved.append(relative)
            provenance = NativeProvenance(
                "UNRESOLVED native payload", "UNRESOLVED", "UNRESOLVED",
                "UNRESOLVED", "No verified hash match in wheel RECORD, CPython runtime, or dpkg package database",
            )
        linkage = _elf_linkage_evidence(path, release_dir)
        evidence = "; ".join(part for part in (provenance.evidence, linkage) if part)
        # Absolute source paths never enter the generated evidence; only the
        # relative release path and path-free provenance description are kept.
        # Evidence is derived from local build paths in Nuitka/dpkg output.
        # Strip any absolute path from every emitted cell as a final privacy
        # boundary, not only from the summary fields above.
        cells = tuple(_path_free_text(cell) for cell in (
            f"`{relative}`", provenance.component, provenance.version,
            provenance.license, provenance.notice, evidence,
        ))
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return rows, unresolved


def stage(
    dist_dir: Path,
    release_dir: Path,
    python_source: str = "Build interpreter used by platform build script",
    nuitka_report: Path | None = None,
    msvc_redist_manifest: Path | None = None,
) -> None:
    if not dist_dir.is_dir() or not release_dir.is_dir():
        raise RuntimeError("Expected deployment and release directories to exist")
    platform_name = "Windows" if os.name == "nt" else "Linux"
    if platform_name == "Windows" and msvc_redist_manifest is None:
        raise RuntimeError(
            "Windows staging requires a verified Visual Studio REDIST manifest; "
            "run packaging/prepare_windows_crt.py before staging."
        )
    if nuitka_report is not None and not nuitka_report.is_file():
        raise RuntimeError(f"Nuitka compilation report is missing: {nuitka_report.name}")
    licenses_dir = release_dir / "LICENSES"
    if licenses_dir.exists():
        shutil.rmtree(licenses_dir)
    licenses_dir.mkdir()
    copy_static_license_files(licenses_dir, platform_name)
    stage_platform_documentation(release_dir, platform_name)
    openssl_version = ssl.OPENSSL_VERSION
    openssl_files = find_openssl_payloads(dist_dir, release_dir)
    if not openssl_files:
        raise RuntimeError("No bundled OpenSSL shared-library files were found in this build")
    if not openssl_version.startswith("OpenSSL 3."):
        raise RuntimeError(
            f"Expected OpenSSL 3.x (Apache-2.0) in this build interpreter, found {openssl_version!r}; review before staging."
        )
    if not (licenses_dir / "OpenSSL-Apache-2.0.txt").is_file():
        raise RuntimeError("OpenSSL 3.x requires LICENSES/OpenSSL-Apache-2.0.txt")
    required_tk_licenses = (
        licenses_dir / "Tcl-license.terms",
        licenses_dir / "Tk-license.terms",
    )
    missing_tk_licenses = [path.name for path in required_tk_licenses if not path.is_file()]
    if missing_tk_licenses:
        raise RuntimeError(
            "Missing separate Tcl/Tk license texts: "
            + ", ".join(missing_tk_licenses)
        )

    runtime = lock_runtime_distributions()
    copied = copy_dist_license_files(
        licenses_dir, {name for name, _, _ in runtime}
    )
    verify_staged_python_license_files(licenses_dir, copied)

    libquadmath_files = (
        stage_libquadmath_source(release_dir, licenses_dir)
        if platform_name == "Linux" else []
    )
    libgfortran_files = (
        stage_libgfortran_source(release_dir, licenses_dir)
        if platform_name == "Linux" else []
    )

    tk_files = find_tk_payloads(dist_dir, release_dir)
    if not tk_files:
        raise RuntimeError(
            f"{platform_name} standalone build has no detectable bundled Tcl/Tk runtime. "
            "Verify Nuitka's tk-inter plugin and do not stage a partial package."
        )
    tcl_version, tk_version = tkinter_runtime_versions()
    (licenses_dir / "Tcl-Tk-runtime-files.txt").write_text(
        f"Tcl runtime patch level: {tcl_version}\n"
        f"Tk runtime version reported by tkinter: {tk_version}\n\n"
        "Detected Tcl/Tk runtime payloads:\n\n"
        + "\n".join(tk_files)
        + "\n\nLicenses: Tcl and Tk have separate license terms. See `Tcl-license.terms`, `Tk-license.terms`, and the source URLs in `THIRD_PARTY_NOTICES.md`.\n",
        encoding="utf-8",
    )
    table = [
        "# Bundled runtime inventory",
        "",
        f"Generated for {platform_name} from the active uv.lock runtime dependency closure, the platform build interpreter, and this build's deployment/release directories.",
        f"Build Python source: {_path_free_text(python_source)}.",
        "Nuitka compilation report: captured for this build and kept outside the release archive because it can contain local paths." if nuitka_report else "Nuitka compilation report: not supplied.",
        "The distribution list records exact installed versions and their supplied license metadata. License texts are copied under LICENSES/Python/.",
        "",
        "| Distribution | Version | Package metadata license |",
        "| --- | --- | --- |",
    ]
    for name, version, license_text in runtime:
        table.append(f"| {name} | {version} | {license_text.replace('|', '\\|')} |")
    native_payload = native_payload_files(release_dir)
    openssl_provenance = (
        linux_openssl_provenance(dist_dir)
        if platform_name == "Linux"
        else f"Bundled by {python_source}; the build interpreter reports {openssl_version}."
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(project.get("project", {}).get("version", "UNRESOLVED"))
    native_rows, unresolved = native_component_rows(
        release_dir, runtime, python_source, openssl_version, openssl_provenance,
        tcl_version, tk_version, licenses_dir, project_version, nuitka_report,
        msvc_redist_manifest,
    )
    table += [
        "",
        "## OpenSSL runtime",
        "",
        f"Version reported by the build interpreter: {openssl_version}.",
        f"Source/provenance: {openssl_provenance}",
        "Applicable license: Apache License 2.0 for OpenSSL 3.x. The complete text is included as `LICENSES/OpenSSL-Apache-2.0.txt`; see the OpenSSL entry in `THIRD_PARTY_NOTICES.md`.",
        "Detected OpenSSL library files in this package:",
        *[f"- `{path}`" for path in openssl_files],
        "",
        "## CPython runtime",
        "",
        f"Version: {sys.version.split()[0]}.",
        f"Source: {_path_free_text(python_source)}.",
        "License: Python Software Foundation License 2.0; see `LICENSES/Python-PSF-2.0-CPython-3.12.14.txt`.",
        "",
        "",
        "## Tcl/Tk runtime",
        "",
        f"Build interpreter reported Tcl {tcl_version} and Tk {tk_version}.",
        "The exact detected standalone payload is listed in `LICENSES/Tcl-Tk-runtime-files.txt`; separate Tcl and Tk license texts are included in `LICENSES/`.",
        "",
        "## Native payload inventory",
        "",
        "Each row is traced through Nuitka report source/destination records, installed Python distribution RECORD metadata, the selected CPython runtime, dpkg ownership/copyright metadata, or a documented build input; hashes provide an independent fallback match. ELF extension rows also list dynamic dependencies and identify which are bundled separately in this inventory. GCC libquadmath and libgfortran rows are matched by their exact ELF build IDs and checked against the pinned AlmaLinux/GCC source RPM records; neither is classified under NumPy's aggregate wheel license. Unresolved rows intentionally fail final archive validation.",
        "",
        "| Archive-relative file | Component | Version | Applicable license | Included license text / notice | Provenance evidence |",
        "| --- | --- | --- | --- | --- | --- |",
        *native_rows,
        "",
        f"Unresolved native payload count: {len(unresolved)}.",
        *(f"- `{item}`" for item in unresolved),
        "",
    ]
    inventory_text = "\n".join(_path_free_text(line) for line in table)
    (release_dir / "BUNDLED_RUNTIME_INVENTORY.md").write_text(
        inventory_text, encoding="utf-8"
    )
    print(f"Staged {len(copied)} Python license files, {len(tk_files)} Tcl/Tk runtime files, {len(libquadmath_files)} verified libquadmath and {len(libgfortran_files)} verified libgfortran source mapping(s), and {len(native_payload)} native payload entries ({len(unresolved)} unresolved)")
    print(f"Staged OpenSSL {openssl_version} under Apache-2.0 ({len(openssl_files)} detected files)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--python-source", default="Build interpreter used by platform build script")
    parser.add_argument("--nuitka-report", type=Path)
    parser.add_argument("--msvc-redist-manifest", type=Path)
    args = parser.parse_args()
    stage(
        args.dist_dir.resolve(), args.release_dir.resolve(), args.python_source,
        args.nuitka_report.resolve() if args.nuitka_report else None,
        args.msvc_redist_manifest.resolve() if args.msvc_redist_manifest else None,
    )


if __name__ == "__main__":
    main()
