#!/usr/bin/env python3
"""Print build-host evidence for unresolved Linux native payloads.

The output is diagnostic and may contain local absolute paths. Do not copy it
into a release archive. A filename/SONAME match is reported as evidence only;
it does not assign a license.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import sysconfig
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import stage_release_licenses as stager
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import stage_release_licenses as stager


def _run(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            arguments, check=False, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    if result.returncode == 0:
        return result.stdout.strip()
    return f"<exit {result.returncode}: {result.stderr.strip()}>"


def _sha256(path: Path) -> str | None:
    try:
        return stager.sha256_file(path)
    except OSError:
        return None


def _package_owner(path: Path) -> dict[str, object]:
    record, attempted_queries = stager._dpkg_package_lookup(path)
    if record is None:
        return {
            "owner_output": "No dpkg-owned file matched original, resolved, or usr-merged path variants",
            "package": None, "version": None,
            "copyright_file": None, "license_field": None,
            "attempted_queries": attempted_queries,
        }
    package = str(record["package"])
    version = str(record["version"]) if record.get("version") else None
    copyright_file = record.get("copyright_file")
    license_field = str(record["license"]) if record.get("license") else None
    return {
        "owner_output": str(record["owner_line"]),
        "package": package,
        "version": version,
        "copyright_file": str(copyright_file) if copyright_file else None,
        "license_field": license_field,
        "owner_path": str(record.get("owner_path", "")),
        "attempted_queries": attempted_queries,
    }


def _ldd(path: Path) -> list[dict[str, object]]:
    output = _run(["ldd", str(path)])
    dependencies: list[dict[str, object]] = []
    for line in output.splitlines():
        match = re.match(r"\s*(\S+)\s+=>\s+(/\S+)", line)
        if match:
            name, raw_path = match.groups()
            reported = Path(raw_path)
            resolved = reported.resolve()
            dependencies.append({
                "soname": name,
                "reported_path": str(reported),
                "resolved_path": str(resolved),
                "sha256": _sha256(resolved),
                "elf_build_id": stager.elf_build_id(resolved),
                # Preserve the loader-reported spelling. On usr-merged Mint,
                # resolving /lib/... first can erase the path dpkg records.
                "package": _package_owner(reported),
            })
        elif "not found" in line:
            dependencies.append({"ldd_line": line.strip(), "resolution": "not found"})
    return dependencies


def _report_sources(report_path: Path | None) -> list[dict[str, str]]:
    if report_path is None:
        return []
    try:
        root = ET.parse(report_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"Cannot parse Nuitka report {report_path}: {exc}") from exc
    records = []
    for element in root.iter():
        source = element.attrib.get("source_path")
        destination = element.attrib.get("dest_path")
        if source or destination:
            records.append({
                "record_type": element.tag,
                "module": element.attrib.get("name", ""),
                "kind": element.attrib.get("kind", ""),
                "staged_destination": (destination or "").replace("\\", "/").removeprefix("./"),
                "source_path": source or "",
            })
    return records


def _unresolved_paths(release_dir: Path) -> list[str]:
    inventory = release_dir / "BUNDLED_RUNTIME_INVENTORY.md"
    if not inventory.is_file():
        return [path.relative_to(release_dir).as_posix() for path in stager.native_payload_files(release_dir)]
    try:
        from verify_release_archive import _inventory_rows
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from verify_release_archive import _inventory_rows
    rows = _inventory_rows(inventory.read_text(encoding="utf-8"))
    return [row[0].strip("`") for row in rows if any(
        "UNRESOLVED" in value.upper() or "UNCLASSIFIED" in value.upper()
        for value in row[1:]
    )]


def diagnose(dist_dir: Path, release_dir: Path, report_path: Path | None) -> dict[str, object]:
    report_records = _report_sources(report_path)
    unresolved = _unresolved_paths(release_dir)
    package_records: dict[str, dict[str, object]] = {}
    host_candidates_by_build_id: dict[str, list[Path]] = {}
    for candidate in stager._host_library_candidates():
        build_id = stager.elf_build_id(candidate)
        if build_id:
            host_candidates_by_build_id.setdefault(build_id, []).append(candidate)
    payloads = []
    for relative in unresolved:
        staged = release_dir / relative
        staged_hash = _sha256(staged)
        staged_build_id = stager.elf_build_id(staged)
        matching_report = [item for item in report_records if (
            item["staged_destination"] == relative
            or (item["module"] and Path(relative).name.startswith(Path(item["module"].replace(".", "/")).name + "."))
        )]
        sources = []
        for record in matching_report:
            raw_source = record["source_path"]
            source_path = stager._expand_nuitka_source_path(raw_source) if raw_source else None
            source_data: dict[str, object] = dict(record)
            if source_path:
                shared_dir = Path(sysconfig.get_config_var("DESTSHARED") or "")
                is_cpython_source = False
                try:
                    source_path.relative_to(shared_dir.resolve(strict=True))
                    is_cpython_source = record.get("record_type") == "module" and record.get("kind") == "PythonExtensionModule"
                except (OSError, ValueError):
                    pass
                source_hash = _sha256(source_path)
                source_build_id = stager.elf_build_id(source_path)
                source_matches_payload = bool(
                    (source_hash and staged_hash and source_hash == staged_hash)
                    or (source_build_id and staged_build_id and source_build_id == staged_build_id)
                )
                verified_cpython_extension = is_cpython_source and source_matches_payload
                source_data.update({
                    "resolved_source_path": str(source_path),
                    "sha256": source_hash,
                    "elf_build_id": source_build_id,
                    "cpython_extension_source": is_cpython_source,
                    "source_matches_staged_payload": source_matches_payload,
                    "classification_status": "verified CPython extension" if verified_cpython_extension else "evidence only; source does not yet match staged payload",
                    "component": "CPython runtime" if verified_cpython_extension else None,
                    "component_version": sys.version.split()[0] if verified_cpython_extension else None,
                    "component_license": "PSF-2.0" if verified_cpython_extension else None,
                    "component_license_notice": "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt" if verified_cpython_extension else None,
                    "package": _package_owner(source_path),
                    "elf_dynamic_section": _run(["readelf", "-d", str(source_path)]),
                    "ldd": _ldd(source_path),
                })
                owner = source_data["package"]
                if owner.get("copyright_file"):
                    cp = owner["copyright_file"]
                    package_records[cp] = {
                        "package": owner,
                        "contents": Path(cp).read_text(encoding="utf-8", errors="replace"),
                    }
            sources.append(source_data)
        build_id_matches = []
        if staged_build_id:
            for candidate in host_candidates_by_build_id.get(staged_build_id, []):
                owner = _package_owner(candidate)
                match_data = {
                    "host_candidate_path": str(candidate),
                    "sha256": _sha256(candidate),
                    "elf_build_id": staged_build_id,
                    "package": owner,
                }
                copyright_path = owner.get("copyright_file")
                if copyright_path and copyright_path not in package_records:
                    package_records[copyright_path] = {
                        "package": owner,
                        "contents": Path(copyright_path).read_text(encoding="utf-8", errors="replace"),
                    }
                build_id_matches.append(match_data)
        staged_data: dict[str, object] = {
            "archive_relative_path": relative,
            "staged_path": str(staged),
            "exists": staged.is_file(),
            "sha256": _sha256(staged),
            "elf_build_id": staged_build_id,
            "host_files_with_matching_elf_build_id": build_id_matches,
            "elf_dynamic_section": _run(["readelf", "-d", str(staged)]),
            "ldd": _ldd(staged) if staged.is_file() else [],
            "nuitka_report_sources": sources,
            "classification_note": "Evidence only; no license is inferred from this payload's filename or SONAME.",
        }
        for dependency in staged_data["ldd"]:
            owner = dependency.get("package")
            copyright_path = owner.get("copyright_file") if owner else None
            if copyright_path and copyright_path not in package_records:
                package_records[copyright_path] = {
                    "package": owner,
                    "contents": Path(copyright_path).read_text(encoding="utf-8", errors="replace"),
                }
        payloads.append(staged_data)

    return {
        "warning": "Diagnostic output contains build-machine absolute paths; do not ship it.",
        "python_build": {
            "executable": sys.executable,
            "version": sys.version,
            "sys_base_prefix": sys.base_prefix,
            "sysconfig_destshared": sysconfig.get_config_var("DESTSHARED"),
            "openssl_version_reported_by_ssl_module": ssl.OPENSSL_VERSION,
            "cpython_license": "PSF-2.0",
            "cpython_license_notice": "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
        },
        "distribution_paths": {"deployment": str(dist_dir), "release": str(release_dir),
                               "nuitka_report": str(report_path) if report_path else None},
        "unresolved_payload_count": len(unresolved),
        "payloads": payloads,
        "package_copyright_records": package_records,
        "provided_by_system_not_bundled": "For each ELF object, ldd output includes resolved dependencies; dependency objects outside the release directory are host-provided and are reported with dpkg owner/version/copyright metadata where available.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--nuitka-report", type=Path)
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()
    result = diagnose(args.dist_dir.resolve(), args.release_dir.resolve(),
                      args.nuitka_report.resolve() if args.nuitka_report else None)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote diagnostic provenance to {args.output}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
