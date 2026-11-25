#!/usr/bin/env python3
"""Validate release evidence and reconcile inventory against archive payloads."""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = (
    "BUNDLED_RUNTIME_INVENTORY.md",
    "LICENSES/Tcl-Tk-runtime-files.txt",
    "LICENSES/Tcl-license.terms",
    "LICENSES/Tk-license.terms",
    "LICENSES/OpenSSL-Apache-2.0.txt",
)
INVENTORY_HEADER = "| Archive-relative file | Component | Version | Applicable license | Included license text / notice | Provenance evidence |"
ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])/(?!/)[^\s|;,)]{2,}|(?<![A-Za-z0-9])[A-Za-z]:\\[^\s|;,)]*"
)
LIBQUADMATH_BUILD_ID = "c1c5f8d61f17e5579c4e85718845b74e4b94116c"
LIBQUADMATH_SOURCE = "SOURCE/gcc-8.5.0-28.el8_10.alma.1.src.rpm"
LIBQUADMATH_SOURCE_SHA256 = "c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0"
LIBQUADMATH_BINARY_RPM_SHA256 = "e50ce3215fc8c5fcabb60664fe4933dc84db9f7b27d304e3168fafacb6016a9e"
LIBQUADMATH_ORIGINAL_BINARY_SHA256 = "2284e5833f5ea62b141499d32373fc207878c1778fe004b4867bc41d75086be2"
LIBQUADMATH_LICENSE = "LICENSES/GNU-LGPL-2.1.txt"
LIBQUADMATH_LICENSE_SHA256 = "32434afcc8666ba060e111d715bfdb6c2d5dd8a35fa4d3ab8ad67d8f850d2f2b"
LIBQUADMATH_MANIFEST = "SOURCE/LIBQUADMATH-SOURCE.md"
LIBGFORTRAN_BUILD_ID = "04dbe32dceb80654422140fb7641f732065999c8"
LIBGFORTRAN_BINARY_SHA256 = "a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160"
LIBGFORTRAN_SOURCE = LIBQUADMATH_SOURCE
LIBGFORTRAN_SOURCE_SHA256 = LIBQUADMATH_SOURCE_SHA256
LIBGFORTRAN_GPL = "LICENSES/GPL-3.0.txt"
LIBGFORTRAN_GPL_SHA256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
LIBGFORTRAN_EXCEPTION = "LICENSES/GCC-Runtime-Library-Exception-3.1.txt"
LIBGFORTRAN_EXCEPTION_SHA256 = "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74"
LIBGFORTRAN_MANIFEST = "SOURCE/LIBGFORTRAN-SOURCE.md"
LGPL_ADDITIONAL_PERMISSION = "LGPL-2.1-Section-6-Additional-Permission.md"
WINDOWS_OPENBLAS_LICENSE = "LICENSES/OpenBLAS-BSD-3-Clause.txt"
WINDOWS_LAPACK_LICENSE = "LICENSES/OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt"
WINDOWS_MSVCRT_TERMS = "LICENSES/Microsoft-Visual-C-Redistributable-terms.md"


def normalized_member(name: str) -> str:
    return name.replace("\\", "/")


def archive_members(archive: Path) -> set[str]:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            return {normalized_member(item.filename) for item in handle.infolist()}
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as handle:
            return {normalized_member(item.name) for item in handle.getmembers()}
    raise ValueError(f"Unsupported or unreadable release archive: {archive}")


def _zip_member_name(handle: zipfile.ZipFile, normalized_name: str) -> str:
    """Resolve slash-normalized archive paths to their stored ZIP names."""
    wanted = normalized_member(normalized_name)
    for item in handle.infolist():
        if normalized_member(item.filename) == wanted:
            return item.filename
    raise RuntimeError(f"Required archive file is missing: {normalized_name}")


def _native_signature(prefix: bytes, name: str = "") -> bool:
    lower = name.lower()
    return (
        prefix.startswith(b"\x7fELF") or prefix.startswith(b"MZ")
        or lower.endswith((".dll", ".pyd", ".exe", ".so")) or ".so." in lower
    )


def _inventory_rows(text: str) -> list[tuple[str, ...]]:
    lines = text.splitlines()
    try:
        header = lines.index(INVENTORY_HEADER)
    except ValueError as exc:
        raise RuntimeError("Runtime inventory is missing the native payload table header") from exc
    rows = []
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if len(cells) != 6:
            raise RuntimeError(f"Malformed native inventory row: {line}")
        path = cells[0].strip("`")
        rows.append((path, *cells[1:]))
    return rows


def _notice_references(value: str) -> list[str]:
    return [part.strip().strip("`") for part in value.split(";") if part.strip()]


def _archive_sha256(archive: Path, member_name: str) -> str:
    digest = hashlib.sha256()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            stored_name = _zip_member_name(handle, member_name)
            with handle.open(stored_name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    else:
        with tarfile.open(archive, "r:*") as handle:
            stream = handle.extractfile(member_name)
            if stream is None:
                raise RuntimeError(f"Required archive file is missing: {member_name}")
            with stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _archive_read(archive: Path, member_name: str) -> bytes:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            return handle.read(_zip_member_name(handle, member_name))
    with tarfile.open(archive, "r:*") as handle:
        stream = handle.extractfile(member_name)
        if stream is None:
            raise RuntimeError(f"Required archive file is missing: {member_name}")
        return stream.read()


def _validate_windows_native_licenses(
    archive: Path,
    root: str,
    actual_native: set[str],
    rows: list[tuple[str, ...]],
    members: set[str],
    errors: list[str],
) -> None:
    """Enforce the additional NumPy wheel and Windows CRT evidence requirements."""
    rows_by_path = {row[0].strip("`"): row for row in rows}
    openblas_files = sorted(
        path for path in actual_native
        if re.fullmatch(r"numpy\.libs/libscipy_openblas64_.*\.dll", path, re.I)
    )
    if openblas_files:
        for notice in (WINDOWS_OPENBLAS_LICENSE, WINDOWS_LAPACK_LICENSE):
            target = f"{root}/{notice}"
            if target not in members:
                errors.append(f"OpenBLAS payload requires license notice: {notice}")
        try:
            openblas_text = _archive_read(archive, f"{root}/{WINDOWS_OPENBLAS_LICENSE}").decode("utf-8")
            lapack_text = _archive_read(archive, f"{root}/{WINDOWS_LAPACK_LICENSE}").decode("utf-8")
            openblas_normalized = " ".join(openblas_text.split())
            lapack_normalized = " ".join(lapack_text.split())
            if not all(value in openblas_normalized for value in (
                "Copyright (c) 2011-2014, The OpenBLAS Project",
                "All rights reserved.",
                "Redistribution and use in source and binary forms",
                "source code must retain the above copyright notice",
                "binary form must reproduce the above copyright notice",
                "Neither the name of the OpenBLAS project",
                "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS",
            )):
                errors.append("OpenBLAS BSD-3-Clause notice is incomplete")
            if not all(value in lapack_normalized for value in (
                "BSD-3-Clause-Attribution",
                "Copyright (c) 1992-2013 The University of Tennessee",
                "Additional copyrights may follow",
                "Redistribution and use in source and binary forms",
                "source code must retain the above copyright notice",
                "binary form must reproduce the above copyright notice",
                "patent, copyright, or any other intellectual property rights",
                "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS",
            )):
                errors.append("LAPACK BSD-3-Clause-Attribution notice is incomplete")
        except (KeyError, RuntimeError, UnicodeDecodeError):
            pass
        for relative in openblas_files:
            row = rows_by_path.get(relative)
            if row is None:
                errors.append(f"OpenBLAS payload lacks a native inventory row: {relative}")
                continue
            _path, component, _version, license_name, notice, evidence = row
            references = set(_notice_references(notice))
            if "OpenBLAS" not in component or "LAPACK" not in component:
                errors.append(f"OpenBLAS payload has incorrect component classification: {relative}")
            if license_name != "BSD-3-Clause AND BSD-3-Clause-Attribution":
                errors.append(f"OpenBLAS payload lacks both BSD license identifiers: {relative}")
            if not {WINDOWS_OPENBLAS_LICENSE, WINDOWS_LAPACK_LICENSE}.issubset(references):
                errors.append(f"OpenBLAS payload does not reference both complete notices: {relative}")
            if "NumPy" not in evidence or "SHA-256 match" not in evidence:
                errors.append(f"OpenBLAS payload lacks NumPy wheel hash provenance: {relative}")

    kiwi_paths = sorted(
        path for path in actual_native
        if re.fullmatch(r"kiwisolver/_cext(?:\.[^/]+)?\.pyd", path, re.I)
    )
    for relative in kiwi_paths:
        row = rows_by_path.get(relative)
        if row is None:
            errors.append(f"kiwisolver extension lacks a native inventory row: {relative}")
            continue
        _path, component, version, license_name, notice, _evidence = row
        expected_notice = f"LICENSES/Python/kiwisolver-{version}/LICENSE"
        if component != "Python distribution kiwisolver" or license_name != "BSD-3-Clause":
            errors.append(f"kiwisolver extension has incorrect license classification: {relative}")
        if expected_notice not in _notice_references(notice):
            errors.append(f"kiwisolver extension points to the wrong license file: {relative}")

    hashed_msvcp = sorted(
        path for path in actual_native
        if re.search(
            r"(?:^|/)(?:msvcp140|vcruntime140(?:_1)?)-[0-9a-f]{32}\.dll$",
            path, re.I,
        )
    )
    if hashed_msvcp:
        errors.append(
            "Hash-named Microsoft CRT DLLs are not permitted in the release: "
            + ", ".join(hashed_msvcp)
        )
    expected_msvc = {
        "numpy.libs/msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    }
    missing_msvc = sorted(expected_msvc - actual_native)
    if missing_msvc:
        errors.append(
            "Release is missing original-named Visual Studio REDIST DLLs: "
            + ", ".join(missing_msvc)
        )
    msvc_files = sorted(path for path in actual_native if path in expected_msvc)
    for relative in msvc_files:
        row = rows_by_path.get(relative)
        if row is None:
            errors.append(f"Microsoft runtime DLL lacks a native inventory row: {relative}")
            continue
        _path, component, version, license_name, notice, evidence = row
        if component != "Microsoft Visual C++ Runtime":
            errors.append(f"Microsoft runtime DLL has incorrect component classification: {relative}")
        if license_name != "Microsoft Visual C++ Redistributable terms":
            errors.append(f"Microsoft runtime DLL lacks its terms classification: {relative}")
        if f"{root}/{WINDOWS_MSVCRT_TERMS}" not in members or WINDOWS_MSVCRT_TERMS not in _notice_references(notice):
            errors.append(f"Microsoft runtime DLL lacks its terms notice: {relative}")
        if not re.fullmatch(r"\d+(?:\.\d+){2,3}", version):
            errors.append(f"Microsoft runtime DLL lacks its exact PE ProductVersion: {relative}")
        if not all(value in evidence for value in (
            "CompanyName=Microsoft Corporation", "OriginalFilename=",
            f"ProductVersion={version}",
            "source SHA-256", "staged payload SHA-256",
        )):
            errors.append(f"Microsoft runtime DLL lacks per-file REDIST evidence: {relative}")
            continue
        if re.search(
            r"Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft\.VC\d+\.CRT",
            evidence,
        ) is None:
            errors.append(f"Microsoft runtime DLL lacks the Visual Studio REDIST source class: {relative}")
        source_hash = re.search(r"source SHA-256 ([0-9a-f]{64})", evidence, re.I)
        staged_hash = re.search(r"staged payload SHA-256 ([0-9a-f]{64})", evidence, re.I)
        archived_hash = _archive_sha256(archive, f"{root}/{relative}")
        if (
            source_hash is None or staged_hash is None
            or source_hash.group(1).casefold() != staged_hash.group(1).casefold()
            or staged_hash.group(1).casefold() != archived_hash
        ):
            errors.append(
                f"Microsoft runtime DLL hash does not match its recorded REDIST source: {relative}"
            )

    if "numpy.libs/msvcp140.dll" in actual_native:
        for pattern in (
            r"numpy/_core/_multiarray_umath(?:\.[^/]+)?\.pyd",
            r"numpy/fft/_pocketfft_umath(?:\.[^/]+)?\.pyd",
        ):
            matching = sorted(
                path for path in actual_native if re.fullmatch(pattern, path, re.I)
            )
            if len(matching) != 1:
                errors.append(
                    "Expected exactly one required NumPy extension for CRT import rewrite: "
                    + pattern
                )
                continue
            relative = matching[0]
            row = rows_by_path.get(relative)
            if row is None:
                errors.append(f"NumPy CRT-patched extension lacks a native inventory row: {relative}")
                continue
            _path, component, version, _license, notice, evidence = row
            archived_hash = _archive_sha256(archive, f"{root}/{relative}")
            source_hash = re.search(r"source SHA-256 ([0-9a-f]{64})", evidence, re.I)
            patched_hash = re.search(r"patched payload SHA-256 ([0-9a-f]{64})", evidence, re.I)
            if component != "Python distribution numpy" or not version:
                errors.append(f"NumPy CRT-patched extension has incorrect classification: {relative}")
            if "RECORD entry" not in evidence or "msvcp140.dll" not in evidence:
                errors.append(f"NumPy CRT-patched extension lacks import rewrite and RECORD evidence: {relative}")
            if source_hash is None or patched_hash is None or source_hash.group(1).casefold() == patched_hash.group(1).casefold() or patched_hash.group(1).casefold() != archived_hash:
                errors.append(f"NumPy CRT-patched extension hashes are incomplete or inconsistent: {relative}")
            expected_notice_prefix = f"LICENSES/Python/numpy-{version}/"
            if not any(item.startswith(expected_notice_prefix) for item in _notice_references(notice)):
                errors.append(f"NumPy CRT-patched extension lacks its bundled NumPy license notice: {relative}")

    for relative in sorted(path for path in actual_native if Path(path).name.casefold() == "libffi-8.dll"):
        row = rows_by_path.get(relative)
        if row is None:
            errors.append(f"libffi-8.dll lacks a native inventory row: {relative}")
            continue
        _path, component, version, _license_name, _notice, evidence = row
        if component != "libffi" or version != "Windows ABI 8; upstream release version not encoded":
            errors.append(f"libffi-8.dll invents or omits its version limitation: {relative}")
        if "does not record the upstream release version" not in evidence:
            errors.append(f"libffi-8.dll lacks evidence that its exact release is unavailable: {relative}")


def _validate_platform_documentation(
    archive: Path, root: str, members: set[str], platform: str, errors: list[str]
) -> None:
    """Require staged notices to describe the actual platform release."""
    documents = ("README.md", "THIRD_PARTY_NOTICES.md")
    texts: dict[str, str] = {}
    for name in documents:
        member = f"{root}/{name}"
        if member not in members:
            errors.append(f"{platform} release is missing platform-scoped documentation: {name}")
            continue
        try:
            texts[name] = _archive_read(archive, member).decode("utf-8")
        except (KeyError, RuntimeError, UnicodeDecodeError):
            errors.append(f"{platform} release documentation is not readable UTF-8: {name}")
    combined = "\n".join(texts.values())
    if "<!-- platform:" in combined:
        errors.append(f"{platform} release documentation contains unrendered platform markers")
    if platform == "Windows":
        forbidden = re.compile(
            r"libquadmath|libgfortran|gcc-8\.5\.0-28\.el8_10\.alma\.1\.src\.rpm|GCC source RPM",
            re.I,
        )
        if forbidden.search(combined):
            errors.append("Windows release documentation contains Linux-only GCC runtime/source evidence")
    else:
        required = (
            "libquadmath", "libgfortran", "gcc-8.5.0-28.el8_10.alma.1.src.rpm",
        )
        for value in required:
            if value.casefold() not in combined.casefold():
                errors.append(f"Linux release documentation omits required GCC runtime/source notice: {value}")


def _elf_build_id(data: bytes) -> str | None:
    """Read a GNU build ID from an ELF64 little-endian program note."""
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        return None
    try:
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        for index in range(phnum):
            base = phoff + index * phentsize
            if base + 56 > len(data):
                return None
            p_type = struct.unpack_from("<I", data, base)[0]
            if p_type != 4:  # PT_NOTE
                continue
            offset, filesz = struct.unpack_from("<QQ", data, base + 8)[0], struct.unpack_from("<Q", data, base + 32)[0]
            end = offset + filesz
            cursor = offset
            while cursor + 12 <= end <= len(data):
                namesz, descsz, note_type = struct.unpack_from("<III", data, cursor)
                cursor += 12
                name_end = cursor + namesz
                name = data[cursor:name_end]
                cursor = (name_end + 3) & ~3
                desc_end = cursor + descsz
                desc = data[cursor:desc_end]
                cursor = (desc_end + 3) & ~3
                if note_type == 3 and name.rstrip(b"\0") == b"GNU":
                    return desc.hex()
    except (IndexError, struct.error):
        return None
    return None


def _validate_libquadmath(
    archive: Path,
    root: str,
    actual_native: set[str],
    elf_build_ids: dict[str, str | None],
    rows: list[tuple[str, ...]],
    members: set[str],
    errors: list[str],
) -> None:
    # This Linux-only source package is required whenever the actual archive
    # carries the known runtime. A filename only detects the candidate; the
    # staged inventory must prove the build ID and matching source evidence.
    candidates = sorted(
        path for path in actual_native
        if "libquadmath" in Path(path).name.lower()
        or elf_build_ids.get(path) == LIBQUADMATH_BUILD_ID
    )
    if not candidates:
        return
    required = (LIBQUADMATH_SOURCE, LIBQUADMATH_LICENSE, LIBQUADMATH_MANIFEST)
    missing = [f"{root}/{path}" for path in required if f"{root}/{path}" not in members]
    if missing:
        errors.append("libquadmath source evidence missing from Linux archive: " + ", ".join(missing))
        return
    source_path = f"{root}/{LIBQUADMATH_SOURCE}"
    if _archive_sha256(archive, source_path) != LIBQUADMATH_SOURCE_SHA256:
        errors.append("Bundled GCC source RPM does not match the pinned AlmaLinux source SHA-256")
    license_path = f"{root}/{LIBQUADMATH_LICENSE}"
    if _archive_sha256(archive, license_path) != LIBQUADMATH_LICENSE_SHA256:
        errors.append("Bundled GNU LGPL text is not the complete verified libquadmath LGPL 2.1 text")
    manifest_path = f"{root}/{LIBQUADMATH_MANIFEST}"
    manifest = _archive_read(archive, manifest_path).decode("utf-8", errors="replace")
    if (
        LIBQUADMATH_BUILD_ID not in manifest
        or LIBQUADMATH_SOURCE_SHA256 not in manifest
        or LIBQUADMATH_BINARY_RPM_SHA256 not in manifest
        or LIBQUADMATH_ORIGINAL_BINARY_SHA256 not in manifest
    ):
        errors.append("libquadmath source manifest lacks the verified ELF build ID or RPM/source checksums")
    if LIBQUADMATH_LICENSE_SHA256 not in manifest:
        errors.append("libquadmath source manifest lacks the verified LGPL text checksum")
    permission_path = f"{root}/{LGPL_ADDITIONAL_PERMISSION}"
    permission = _archive_read(archive, permission_path).decode("utf-8", errors="replace") if permission_path in members else ""
    permission_terms = (
        "modify the MiLaS work", "recipient's own use", "reverse engineer",
        "debug modifications", "does not authorize commercial", "distribution",
        "all other terms",
    )
    if any(term.casefold() not in permission.casefold() for term in permission_terms):
        errors.append("libquadmath LGPL section 6 additional permission is missing or incomplete")
    legacy_terms = (
        "remains a legal review item", "still needs review",
        "legal review should confirm", "PolyForm-permitted purposes satisfies",
    )
    if any(term.casefold() in manifest.casefold() or term.casefold() in permission.casefold()
           for term in legacy_terms):
        errors.append("libquadmath documentation retains an obsolete unresolved section 6 statement")
    if "debug modifications to libquadmath" not in manifest:
        errors.append("libquadmath source manifest does not document the section 6 debugging permission")
    if "ELF SONAME:" not in manifest or "DT_NEEDED" not in manifest or "$ORIGIN" not in manifest:
        errors.append("libquadmath source manifest lacks evidence of the replaceable shared-library mechanism")
    by_path = {row[0].strip("`"): row for row in rows}
    for relative in candidates:
        row = by_path.get(relative)
        if row is None:
            errors.append(f"libquadmath lacks a native inventory row: {relative}")
            continue
        _path, component, version, license_name, notice, _evidence = row
        if component != "GCC libquadmath (AlmaLinux runtime RPM)":
            errors.append(f"libquadmath provenance is not correctly identified: {relative}")
        if version != "8.5.0-28.el8_10.alma.1 (GCC 8.5.0)":
            errors.append(f"libquadmath version does not match its verified source RPM: {relative}")
        if license_name != "LGPL-2.1-or-later":
            errors.append(f"libquadmath is not recorded under its LGPL terms: {relative}")
        if (
            LIBQUADMATH_ORIGINAL_BINARY_SHA256 not in _evidence
            or LIBQUADMATH_BINARY_RPM_SHA256 not in _evidence
            or LIBQUADMATH_SOURCE_SHA256 not in _evidence
        ):
            errors.append(f"libquadmath inventory evidence lacks the binary/source RPM hashes: {relative}")
        wanted_notices = {LIBQUADMATH_LICENSE, LIBQUADMATH_SOURCE, LIBQUADMATH_MANIFEST}
        if not wanted_notices.issubset(set(_notice_references(notice))):
            errors.append(f"libquadmath row does not reference its license and source evidence: {relative}")
        binary_path = f"{root}/{relative}"
        binary_data = _archive_read(archive, binary_path)
        binary_digest = hashlib.sha256(binary_data).hexdigest()
        if _elf_build_id(binary_data) != LIBQUADMATH_BUILD_ID:
            errors.append(f"libquadmath archived ELF build ID is not the verified GCC build: {relative}")
        expected_entry = (
            f"### `{relative}`\n\n- Archive-relative binary: `{relative}`\n"
            f"- SHA-256 of this staged binary: `{binary_digest}`\n"
            f"- ELF build ID: `{LIBQUADMATH_BUILD_ID}`"
        )
        if expected_entry not in manifest:
            errors.append(f"libquadmath source manifest does not match the archived binary hash: {relative}")


def _validate_libgfortran(
    archive: Path, root: str, actual_native: set[str],
    elf_build_ids: dict[str, str | None], rows: list[tuple[str, ...]],
    members: set[str], errors: list[str],
) -> None:
    candidates = sorted(
        path for path in actual_native
        if "libgfortran" in Path(path).name.casefold()
        or elf_build_ids.get(path) == LIBGFORTRAN_BUILD_ID
    )
    if not candidates:
        return
    required = (LIBGFORTRAN_SOURCE, LIBGFORTRAN_GPL, LIBGFORTRAN_EXCEPTION, LIBGFORTRAN_MANIFEST)
    missing = [f"{root}/{path}" for path in required if f"{root}/{path}" not in members]
    if missing:
        errors.append("libgfortran source/license evidence missing from Linux archive: " + ", ".join(missing))
        return
    if _archive_sha256(archive, f"{root}/{LIBGFORTRAN_SOURCE}") != LIBGFORTRAN_SOURCE_SHA256:
        errors.append("libgfortran GCC source RPM does not match the pinned SHA-256")
    for path, expected, label in (
        (LIBGFORTRAN_GPL, LIBGFORTRAN_GPL_SHA256, "GPL-3.0"),
        (LIBGFORTRAN_EXCEPTION, LIBGFORTRAN_EXCEPTION_SHA256, "GCC Runtime Library Exception 3.1"),
    ):
        if _archive_sha256(archive, f"{root}/{path}") != expected:
            errors.append(f"libgfortran {label} text is not the complete verified source text")
    manifest_path = f"{root}/{LIBGFORTRAN_MANIFEST}"
    manifest = _archive_read(archive, manifest_path).decode("utf-8", errors="replace")
    for required_text in (
        LIBGFORTRAN_BUILD_ID, LIBGFORTRAN_BINARY_SHA256, LIBGFORTRAN_SOURCE_SHA256,
        LIBGFORTRAN_GPL_SHA256, LIBGFORTRAN_EXCEPTION_SHA256,
        "8.5.0-28.el8_10.alma.1", "GCC `8.5.0`", "ABI major: `5`",
        "libgfortran/runtime/error.c",
    ):
        if required_text not in manifest:
            errors.append("libgfortran source manifest lacks verified provenance: " + required_text)
    by_path = {row[0].strip("`"): row for row in rows}
    for relative in candidates:
        row = by_path.get(relative)
        if row is None:
            errors.append(f"libgfortran lacks a native inventory row: {relative}")
            continue
        _path, component, version, license_name, notice, evidence = row
        if component != "GCC libgfortran (AlmaLinux GCC source RPM)":
            errors.append(f"libgfortran provenance is not correctly identified: {relative}")
        if version != "8.5.0-28.el8_10.alma.1 (GCC 8.5.0; ABI 5)":
            errors.append(f"libgfortran version/ABI does not match its source RPM: {relative}")
        if license_name != "GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1":
            errors.append(f"libgfortran is not recorded under its GCC runtime license: {relative}")
        if "numpy" in (component + " " + license_name + " " + notice).casefold():
            errors.append(f"libgfortran is incorrectly associated with NumPy metadata: {relative}")
        wanted = {LIBGFORTRAN_GPL, LIBGFORTRAN_EXCEPTION, LIBGFORTRAN_SOURCE, LIBGFORTRAN_MANIFEST}
        if not wanted.issubset(set(_notice_references(notice))):
            errors.append(f"libgfortran inventory row omits source/license evidence: {relative}")
        if any(value not in evidence for value in (
            LIBGFORTRAN_BUILD_ID, LIBGFORTRAN_BINARY_SHA256, LIBGFORTRAN_SOURCE_SHA256,
        )):
            errors.append(f"libgfortran inventory evidence lacks binary/source identity: {relative}")
        binary_path = f"{root}/{relative}"
        payload = _archive_read(archive, binary_path)
        digest = hashlib.sha256(payload).hexdigest()
        if _elf_build_id(payload) != LIBGFORTRAN_BUILD_ID or digest != LIBGFORTRAN_BINARY_SHA256:
            errors.append(f"libgfortran archived ELF identity differs from the verified binary: {relative}")
        expected_entry = (
            f"### `{relative}`\n\n- Archive-relative binary SHA-256: `{digest}`\n"
            f"- ELF build ID: `{LIBGFORTRAN_BUILD_ID}`"
        )
        if expected_entry not in manifest:
            errors.append(f"libgfortran source manifest does not match archived binary hash: {relative}")


def validate_archive(archive: Path, package_root: str) -> tuple[str, ...]:
    members = archive_members(archive)
    root = package_root.rstrip("/")
    expected = tuple(f"{root}/{path}" for path in REQUIRED_FILES)
    missing = tuple(path for path in expected if path not in members)
    errors = []
    if missing:
        errors.append("Required file missing from release archive: " + ", ".join(missing))

    inventory_path = f"{root}/BUNDLED_RUNTIME_INVENTORY.md"
    if inventory_path not in members:
        raise RuntimeError("; ".join(errors))

    actual_native: set[str] = set()
    elf_build_ids: dict[str, str | None] = {}
    inventory_bytes = None
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            inventory_bytes = handle.read(_zip_member_name(handle, inventory_path))
            for item in handle.infolist():
                member = normalized_member(item.filename)
                if not item.is_dir() and member.startswith(root + "/"):
                    with handle.open(item) as stream:
                        payload = stream.read()
                        if _native_signature(payload[:4], member):
                            actual_native.add(member[len(root) + 1 :])
                            if payload.startswith(b"\x7fELF"):
                                elf_build_ids[member[len(root) + 1 :]] = _elf_build_id(payload)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as handle:
            item = handle.extractfile(inventory_path)
            assert item is not None
            inventory_bytes = item.read()
            for member in handle.getmembers():
                name = normalized_member(member.name)
                if not member.isfile() or not name.startswith(root + "/"):
                    continue
                stream = handle.extractfile(member)
                if stream is not None:
                    payload = stream.read()
                    if _native_signature(payload[:4], name):
                        relative = name[len(root) + 1 :]
                        actual_native.add(relative)
                        if payload.startswith(b"\x7fELF"):
                            elf_build_ids[relative] = _elf_build_id(payload)

    linux_quadmath_files = []
    if "linux" in root.casefold():
        linux_quadmath_files = sorted(
            path for path in actual_native
            if "libquadmath" in Path(path).name.lower()
            or elf_build_ids.get(path) == LIBQUADMATH_BUILD_ID
        )

    try:
        inventory_text = inventory_bytes.decode("utf-8")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Runtime inventory is not valid UTF-8 text") from exc
    if ABSOLUTE_PATH.search(inventory_text):
        errors.append("Runtime inventory contains an absolute build-machine path")

    try:
        rows = _inventory_rows(inventory_text)
    except RuntimeError as exc:
        errors.append(str(exc))
        rows = []
    if "linux" in root.casefold():
        declared_quadmath = [
            row[0].strip("`") for row in rows
            if "libquadmath" in row[1].casefold()
        ]
        linux_quadmath_files = sorted(set(linux_quadmath_files) | set(declared_quadmath))
        if linux_quadmath_files:
            expected += tuple(
                f"{root}/{path}"
                for path in (
                    LIBQUADMATH_SOURCE, LIBQUADMATH_LICENSE, LIBQUADMATH_MANIFEST,
                    LGPL_ADDITIONAL_PERMISSION,
                )
            )
        linux_gfortran_files = sorted(
            path for path in actual_native
            if "libgfortran" in Path(path).name.casefold()
            or elf_build_ids.get(path) == LIBGFORTRAN_BUILD_ID
        )
        if linux_gfortran_files:
            expected += tuple(
                f"{root}/{path}"
                for path in (
                    LIBGFORTRAN_SOURCE, LIBGFORTRAN_GPL,
                    LIBGFORTRAN_EXCEPTION, LIBGFORTRAN_MANIFEST,
                )
            )
    declared: set[str] = set()
    for row in rows:
        relative, component, version, license_name, notice, evidence = row
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in relative:
            errors.append(f"Inventory path is not archive-relative: {relative}")
            continue
        if relative in declared:
            errors.append(f"Duplicate native inventory row: {relative}")
        declared.add(relative)
        if any(not value or "UNRESOLVED" in value.upper() or "UNCLASSIFIED" in value.upper()
               for value in (component, version, license_name, notice, evidence)):
            errors.append(f"Native payload lacks verified classification: {relative}")
        if component.startswith("Debian package ") and len(version) % 2 == 0:
            midpoint = len(version) // 2
            if version[:midpoint] == version[midpoint:]:
                errors.append(
                    f"Debian package version is duplicated in native inventory: {relative}"
                )
        for notice_ref in _notice_references(notice):
            if notice_ref in ("LICENSE", "NOTICE"):
                check_paths = [f"{root}/{notice_ref}"]
                if not all(path in members for path in check_paths):
                    errors.append(f"Notice referenced by {relative} is missing: {notice_ref}")
                continue
            if notice_ref.startswith("LICENSES/") and notice_ref.endswith("/"):
                prefix = f"{root}/{notice_ref}"
                if not any(member.startswith(prefix) and not member.endswith("/") for member in members):
                    errors.append(f"License directory referenced by {relative} is empty: {notice_ref}")
            else:
                target = f"{root}/{notice_ref}"
                if target not in members:
                    errors.append(f"Notice referenced by {relative} is missing: {notice_ref}")

    if "windows" in root.casefold():
        _validate_windows_native_licenses(
            archive, root, actual_native, rows, members, errors,
        )
        _validate_platform_documentation(archive, root, members, "Windows", errors)
    elif "linux" in root.casefold() and f"{root}/{WINDOWS_MSVCRT_TERMS}" in members:
        errors.append("Linux release includes the Windows-only Visual C++ Redistributable terms document")

    omitted = sorted(actual_native - declared)
    extra = sorted(declared - actual_native)
    if omitted:
        errors.append("Native archive payload missing from inventory: " + ", ".join(omitted))
    if extra:
        errors.append("Inventory lists native files absent from archive: " + ", ".join(extra))
    unresolved_count = re.search(r"^Unresolved native payload count: (\d+)\.", inventory_text, re.M)
    if unresolved_count is None:
        errors.append("Runtime inventory has no unresolved-payload count")
    elif int(unresolved_count.group(1)) != 0:
        errors.append(f"Runtime inventory reports {unresolved_count.group(1)} unresolved native payload(s)")

    if linux_quadmath_files:
        _validate_libquadmath(
            archive, root, actual_native, elf_build_ids, rows, members, errors
        )
    linux_gfortran_files = (
        sorted(path for path in actual_native
               if "libgfortran" in Path(path).name.casefold()
               or elf_build_ids.get(path) == LIBGFORTRAN_BUILD_ID)
        if "linux" in root.casefold() else []
    )
    if "linux" in root.casefold():
        _validate_libgfortran(
            archive, root, actual_native, elf_build_ids, rows, members, errors
        )
        if linux_quadmath_files or linux_gfortran_files:
            _validate_platform_documentation(archive, root, members, "Linux", errors)

    if errors:
        raise RuntimeError("; ".join(errors))
    return tuple(dict.fromkeys(expected))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("package_root")
    args = parser.parse_args()
    verified = validate_archive(args.archive, args.package_root)
    print(f"Verified {len(verified)} required evidence files and a fully classified native inventory in {args.archive}")


if __name__ == "__main__":
    main()
