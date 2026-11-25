import hashlib
import importlib.util
import json
from email.message import Message
from pathlib import Path
import tarfile
import types
import zipfile


_STAGER_PATH = Path(__file__).parents[1] / "packaging" / "stage_release_licenses.py"
_SPEC = importlib.util.spec_from_file_location("milas_release_license_stager", _STAGER_PATH)
_STAGER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STAGER)

_ARCHIVE_VALIDATOR_PATH = Path(__file__).parents[1] / "packaging" / "verify_release_archive.py"
_ARCHIVE_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "milas_release_archive_validator", _ARCHIVE_VALIDATOR_PATH
)
_ARCHIVE_VALIDATOR = importlib.util.module_from_spec(_ARCHIVE_VALIDATOR_SPEC)
_ARCHIVE_VALIDATOR_SPEC.loader.exec_module(_ARCHIVE_VALIDATOR)

_DIAGNOSTIC_PATH = Path(__file__).parents[1] / "packaging" / "diagnose_native_provenance.py"
_DIAGNOSTIC_SPEC = importlib.util.spec_from_file_location(
    "milas_native_provenance_diagnostic", _DIAGNOSTIC_PATH
)
_DIAGNOSTIC = importlib.util.module_from_spec(_DIAGNOSTIC_SPEC)
_DIAGNOSTIC_SPEC.loader.exec_module(_DIAGNOSTIC)

_WINDOWS_CRT_PREP_PATH = Path(__file__).parents[1] / "packaging" / "prepare_windows_crt.py"
_WINDOWS_CRT_PREP_SPEC = importlib.util.spec_from_file_location(
    "milas_windows_crt_preparation", _WINDOWS_CRT_PREP_PATH
)
_WINDOWS_CRT_PREP = importlib.util.module_from_spec(_WINDOWS_CRT_PREP_SPEC)
_WINDOWS_CRT_PREP_SPEC.loader.exec_module(_WINDOWS_CRT_PREP)


def test_platform_documentation_and_static_licenses_are_scoped(tmp_path):
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    notices = (Path(__file__).parents[1] / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    windows = "\n".join((
        _STAGER.render_platform_document(readme, "Windows"),
        _STAGER.render_platform_document(notices, "Windows"),
    )).casefold()
    linux = "\n".join((
        _STAGER.render_platform_document(readme, "Linux"),
        _STAGER.render_platform_document(notices, "Linux"),
    )).casefold()

    assert "libquadmath" not in windows
    assert "libgfortran" not in windows
    assert "gcc-8.5.0-28.el8_10.alma.1.src.rpm" not in windows
    assert "msvcp140.dll" in windows and "openblas" in windows
    assert "libquadmath" in linux and "libgfortran" in linux
    assert "gcc-8.5.0-28.el8_10.alma.1.src.rpm" in linux
    assert "section 6" in linux
    assert "<!-- platform:" not in windows + linux

    windows_licenses = tmp_path / "windows-licenses"
    linux_licenses = tmp_path / "linux-licenses"
    windows_licenses.mkdir()
    linux_licenses.mkdir()
    _STAGER.copy_static_license_files(windows_licenses, "Windows")
    _STAGER.copy_static_license_files(linux_licenses, "Linux")
    for name in (
        "GNU-LGPL-2.1.txt", "GPL-3.0.txt",
        "GCC-Runtime-Library-Exception-3.1.txt",
    ):
        assert not (windows_licenses / name).exists()
        assert (linux_licenses / name).is_file()
    assert (windows_licenses / "Microsoft-Visual-C-Redistributable-terms.md").is_file()
    assert not (linux_licenses / "Microsoft-Visual-C-Redistributable-terms.md").exists()


def test_kiwisolver_curated_license_overrides_malformed_wheel_metadata():
    metadata = Message()
    metadata["Name"] = "kiwisolver"
    metadata["License-Expression"] = "========================="
    dist = types.SimpleNamespace(metadata=metadata)

    assert _STAGER._license_for_distribution(dist) == "BSD-3-Clause"


def test_linux_kiwisolver_uses_verified_nuitka_source_when_payload_hash_changes(
    tmp_path, monkeypatch,
):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    payload = release / "kiwisolver" / "_cext.cpython-312-x86_64-linux-gnu.so"
    payload.parent.mkdir(parents=True)
    licenses.mkdir(parents=True)
    payload.write_bytes(b"Nuitka transformed kiwisolver ELF payload")
    notice = licenses / "Python" / "kiwisolver-1.5.1" / "LICENSE"
    notice.parent.mkdir(parents=True)
    notice.write_text("BSD 3-Clause license", encoding="utf-8")

    report_source = _STAGER.NativeProvenance(
        "Python distribution kiwisolver", "1.5.1", "BSD-3-Clause",
        "LICENSES/Python/kiwisolver-1.5.1/",
        "Nuitka report source resolves to installed kiwisolver 1.5.1 distribution metadata",
    )
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda _root: [payload])
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *_args: {
        payload.relative_to(release).as_posix(): report_source,
    })
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *_args: {})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *_args: "ELF fixture")

    rows, unresolved = _STAGER.native_component_rows(
        release, [("kiwisolver", "1.5.1", "bad wheel metadata")],
        "Linux CPython fixture", "OpenSSL 3.0.2", "fixture", "8.6.12", "8.6",
        licenses, "0.1.0",
    )

    assert not unresolved
    assert len(rows) == 1
    assert (
        "| `kiwisolver/_cext.cpython-312-x86_64-linux-gnu.so` | "
        "Python distribution kiwisolver | 1.5.1 | BSD-3-Clause | "
        "LICENSES/Python/kiwisolver-1.5.1/LICENSE |"
    ) in rows[0]


def test_windows_crt_preparation_patches_imports_and_copies_redist_bytes(
    tmp_path, monkeypatch,
):
    dist = tmp_path / "dist"
    numpy_core = dist / "numpy" / "_core"
    numpy_libs = dist / "numpy.libs"
    crt = dist / "numpy.libs" / "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
    numpy_core.mkdir(parents=True)
    numpy_libs.mkdir(parents=True)
    pyd = numpy_core / "_multiarray_umath.pyd"
    pyd.write_bytes(b"MZ NumPy extension fixture")
    crt.write_bytes(b"MZ wheel CRT fixture")
    (dist / "vcruntime140.dll").write_bytes(b"MZ old runtime fixture")
    (dist / "vcruntime140_1.dll").write_bytes(b"MZ old runtime support fixture")

    redist = (
        tmp_path / "VS" / "VC" / "Redist" / "MSVC" / "14.44.35211"
        / "x64" / "Microsoft.VC143.CRT"
    )
    redist.mkdir(parents=True)
    sources = {}
    for name in _WINDOWS_CRT_PREP.CRT_FILES:
        source = redist / name
        source.write_bytes(f"MZ official REDIST bytes for {name}".encode())
        sources[name] = source

    monkeypatch.setattr(_WINDOWS_CRT_PREP, "_stager_module", lambda: _STAGER)
    monkeypatch.setattr(
        _WINDOWS_CRT_PREP, "_numpy_record_entry",
        lambda _executable, _dist: {
            "distribution": "numpy", "version": "2.5.4",
            "record_entry": "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd",
            "source_sha256": _STAGER.sha256_file(pyd),
        },
    )
    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER, "_windows_file_company_name", lambda _path: "Microsoft Corporation")
    monkeypatch.setattr(_STAGER, "_windows_file_original_filename", lambda path: path.name)
    monkeypatch.setattr(
        _STAGER, "_windows_file_product_version",
        lambda path: "14.40.33810.0" if "a4c2229" in path.name else "14.44.35211.0",
    )

    dependency_map = {pyd: [crt.name]}

    def fake_dependencies(path, _dumpbin):
        return dependency_map.get(path, [])

    def fake_run(command, **_kwargs):
        assert command[1:3] == ["replace-needed", str(pyd)]
        assert command[-2:] == [crt.name, "msvcp140.dll"]
        dependency_map[pyd] = ["msvcp140.dll"]
        pyd.write_bytes(b"MZ patched NumPy extension fixture")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_WINDOWS_CRT_PREP, "_dependencies", fake_dependencies)
    monkeypatch.setattr(_WINDOWS_CRT_PREP.subprocess, "run", fake_run)
    manifest = tmp_path / "build" / "redist.json"
    prepared = _WINDOWS_CRT_PREP.prepare(
        dist, redist, manifest, dumpbin="dumpbin.exe", delvewheel="delvewheel.exe"
    )

    assert dependency_map[pyd] == ["msvcp140.dll"]
    assert not crt.exists()
    for name, relative in _WINDOWS_CRT_PREP.CRT_FILES.items():
        target = dist / relative
        assert target.read_bytes() == sources[name].read_bytes()
        assert prepared["redist_files"][name]["sha256"] == prepared["redist_files"][name]["staged_sha256"]
    assert "14.44.35211.0" == prepared["redist_files"]["msvcp140.dll"]["product_version"]
    assert prepared["numpy_patched_modules"][0]["patched_sha256"] == _STAGER.sha256_file(pyd)
    assert manifest.is_file()
    assert "C:\\" not in manifest.read_text(encoding="utf-8")


def test_windows_cpython_native_hashes_cover_dlls_and_interpreter_root(
    tmp_path, monkeypatch
):
    base = tmp_path / "managed-python"
    dlls = base / "DLLs"
    dlls.mkdir(parents=True)
    extension = dlls / "_socket.pyd"
    interpreter = base / "python312.dll"
    unrelated = dlls / "libffi-8.dll"
    extension.write_bytes(b"verified CPython extension")
    interpreter.write_bytes(b"verified CPython interpreter")
    unrelated.write_bytes(b"third-party library")

    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER.sys, "base_prefix", str(base))
    monkeypatch.setattr(
        _STAGER.sysconfig, "get_config_var",
        lambda key: str(tmp_path / f"missing-{key}"),
    )
    monkeypatch.setattr(_STAGER.importlib.metadata, "distributions", lambda: ())
    monkeypatch.setattr(_STAGER.ssl, "OPENSSL_VERSION", "test SSL runtime")

    sources = _STAGER._windows_cpython_native_sources()
    by_hash = _STAGER._python_provenance_index(set())

    assert set(sources) == {extension, interpreter}
    assert by_hash[_STAGER.sha256_file(extension)].component == "CPython runtime"
    assert by_hash[_STAGER.sha256_file(interpreter)].component == "CPython runtime"
    assert by_hash[_STAGER.sha256_file(unrelated)].component == "libffi"


def test_windows_vendor_runtime_dlls_need_exact_source_hash_and_terms(
    tmp_path, monkeypatch
):
    base = tmp_path / "managed-python"
    dlls = base / "DLLs"
    dlls.mkdir(parents=True)
    payloads = {
        "libffi-8.dll": b"libffi fixture",
        "zlib1.dll": b"zlib fixture",
        "vcruntime140.dll": b"Microsoft CRT fixture",
        "vcruntime140_1.dll": b"Microsoft CRT support fixture",
    }
    sources = {}
    for name, data in payloads.items():
        path = (base if name.startswith("vcruntime") else dlls) / name
        path.write_bytes(data)
        sources[name] = path

    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER.sys, "base_prefix", str(base))
    monkeypatch.setattr(
        _STAGER, "_windows_file_product_version",
        lambda path: {
            "zlib1.dll": "1.3.1",
            "vcruntime140.dll": "14.44.35211.0",
            "vcruntime140_1.dll": "14.44.35211.0",
        }.get(path.name),
    )
    monkeypatch.setattr(
        _STAGER, "_windows_file_original_filename",
        lambda path: path.name if path.name.startswith("vcruntime") else None,
    )
    monkeypatch.setattr(
        _STAGER, "_windows_file_company_name",
        lambda path: "Microsoft Corporation" if path.name.startswith("vcruntime") else None,
    )
    classified = _STAGER._windows_runtime_library_provenance(
        "uv-managed CPython 3.12.14 (Astral python-build-standalone distribution)"
    )
    assert len(classified) == 4
    libffi = classified[_STAGER.sha256_file(sources["libffi-8.dll"])]
    assert libffi.version == "Windows ABI 8; upstream release version not encoded"
    assert libffi.license == "MIT"
    assert classified[_STAGER.sha256_file(sources["zlib1.dll"])].version == "1.3.1"
    assert "14.44.35211.0" in classified[
        _STAGER.sha256_file(sources["vcruntime140.dll"])
    ].version
    assert classified[_STAGER.sha256_file(sources["libffi-8.dll"])].notice == (
        "LICENSES/libffi-LICENSE.txt"
    )
    assert _STAGER.sha256_file(sources["zlib1.dll"]) in classified[
        _STAGER.sha256_file(sources["zlib1.dll"])
    ].evidence
    terms = " ".join((
        _STAGER.ROOT / "packaging/licenses/Microsoft-Visual-C-Redistributable-terms.md"
    ).read_text(encoding="utf-8").split())
    assert "licensed Visual Studio users" in terms
    assert "does not record the upstream release version" in libffi.evidence


def test_windows_msvc_runtime_detection_uses_pe_identity_and_version(monkeypatch, tmp_path):
    path = tmp_path / "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
    path.write_bytes(b"MZ fixture")
    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER, "_windows_file_original_filename", lambda _: "msvcp140.dll")
    monkeypatch.setattr(_STAGER, "_windows_file_company_name", lambda _: "Microsoft Corporation")
    monkeypatch.setattr(_STAGER, "_windows_file_product_version", lambda _: "14.40.33810.0")

    result = _STAGER._windows_microsoft_runtime_provenance(path, "numpy.libs/" + path.name)

    assert result.component == "Microsoft Visual C++ Runtime"
    assert result.version == "14.40.33810.0"
    assert "OriginalFilename=msvcp140.dll" in result.evidence
    assert "Visual Studio REDIST" in result.evidence
    assert "packaged basename" in result.evidence

    monkeypatch.setattr(_STAGER, "_windows_file_company_name", lambda _: "Other Corporation")
    assert _STAGER._windows_microsoft_runtime_provenance(path, path.name) is None


def test_numpy_wheel_msvcp140_record_gets_microsoft_provenance(monkeypatch, tmp_path):
    name = "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
    path = tmp_path / "numpy.libs" / name
    path.parent.mkdir()
    path.write_bytes(b"MZ Microsoft runtime fixture")

    class Distribution:
        metadata = {"Name": "numpy", "License-Expression": "BSD-3-Clause"}
        version = "2.5.4"
        files = [Path("numpy.libs") / name]

        def locate_file(self, record):
            return path

    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER.importlib.metadata, "distributions", lambda: [Distribution()])
    monkeypatch.setattr(_STAGER, "_windows_cpython_native_sources", lambda: [])
    monkeypatch.setattr(_STAGER, "_windows_runtime_library_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER.sysconfig, "get_config_var", lambda key: str(tmp_path / "missing"))
    monkeypatch.setattr(_STAGER.ssl, "OPENSSL_VERSION", "test TLS")
    monkeypatch.setattr(_STAGER, "_windows_file_original_filename", lambda _: "msvcp140.dll")
    monkeypatch.setattr(_STAGER, "_windows_file_company_name", lambda _: "Microsoft Corporation")
    monkeypatch.setattr(_STAGER, "_windows_file_product_version", lambda _: "14.40.33810.0")

    provenance = _STAGER._python_provenance_index({"numpy"})[
        _STAGER.sha256_file(path)
    ]

    assert provenance.component == "Microsoft Visual C++ Runtime"
    assert provenance.version == "14.40.33810.0"
    assert "OriginalFilename=msvcp140.dll" in provenance.evidence
    assert "numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll" in provenance.evidence


def test_native_inventory_maps_openblas_and_kiwisolver_to_exact_notices(
    tmp_path, monkeypatch
):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    licenses.mkdir(parents=True)
    openblas = release / "numpy.libs" / "libscipy_openblas64_-fixture.dll"
    kiwi = release / "kiwisolver" / "_cext.pyd"
    kiwi_linux_abi = release / "kiwisolver" / "_cext.cpython-312-x86_64-linux-gnu.so"
    kiwi_linux_alias = release / "kiwisolver" / "_cext.so"
    openblas.parent.mkdir()
    kiwi.parent.mkdir()
    kiwi_linux_abi.parent.mkdir(exist_ok=True)
    openblas.write_bytes(b"MZ OpenBLAS payload")
    kiwi.write_bytes(b"MZ kiwisolver extension")
    kiwi_linux_abi.write_bytes(b"\x7fELF kiwisolver ABI extension")
    kiwi_linux_alias.write_bytes(b"\x7fELF kiwisolver alias extension")
    openblas_notice = licenses / "OpenBLAS-BSD-3-Clause.txt"
    lapack_notice = licenses / "OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt"
    openblas_notice.write_text("openblas complete notice", encoding="utf-8")
    lapack_notice.write_text("lapack attribution notice", encoding="utf-8")
    kiwi_notice = licenses / "Python" / "kiwisolver-1.5.1" / "LICENSE"
    kiwi_notice.parent.mkdir(parents=True)
    kiwi_notice.write_text("BSD 3-Clause license", encoding="utf-8")
    openblas_source = _STAGER.NativeProvenance(
        "Python distribution numpy", "2.5.4", "wheel metadata", "numpy wheel",
        "SHA-256 match to installed numpy RECORD entry",
    )
    kiwi_source = _STAGER.NativeProvenance(
        "Python distribution kiwisolver", "1.5.1", "bad metadata label",
        "LICENSES/Python/kiwisolver-1.5.1/", "SHA-256 match to installed kiwisolver RECORD entry",
    )
    source_index = {
        _STAGER.sha256_file(openblas): openblas_source,
        _STAGER.sha256_file(kiwi): kiwi_source,
        _STAGER.sha256_file(kiwi_linux_abi): kiwi_source,
        _STAGER.sha256_file(kiwi_linux_alias): kiwi_source,
    }
    monkeypatch.setattr(
        _STAGER, "native_payload_files",
        lambda root: [openblas, kiwi, kiwi_linux_abi, kiwi_linux_alias],
    )
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: source_index)
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "")

    rows, unresolved = _STAGER.native_component_rows(
        release, [("numpy", "2.5.4", "metadata"), ("kiwisolver", "1.5.1", "metadata")],
        "Windows CPython fixture", "OpenSSL 3.5.9", "fixture", "8.6.15", "8.6",
        licenses, "0.1.0",
    )

    assert not unresolved
    openblas_row = next(row for row in rows if "libscipy_openblas64_" in row)
    kiwi_row = next(row for row in rows if "_cext.pyd" in row)
    assert "BSD-3-Clause AND BSD-3-Clause-Attribution" in openblas_row
    assert "LICENSES/OpenBLAS-BSD-3-Clause.txt" in openblas_row
    assert "LICENSES/OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt" in openblas_row
    assert "| `kiwisolver/_cext.pyd` |" in kiwi_row
    assert "| BSD-3-Clause | LICENSES/Python/kiwisolver-1.5.1/LICENSE |" in kiwi_row
    for path in ("kiwisolver/_cext.cpython-312-x86_64-linux-gnu.so", "kiwisolver/_cext.so"):
        row = next(row for row in rows if f"`{path}`" in row)
        assert f"| `{path}` | Python distribution kiwisolver | 1.5.1 | BSD-3-Clause | LICENSES/Python/kiwisolver-1.5.1/LICENSE |" in row


def test_native_inventory_records_exact_msvc_redist_source_and_hashes(
    tmp_path, monkeypatch,
):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    licenses.mkdir(parents=True)
    payloads = {
        "numpy.libs/msvcp140.dll": b"MZ redistributable msvcp",
        "vcruntime140.dll": b"MZ redistributable vcruntime",
        "vcruntime140_1.dll": b"MZ redistributable support runtime",
    }
    redist_files = {}
    for relative, content in payloads.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        name = Path(relative).name
        digest = _STAGER.sha256_file(path)
        version = "14.40.33810.0" if name == "msvcp140.dll" else "14.44.35211.0"
        redist_files[name] = {
            "product_version": version,
            "sha256": digest,
            "staged_sha256": digest,
            "redist_source": "Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC143.CRT",
        }
    manifest_path = tmp_path / "redist-manifest.json"
    manifest = {"redist_files": redist_files, "numpy_patched_modules": []}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda _root: [release / item for item in payloads])
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "")
    monkeypatch.setattr(_STAGER, "_windows_file_company_name", lambda _path: "Microsoft Corporation")
    monkeypatch.setattr(_STAGER, "_windows_file_original_filename", lambda path: path.name)
    monkeypatch.setattr(
        _STAGER, "_windows_file_product_version",
        lambda path: "14.40.33810.0" if path.name == "msvcp140.dll" else "14.44.35211.0",
    )

    rows, unresolved = _STAGER.native_component_rows(
        release, [], "CPython Windows", "OpenSSL 3.5.9", "fixture", "8.6.15", "8.6",
        licenses, "0.1.0", msvc_redist_manifest=manifest_path,
    )
    assert not unresolved
    assert len(rows) == 3
    for row in rows:
        assert "Microsoft Visual C++ Runtime" in row
        assert "Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC143.CRT" in row
        assert "source SHA-256" in row
        assert "staged payload SHA-256" in row
        assert "C:\\" not in row


def test_native_inventory_classifies_hash_changed_numpy_extension_from_record_manifest(
    tmp_path, monkeypatch,
):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    licenses.mkdir(parents=True)
    payload = release / "numpy" / "_core" / "_multiarray_umath.pyd"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"patched extension import table")
    source_bytes = b"original NumPy wheel extension"
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    patched_hash = _STAGER.sha256_file(payload)
    license_notice = licenses / "Python" / "numpy-2.5.4" / "LICENSE.txt"
    license_notice.parent.mkdir(parents=True)
    license_notice.write_text("NumPy license", encoding="utf-8")
    manifest = {
        "redist_files": {
            name: {"sha256": "a" * 64, "staged_sha256": "a" * 64,
                  "product_version": "14.51.36247.0", "redist_source": "VS REDIST"}
            for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
        },
        "numpy_patched_modules": [{
            "archive_path": "numpy/_core/_multiarray_umath.pyd",
            "distribution": "numpy", "version": "2.5.4",
            "record_entry": "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd",
            "source_sha256": source_hash, "patched_sha256": patched_hash,
            "old_import": "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll",
            "new_import": "msvcp140.dll",
        }],
    }
    manifest_path = tmp_path / "redist-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provenance = _STAGER.NativeProvenance(
        "Python distribution numpy", "2.5.4", "BSD-3-Clause", 
        "LICENSES/Python/numpy-2.5.4/",
        "SHA-256 match to installed numpy 2.5.4 RECORD entry "
        "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd",
    )
    monkeypatch.setattr(_STAGER.sys, "platform", "win32")
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda _root: [payload])
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: {source_hash: provenance})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "")

    rows, unresolved = _STAGER.native_component_rows(
        release, [("numpy", "2.5.4", "BSD-3-Clause")], "CPython Windows",
        "OpenSSL 3.5.9", "fixture", "8.6.15", "8.6", licenses, "0.1.0",
        msvc_redist_manifest=manifest_path,
    )

    assert not unresolved
    row = rows[0]
    assert "Python distribution numpy" in row
    assert "RECORD entry" in row and source_hash in row
    assert patched_hash in row
    assert "msvcp140.dll" in row
    assert "UNRESOLVED" not in row


def test_windows_archive_validator_accepts_record_verified_numpy_crt_rewrite(tmp_path):
    archive = tmp_path / "release.zip"
    root = "MiLaS-windows-x64"
    pyd_path = "numpy/_core/_multiarray_umath.pyd"
    pocketfft_path = "numpy/fft/_pocketfft_umath.pyd"
    payloads = {
        pyd_path: b"patched NumPy payload",
        pocketfft_path: b"patched PocketFFT payload",
        "numpy.libs/msvcp140.dll": b"msvcp redistributable",
        "vcruntime140.dll": b"vcruntime redistributable",
        "vcruntime140_1.dll": b"vcruntime support redistributable",
    }
    with zipfile.ZipFile(archive, "w") as handle:
        for relative, data in payloads.items():
            handle.writestr(f"{root}/{relative}", data)
        handle.writestr(f"{root}/LICENSES/Microsoft-Visual-C-Redistributable-terms.md", "terms")
        handle.writestr(f"{root}/LICENSES/Python/numpy-2.5.4/LICENSE.txt", "numpy")
    patched_hash = hashlib.sha256(payloads[pyd_path]).hexdigest()
    original_hash = "b" * 64
    rows = []
    for relative in (pyd_path, pocketfft_path):
        patched_hash = hashlib.sha256(payloads[relative]).hexdigest()
        rows.append((
            f"`{relative}`", "Python distribution numpy", "2.5.4", "BSD-3-Clause",
            "LICENSES/Python/numpy-2.5.4/LICENSE.txt",
            "NumPy import table changed from msvcp140-0123456789abcdef0123456789abcdef.dll "
            f"to msvcp140.dll; original bytes match NumPy 2.5.4 RECORD entry {relative} "
            f"with source SHA-256 {original_hash}; patched payload SHA-256 {patched_hash}",
        ))
    for relative in sorted(set(payloads) - {pyd_path, pocketfft_path}):
        digest = hashlib.sha256(payloads[relative]).hexdigest()
        rows.append((
            f"`{relative}`", "Microsoft Visual C++ Runtime", "14.51.36247.0",
            "Microsoft Visual C++ Redistributable terms",
            "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
            "PE CompanyName=Microsoft Corporation; OriginalFilename="
            f"{Path(relative).name}; ProductVersion=14.51.36247.0; "
            "Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC145.CRT; "
            f"source SHA-256 {digest}; staged payload SHA-256 {digest}",
        ))
    actual_native = set(payloads)
    members = {
        f"{root}/{relative}" for relative in payloads
    } | {
        f"{root}/LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
        f"{root}/LICENSES/Python/numpy-2.5.4/LICENSE.txt",
    }
    errors = []

    _ARCHIVE_VALIDATOR._validate_windows_native_licenses(
        archive, root, actual_native, rows, members, errors,
    )

    assert errors == [], errors


def test_native_inventory_forwards_python_source_to_provenance_index(
    tmp_path, monkeypatch
):
    release = tmp_path / "release"
    release.mkdir()
    payload = release / "libffi-8.dll"
    payload.write_bytes(b"MZ fixture payload")
    source = "uv 0.13.6; preinstalled CPython 3.12.14"
    provenance = _STAGER.NativeProvenance(
        "libffi", "Windows ABI 8; upstream release version not encoded", "MIT",
        "LICENSES/libffi-LICENSE.txt", "fixture exact hash",
    )
    observed = {}

    def provenance_index(*args):
        observed["python_source"] = args[-1]
        return {_STAGER.sha256_file(payload): provenance}

    monkeypatch.setattr(_STAGER, "native_payload_files", lambda root: [payload])
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_native_provenance_index", provenance_index)
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "")

    rows, unresolved = _STAGER.native_component_rows(
        release, [], source, "OpenSSL 3.5.9", "fixture", "8.6.15", "8.6",
        release / "LICENSES", "0.1.0",
    )

    assert observed["python_source"] == source
    assert not unresolved
    assert len(rows) == 1
    assert "libffi" in rows[0] and "MIT" in rows[0]


def test_dependency_licenses_use_pep639_metadata_when_importlib_files_are_missing(
    tmp_path, monkeypatch, capsys
):
    class Metadata(Message):
        def __init__(self):
            super().__init__()
            self["Name"] = "Example-Wheel"
            self["Version"] = "1.2.3"
            self["License-File"] = "COPYING.txt"

    class Distribution:
        metadata = Metadata()
        version = "1.2.3"
        files = None

        def read_text(self, filename):
            if filename == "RECORD":
                return None
            if filename == "licenses/COPYING.txt":
                return "Example license text\n"
            return None

    monkeypatch.setattr(
        _STAGER.importlib.metadata, "distributions", lambda: [Distribution()]
    )
    destination = tmp_path / "LICENSES"

    copied = _STAGER.copy_dist_license_files(destination, {"example_wheel"})

    license_path = destination / "Python" / "Example-Wheel-1.2.3" / "COPYING.txt"
    assert copied == ["Python/Example-Wheel-1.2.3/COPYING.txt"]
    assert license_path.read_text(encoding="utf-8") == "Example license text\n"
    output = capsys.readouterr().out
    assert "copied 1 license file(s) from 1 of 1" in output
    assert "License-File=['COPYING.txt']" in output


def test_dependency_licenses_fall_back_to_wheel_record_paths(
    tmp_path, monkeypatch, capsys
):
    class Metadata(Message):
        def __init__(self):
            super().__init__()
            self["Name"] = "Record-Wheel"
            self["Version"] = "4.5.6"

    source = tmp_path / "site-packages" / "record_wheel-4.5.6.dist-info" / "licenses" / "LICENSE.md"
    source.parent.mkdir(parents=True)
    source.write_text("Wheel RECORD license\n", encoding="utf-8")

    class Distribution:
        metadata = Metadata()
        version = "4.5.6"
        files = None

        def read_text(self, filename):
            if filename == "RECORD":
                return "record_wheel-4.5.6.dist-info\\licenses\\LICENSE.md,,\n"
            return None

        def locate_file(self, relative):
            return tmp_path / "site-packages" / str(relative).replace("\\", "/")

    monkeypatch.setattr(
        _STAGER.importlib.metadata, "distributions", lambda: [Distribution()]
    )
    destination = tmp_path / "LICENSES"

    copied = _STAGER.copy_dist_license_files(destination, {"record-wheel"})

    license_path = destination / "Python" / "Record-Wheel-4.5.6" / "LICENSE.md"
    assert copied == ["Python/Record-Wheel-4.5.6/LICENSE.md"]
    assert license_path.read_text(encoding="utf-8") == "Wheel RECORD license\n"
    output = capsys.readouterr().out
    assert "RECORD entries=1" in output


def test_staged_python_license_check_accepts_windows_path_return_values(tmp_path):
    licenses = tmp_path / "LICENSES"
    expected = licenses / "Python" / "matplotlib-3.11.2" / "LICENSE.txt"
    expected.parent.mkdir(parents=True)
    expected.write_text("Matplotlib license text\n", encoding="utf-8")

    # Path.relative_to() stringifies with backslashes on Windows, whereas the
    # staging check had only accepted the POSIX spelling "Python/...".
    returned = [r"Python\matplotlib-3.11.2\LICENSE.txt"]
    _STAGER.verify_staged_python_license_files(licenses, returned)

    try:
        _STAGER.verify_staged_python_license_files(
            licenses, [r"Python\matplotlib-3.11.2\MISSING.txt"]
        )
    except RuntimeError as exc:
        assert "missing or empty staged file" in str(exc)
    else:
        raise AssertionError("license staging accepted a missing copied file")


def test_stager_finds_tk_runtime_payloads_on_linux_and_windows(tmp_path):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    (deployment / "tcl9.0").mkdir(parents=True)
    release.mkdir()
    (release / "_tkinter.cpython-312-x86_64-linux-gnu.so").touch()
    (release / "libtcl9.0.so").touch()
    (release / "tcl9.0").mkdir()
    (release / "tcl9.0" / "init.tcl").touch()
    (release / "tk9.0.dll").touch()

    findings = _STAGER.find_tk_payloads(deployment, release)

    assert any("_tkinter.cpython" in item for item in findings)
    assert any("libtcl9.0.so" in item for item in findings)
    assert any("tcl9.0/init.tcl" in item for item in findings)
    assert any("tk9.0.dll" in item for item in findings)
    assert all(item.startswith("release package/") for item in findings)


def test_stager_finds_tcl_tk_script_archives(tmp_path):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    deployment.mkdir(parents=True)
    release.mkdir()
    (release / "libtcl9.0.4.zip").touch()
    (release / "libtk9.0.4.zip").touch()

    findings = _STAGER.find_tk_payloads(deployment, release)

    assert any("libtcl9.0.4.zip" in item for item in findings)
    assert any("libtk9.0.4.zip" in item for item in findings)


def test_stager_does_not_treat_non_tk_payload_as_tcl_tk(tmp_path):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    deployment.mkdir()
    release.mkdir()
    (deployment / "readme.txt").touch()
    (release / "Qt6Core.dll").touch()

    assert _STAGER.find_tk_payloads(deployment, release) == []


def test_stager_detects_platform_specific_openssl_filenames(tmp_path):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    deployment.mkdir()
    release.mkdir()
    (deployment / "libcrypto.so.3").touch()
    (deployment / "libssl.so.3").touch()
    (release / "libcrypto-3-x64.dll").touch()
    (release / "libssl-3-x64.dll").touch()

    found = _STAGER.find_openssl_payloads(deployment, release)

    assert any("libssl-3-x64.dll" in item for item in found)
    assert len(found) == 2


def test_stage_writes_required_inventory_and_openssl_license_files(tmp_path, monkeypatch):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    (deployment / "payload").mkdir(parents=True)
    (release / "tcl8.6").mkdir(parents=True)
    for filename in (
        "_tkinter.cpython-312-x86_64-linux-gnu.so",
        "libtcl8.6.so",
        "libssl.so.3",
        "libcrypto.so.3",
    ):
        (release / filename).write_bytes(b"\x7fELFtest-native-payload")
    (release / "tcl8.6" / "init.tcl").touch()

    monkeypatch.setattr(_STAGER, "lock_runtime_distributions", lambda: [("matplotlib", "3.11.2", "BSD-3-Clause")])

    def copy_test_license_files(destination, package_names):
        target = destination / "Python" / "matplotlib-3.11.2" / "LICENSE.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test license", encoding="utf-8")
        return [str(target.relative_to(destination))]

    monkeypatch.setattr(_STAGER, "copy_dist_license_files", copy_test_license_files)
    monkeypatch.setattr(_STAGER, "tkinter_runtime_versions", lambda: ("8.6.14", "8.6"))
    monkeypatch.setattr(
        _STAGER,
        "_native_provenance_index",
        lambda licenses, hashes, names=None, runtime_names=None, payloads=None, python_source="": {
            digest: _STAGER.NativeProvenance(
                "Fixture component", "1.2.3", "MIT", "LICENSES/Python/matplotlib-3.11.2/",
                "fixture hash provenance",
            )
            for digest in hashes
        },
    )

    _STAGER.stage(deployment, release, "local pyenv CPython 3.12.14 at /build-host/python")

    assert (release / "BUNDLED_RUNTIME_INVENTORY.md").is_file()
    assert (release / "LICENSES" / "Tcl-Tk-runtime-files.txt").is_file()
    assert (release / "LICENSES" / "OpenSSL-Apache-2.0.txt").is_file()
    openblas_license = release / "LICENSES" / "OpenBLAS-BSD-3-Clause.txt"
    lapack_license = release / "LICENSES" / "OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt"
    assert openblas_license.is_file()
    assert lapack_license.is_file()
    assert "Copyright (c) 2011-2014, The OpenBLAS Project" in openblas_license.read_text(encoding="utf-8")
    assert "BSD-3-Clause-Attribution" in lapack_license.read_text(encoding="utf-8")
    inventory = (release / "BUNDLED_RUNTIME_INVENTORY.md").read_text(encoding="utf-8")
    assert "OpenSSL runtime" in inventory
    assert _STAGER.ssl.OPENSSL_VERSION in inventory
    assert "local pyenv CPython 3.12.14" in inventory
    assert "Apache-2.0" in inventory
    assert "libssl.so.3" in inventory
    assert "/build-host" not in inventory
    assert "Archive-relative file" in inventory
    assert "Unresolved native payload count: 0." in inventory
    (release / "LICENSE").write_text("MiLaS license fixture", encoding="utf-8")
    (release / "NOTICE").write_text("MiLaS notice fixture", encoding="utf-8")
    archive = tmp_path / "staged-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(release, arcname="MiLaS-linux-x86_64")
    _ARCHIVE_VALIDATOR.validate_archive(archive, "MiLaS-linux-x86_64")


def _write_valid_inventory(
    root: Path,
    native_path: str = "MiLaS.bin",
    extra: str = "",
    *,
    component: str = "MiLaS executable",
    version: str = "1.0",
):
    (root / "BUNDLED_RUNTIME_INVENTORY.md").write_text(
        "# Bundled runtime inventory\n\n"
        + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
        + "| --- | --- | --- | --- | --- | --- |\n"
        + f"| `{native_path}` | {component} | {version} | PolyForm Noncommercial License 1.0.0 | LICENSE; NOTICE | generated fixture provenance{extra} |\n\n"
        + "Unresolved native payload count: 0.\n",
        encoding="utf-8",
    )


def _make_representative_tar(tmp_path: Path, *, inventory_path: str | None = "MiLaS.bin", extra: str = "") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    package_root = "MiLaS-linux-x86_64"
    release = tmp_path / package_root
    for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    (release / "LICENSE").write_text("fixture MiLaS license\n", encoding="utf-8")
    (release / "NOTICE").write_text("fixture notice\n", encoding="utf-8")
    (release / "MiLaS.bin").write_bytes(b"\x7fELFfixture executable")
    _write_valid_inventory(release, inventory_path or "", extra)

    archive = tmp_path / "representative-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(release, arcname=package_root)
    return archive


def test_representative_tar_archive_inventory_matches_native_payload(tmp_path):
    package_root = "MiLaS-linux-x86_64"
    archive = _make_representative_tar(tmp_path)

    verified = _ARCHIVE_VALIDATOR.validate_archive(archive, package_root)

    assert f"{package_root}/BUNDLED_RUNTIME_INVENTORY.md" in verified
    with tarfile.open(archive, "r:gz") as tar:
        assert f"{package_root}/BUNDLED_RUNTIME_INVENTORY.md" in tar.getnames()


def test_archive_validation_rejects_native_payload_omitted_from_inventory(tmp_path):
    archive = _make_representative_tar(tmp_path, inventory_path="other.bin")

    try:
        _ARCHIVE_VALIDATOR.validate_archive(archive, "MiLaS-linux-x86_64")
    except RuntimeError as exc:
        assert "Native archive payload missing from inventory" in str(exc)
    else:
        raise AssertionError("archive validator accepted an omitted ELF payload")


def test_archive_validation_rejects_absolute_path_and_unresolved_component(tmp_path):
    archive = _make_representative_tar(tmp_path, extra="; /home/build-user/python")

    try:
        _ARCHIVE_VALIDATOR.validate_archive(archive, "MiLaS-linux-x86_64")
    except RuntimeError as exc:
        assert "absolute build-machine path" in str(exc)
    else:
        raise AssertionError("archive validator accepted an absolute build path")

    archive = _make_representative_tar(
        tmp_path / "unresolved", extra="; unresolved component marker UNRESOLVED"
    )
    try:
        _ARCHIVE_VALIDATOR.validate_archive(archive, "MiLaS-linux-x86_64")
    except RuntimeError as exc:
        assert "lacks verified classification" in str(exc)
    else:
        raise AssertionError("archive validator accepted an unresolved native component")


def test_archive_validation_rejects_duplicated_debian_package_version(tmp_path):
    archive = _make_representative_tar(tmp_path)
    root = "MiLaS-linux-x86_64"
    release = tmp_path / "duplicated-version" / root
    release.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as source:
        for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
            target = release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(f"{root}/{relative}").read())
    (release / "LICENSE").write_text("fixture", encoding="utf-8")
    (release / "NOTICE").write_text("fixture", encoding="utf-8")
    (release / "MiLaS.bin").write_bytes(b"\x7fELFfixture executable")
    _write_valid_inventory(
        release,
        component="Debian package libdemo1",
        version="1.0.8-5ubuntu0.11.0.8-5ubuntu0.1",
    )
    bad_archive = tmp_path / "duplicated-version.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as target:
        target.add(release, arcname=root)

    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_archive, root)
    except RuntimeError as exc:
        assert "Debian package version is duplicated" in str(exc)
    else:
        raise AssertionError("archive validator accepted a duplicated Debian version")


def test_nuitka_report_maps_destination_to_verified_source_metadata(tmp_path, monkeypatch):
    source = tmp_path / "site-packages" / "sample.libs" / "libsample.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture native")
    provenance = _STAGER.NativeProvenance(
        "Python distribution Sample", "2.3.4", "BSD-3-Clause",
        "LICENSES/Python/Sample-2.3.4/", "wheel RECORD source evidence",
    )
    monkeypatch.setattr(
        _STAGER, "_python_provenance_paths", lambda runtime_names: {str(source.resolve()): provenance}
    )
    report = tmp_path / "nuitka-report.xml"
    report.write_text(
        '<nuitka-compilation-report><included_dll '
        'dest_path="sample.libs/libsample.so" '
        f'source_path="{source}" /></nuitka-compilation-report>',
        encoding="utf-8",
    )

    mapped = _STAGER._nuitka_report_provenance(
        report, tmp_path / "release" / "LICENSES", {"sample"}
    )

    assert mapped["sample.libs/libsample.so"].component == "Python distribution Sample"
    assert mapped["sample.libs/libsample.so"].version == "2.3.4"


def test_nuitka_top_level_cpython_extensions_map_even_when_standalone_subdir_differs(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "python" / "lib-dynload"
    source_root.mkdir(parents=True)
    source_by_name = {}
    provenance = _STAGER.NativeProvenance(
        "CPython runtime", "3.12.14", "PSF-2.0",
        "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
        "Nuitka report source resolves to CPython standard-library extension",
    )
    modules = ("_ssl", "_hashlib", "_tkinter", "_ctypes", "_bz2", "_lzma")
    report_records = []
    for module in modules:
        source = source_root / f"{module}.cpython-312-x86_64-linux-gnu.so"
        source.touch()
        source_by_name[str(source.resolve())] = provenance
        report_records.append(
            f'<module name="{module}" kind="Extension" source_path="{source}" />'
        )
    monkeypatch.setattr(
        _STAGER, "_python_provenance_paths", lambda runtime_names: source_by_name
    )
    report = tmp_path / "nuitka-report.xml"
    report.write_text(
        "<nuitka-compilation-report>" + "".join(report_records)
        + "</nuitka-compilation-report>",
        encoding="utf-8",
    )
    mapped = _STAGER._nuitka_report_provenance(
        report, tmp_path / "release" / "LICENSES", set()
    )

    for module in modules:
        assert mapped[f"module:{module}"].component == "CPython runtime"
        assert mapped[f"module:{module}"].license == "PSF-2.0"

    # Verify the real inventory resolver associates each report-backed
    # top-level module even if Nuitka places the destination in lib-dynload/.
    staged = tmp_path / "release"
    (staged / "lib-dynload").mkdir(parents=True)
    destinations = []
    for module in modules:
        target = staged / "lib-dynload" / f"{module}.cpython-312-x86_64-linux-gnu.so"
        target.write_bytes(b"\x7fELF" + module.encode())
        destinations.append(target)
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda root: destinations)
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: mapped)
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "")
    rows, unresolved = _STAGER.native_component_rows(
        staged, [], "CPython fixture", "OpenSSL 3.0.2", "fixture linkage",
        "8.6.14", "8.6", staged / "LICENSES", "1.0.0",
    )
    assert not unresolved
    assert len(rows) == len(modules)
    assert all("CPython runtime" in row and "PSF-2.0" in row for row in rows)


def test_system_library_hash_provenance_accepts_versioned_ldconfig_target(
    tmp_path, monkeypatch
):
    release = tmp_path / "release"
    release.mkdir()
    payload = release / "libX11.so.6.4.0"
    payload.write_bytes(b"ELF fixture for exact hash")
    copyright_file = tmp_path / "copyright"
    copyright_file.write_text("Format: fixture\nLicense: MIT\n", encoding="utf-8")
    monkeypatch.setattr(_STAGER, "_python_provenance_index", lambda names, python_source="": {})
    monkeypatch.setattr(
        _STAGER, "_host_library_candidates", lambda: [tmp_path / "libX11.so.6"]
    )
    alias = tmp_path / "libX11.so.6"
    alias.write_bytes(payload.read_bytes())
    monkeypatch.setattr(
        _STAGER, "_dpkg_package_for_path",
        lambda path: ("libx11-6", "2:1.8.4-2ubuntu0.3", copyright_file, "MIT"),
    )

    index = _STAGER._native_provenance_index(
        tmp_path / "release" / "LICENSES", {_STAGER.sha256_file(payload)},
        {payload.name}, set(),
    )

    provenance = index[_STAGER.sha256_file(payload)]
    assert provenance.component == "Debian package libx11-6"
    assert provenance.version == "2:1.8.4-2ubuntu0.3"
    assert provenance.license == "MIT"
    assert provenance.notice.startswith("LICENSES/Linux-packages/")
    assert (tmp_path / "release" / provenance.notice).is_file()


def test_native_inventory_sanitizes_all_provenance_paths_and_records_elf_links(
    tmp_path, monkeypatch
):
    release = tmp_path / "release"
    release.mkdir()
    extension = release / "_ssl.cpython-312-x86_64-linux-gnu.so"
    extension.write_bytes(b"\x7fELFfixture module")
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda root: [extension])
    monkeypatch.setattr(_STAGER, "ssl", type("SSL", (), {"OPENSSL_VERSION": "OpenSSL 3.0.2"}))
    monkeypatch.setattr(
        _STAGER, "_nuitka_report_provenance",
        lambda *args: {"module:_ssl": _STAGER.NativeProvenance(
            "CPython runtime", "3.12.14", "PSF-2.0",
            "LICENSES/Python-PSF-2.0-CPython-3.12.14.txt",
            "source was /build-host/python/lib-dynload/_ssl.so",
        )},
    )
    monkeypatch.setattr(
        _STAGER, "_native_provenance_index", lambda *args: {}
    )
    monkeypatch.setattr(
        _STAGER, "_elf_linkage_evidence",
        lambda path, root: "ELF dependencies: libssl.so.3 (bundled as `libssl.so.3`); libcrypto.so.3 (bundled as `libcrypto.so.3`)",
    )
    rows, unresolved = _STAGER.native_component_rows(
        release, [], "interpreter at /build-host/python", "OpenSSL 3.0.2",
        "OpenSSL linked through /build-host/python", "8.6.14", "8.6",
        release / "LICENSES", "1.0.0",
    )

    assert not unresolved
    row = rows[0]
    assert "CPython runtime" in row and "PSF-2.0" in row
    assert "libssl.so.3" in row and "libcrypto.so.3" in row
    assert "/build-host" not in row
    assert "[absolute path omitted]" in row


def test_libquadmath_source_is_verified_and_overrides_numpy_aggregate_license(
    tmp_path, monkeypatch
):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    source = release / "SOURCE"
    licenses.mkdir(parents=True)
    source.mkdir()
    payload = release / "libquadmath-renamed.so.0.0.0"
    payload.write_bytes(b"\x7fELF fixture payload")
    (source / _STAGER.LIBQUADMATH_SOURCE_NAME).write_bytes(b"fixture source RPM")
    consumer = release / "libgfortran-fixture.so.5"
    consumer.write_bytes(b"\x7fELF fixture consumer")
    actual_source_digest = _STAGER.sha256_file(source / _STAGER.LIBQUADMATH_SOURCE_NAME)
    (licenses / "GNU-LGPL-2.1.txt").write_bytes(
        (_STAGER.STATIC_LICENSES / "GNU-LGPL-2.1.txt").read_bytes()
    )
    monkeypatch.setattr(_STAGER, "LIBQUADMATH_SOURCE_SHA256", actual_source_digest)
    monkeypatch.setattr(
        _STAGER, "elf_build_id",
        lambda path: _STAGER.LIBQUADMATH_BUILD_ID if path == payload else None,
    )
    monkeypatch.setattr(
        _STAGER, "native_payload_files", lambda root: [payload, consumer]
    )
    monkeypatch.setattr(
        _STAGER, "elf_dynamic_metadata",
        lambda path: (
            ("libquadmath-renamed.so.0.0.0", [], [])
            if path == payload else (None, ["libquadmath-renamed.so.0.0.0"], ["$ORIGIN"])
        ),
    )

    found = _STAGER.stage_libquadmath_source(release, licenses)

    assert found == [payload]
    manifest_path = source / "LIBQUADMATH-SOURCE.md"
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "gcc-8.5.0-28.el8_10.alma.1.src.rpm" in manifest
    assert actual_source_digest in manifest
    assert _STAGER.LIBQUADMATH_BUILD_ID in manifest
    assert _STAGER.sha256_file(payload) in manifest
    assert "DT_NEEDED" in manifest
    assert "$ORIGIN" in manifest
    assert "## Source evidence status" in manifest
    assert "hash-verified during staging" in manifest
    assert "newly built Linux release archive still needs to pass" not in manifest
    assert "/workspace/" not in manifest

    numpy_provenance = _STAGER.NativeProvenance(
        "Python distribution numpy", "2.5.4", "BSD-3-Clause",
        "LICENSES/Python/numpy-2.5.4/", "fixture wheel metadata",
    )
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda root: [payload])
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: {
        _STAGER.sha256_file(payload): numpy_provenance
    })
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "fixture ELF linkage")
    rows, unresolved = _STAGER.native_component_rows(
        release, [("numpy", "2.5.4", "BSD-3-Clause")], "Python fixture",
        "OpenSSL 3.0.2", "fixture", "8.6.14", "8.6", licenses, "1.0.0",
    )
    assert not unresolved
    assert len(rows) == 1
    assert "GCC libquadmath (AlmaLinux runtime RPM)" in rows[0]
    assert "LGPL-2.1-or-later" in rows[0]
    assert "BSD-3-Clause" not in rows[0]
    assert "GNU-LGPL-2.1.txt" in rows[0]
    assert "SOURCE/gcc-8.5.0-28.el8_10.alma.1.src.rpm" in rows[0]


def test_libgfortran_source_and_inventory_are_separate_from_numpy(tmp_path, monkeypatch):
    release = tmp_path / "release"
    licenses = release / "LICENSES"
    source = release / "SOURCE"
    licenses.mkdir(parents=True)
    source.mkdir()
    payload = release / "libgfortran-83c28eba-468e71e5.so.5.0.0"
    payload.write_bytes(b"ELF libgfortran fixture")
    (source / _STAGER.LIBGFORTRAN_SOURCE_NAME).write_bytes(b"fixture GCC source RPM")
    for license_name in ("GPL-3.0.txt", "GCC-Runtime-Library-Exception-3.1.txt"):
        target = licenses / license_name
        target.write_bytes((_STAGER.STATIC_LICENSES / license_name).read_bytes())
    binary_digest = _STAGER.sha256_file(payload)
    source_digest = _STAGER.sha256_file(source / _STAGER.LIBGFORTRAN_SOURCE_NAME)
    monkeypatch.setattr(_STAGER, "LIBGFORTRAN_BINARY_SHA256", binary_digest)
    monkeypatch.setattr(_STAGER, "LIBGFORTRAN_SOURCE_SHA256", source_digest)
    monkeypatch.setattr(_STAGER, "LIBGFORTRAN_GPL_SHA256", _STAGER.sha256_file(licenses / "GPL-3.0.txt"))
    monkeypatch.setattr(_STAGER, "LIBGFORTRAN_EXCEPTION_SHA256", _STAGER.sha256_file(licenses / "GCC-Runtime-Library-Exception-3.1.txt"))
    monkeypatch.setattr(_STAGER, "native_payload_files", lambda _root: [payload])
    monkeypatch.setattr(_STAGER, "elf_build_id", lambda _path: _STAGER.LIBGFORTRAN_BUILD_ID)

    assert _STAGER.stage_libgfortran_source(release, licenses) == [payload]
    manifest = (source / "LIBGFORTRAN-SOURCE.md").read_text(encoding="utf-8")
    assert "8.5.0-28.el8_10.alma.1" in manifest
    assert "GCC `8.5.0`" in manifest
    assert "libgfortran/runtime/error.c" in manifest
    assert "COPYING3" in manifest and "COPYING.RUNTIME" in manifest
    assert binary_digest in manifest and source_digest in manifest

    numpy_provenance = _STAGER.NativeProvenance(
        "Python distribution numpy", "2.5.4", "BSD-3-Clause",
        "LICENSES/Python/numpy-2.5.4/", "fixture NumPy metadata",
    )
    monkeypatch.setattr(_STAGER, "_native_provenance_index", lambda *args: {binary_digest: numpy_provenance})
    monkeypatch.setattr(_STAGER, "_nuitka_report_provenance", lambda *args: {})
    monkeypatch.setattr(_STAGER, "_elf_linkage_evidence", lambda *args: "GCC runtime fixture")
    rows, unresolved = _STAGER.native_component_rows(
        release, [("numpy", "2.5.4", "BSD-3-Clause")], "Python fixture",
        "OpenSSL 3.0.2", "fixture", "8.6", "8.6", licenses, "0.1.0",
    )
    assert not unresolved
    assert len(rows) == 1
    row = rows[0]
    assert "GCC libgfortran (AlmaLinux GCC source RPM)" in row
    assert "GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1" in row
    assert "BSD-3-Clause" not in row and "Python distribution numpy" not in row
    assert "LICENSES/GPL-3.0.txt" in row
    assert "LICENSES/GCC-Runtime-Library-Exception-3.1.txt" in row
    assert "SOURCE/LIBGFORTRAN-SOURCE.md" in row


def _elf64_with_gnu_build_id(build_id: str) -> bytes:
    import struct

    descriptor = bytes.fromhex(build_id)
    note = struct.pack("<III", 4, len(descriptor), 3) + b"GNU\0" + descriptor
    note += b"\0" * ((-len(note)) % 4)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 16, 3)
    struct.pack_into("<H", header, 18, 62)
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<H", header, 52, 64)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, 1)
    phdr = struct.pack("<IIQQQQQQ", 4, 0, 120, 0, 0, len(note), len(note), 4)
    return bytes(header) + phdr + note


def test_linux_archive_requires_matching_libquadmath_source_and_hashes(
    tmp_path, monkeypatch
):
    import hashlib

    root = "MiLaS-linux-x86_64"
    release = tmp_path / root
    for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    (release / "LICENSE").write_text("MiLaS license", encoding="utf-8")
    (release / "NOTICE").write_text("MiLaS notice", encoding="utf-8")
    (release / "README.md").write_text(
        "Linux runtime uses libquadmath and libgfortran. "
        "Source: gcc-8.5.0-28.el8_10.alma.1.src.rpm.\n", encoding="utf-8"
    )
    (release / "THIRD_PARTY_NOTICES.md").write_text(
        "Linux GCC runtime: libquadmath and libgfortran; "
        "gcc-8.5.0-28.el8_10.alma.1.src.rpm.\n", encoding="utf-8"
    )
    binary_rel = "renamed-native-runtime.so.0"
    binary = _elf64_with_gnu_build_id(_ARCHIVE_VALIDATOR.LIBQUADMATH_BUILD_ID)
    (release / binary_rel).write_bytes(binary)
    source_data = b"representative pinned source RPM bytes"
    source_path = release / _ARCHIVE_VALIDATOR.LIBQUADMATH_SOURCE
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source_data)
    license_data = b"representative complete LGPL text bytes"
    license_path = release / _ARCHIVE_VALIDATOR.LIBQUADMATH_LICENSE
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_bytes(license_data)
    permission_path = release / _ARCHIVE_VALIDATOR.LGPL_ADDITIONAL_PERMISSION
    permission_path.write_text(
        "modify the MiLaS work for the recipient's own use; reverse engineer "
        "only to debug modifications. This does not authorize commercial use; "
        "distribution remains limited and all other terms continue to apply.",
        encoding="utf-8",
    )
    source_digest = hashlib.sha256(source_data).hexdigest()
    license_digest = hashlib.sha256(license_data).hexdigest()
    monkeypatch.setattr(
        _ARCHIVE_VALIDATOR, "LIBQUADMATH_SOURCE_SHA256", source_digest
    )
    monkeypatch.setattr(
        _ARCHIVE_VALIDATOR, "LIBQUADMATH_LICENSE_SHA256", license_digest
    )
    binary_digest = hashlib.sha256(binary).hexdigest()
    manifest_path = release / _ARCHIVE_VALIDATOR.LIBQUADMATH_MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "# libquadmath source and binary provenance\n\n"
        f"- Source RPM SHA-256: `{source_digest}`\n"
        f"- Binary RPM SHA-256: `{_ARCHIVE_VALIDATOR.LIBQUADMATH_BINARY_RPM_SHA256}`\n"
        f"- Original binary SHA-256: `{_ARCHIVE_VALIDATOR.LIBQUADMATH_ORIGINAL_BINARY_SHA256}`\n"
        f"- Complete LGPL text SHA-256: `{license_digest}`\n\n"
        "- Section 6 user rights permit modification and reverse engineering to debug modifications to libquadmath.\n"
        "- ELF SONAME: `libquadmath-fixture.so.0.0.0`\n"
        "- Direct bundled consumers verified from ELF `DT_NEEDED` and runtime-path entries:\n"
        "  - `libgfortran-fixture.so.5` requests the SONAME and has `$ORIGIN` in RPATH.\n"
        f"### `{binary_rel}`\n\n"
        f"- Archive-relative binary: `{binary_rel}`\n"
        f"- SHA-256 of this staged binary: `{binary_digest}`\n"
        f"- ELF build ID: `{_ARCHIVE_VALIDATOR.LIBQUADMATH_BUILD_ID}`\n",
        encoding="utf-8",
    )
    inventory = (
        "# Bundled runtime inventory\n\n"
        + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
        + "| --- | --- | --- | --- | --- | --- |\n"
        + f"| `{binary_rel}` | GCC libquadmath (AlmaLinux runtime RPM) | 8.5.0-28.el8_10.alma.1 (GCC 8.5.0) | LGPL-2.1-or-later | {_ARCHIVE_VALIDATOR.LIBQUADMATH_LICENSE}; {_ARCHIVE_VALIDATOR.LIBQUADMATH_SOURCE}; {_ARCHIVE_VALIDATOR.LIBQUADMATH_MANIFEST} | build ID { _ARCHIVE_VALIDATOR.LIBQUADMATH_BUILD_ID }; original { _ARCHIVE_VALIDATOR.LIBQUADMATH_ORIGINAL_BINARY_SHA256 }; binary RPM { _ARCHIVE_VALIDATOR.LIBQUADMATH_BINARY_RPM_SHA256 }; source RPM { _ARCHIVE_VALIDATOR.LIBQUADMATH_SOURCE_SHA256 } |\n\n"
        + "Unresolved native payload count: 0.\n"
    )
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(inventory, encoding="utf-8")
    archive = tmp_path / "libquadmath-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(release, arcname=root)

    verified = _ARCHIVE_VALIDATOR.validate_archive(archive, root)
    assert f"{root}/{_ARCHIVE_VALIDATOR.LIBQUADMATH_SOURCE}" in verified
    assert f"{root}/{_ARCHIVE_VALIDATOR.LIBQUADMATH_MANIFEST}" in verified
    assert f"{root}/{_ARCHIVE_VALIDATOR.LGPL_ADDITIONAL_PERMISSION}" in verified

    bad_source = release / _ARCHIVE_VALIDATOR.LIBQUADMATH_SOURCE
    bad_source.write_bytes(b"wrong source RPM")
    bad_archive = tmp_path / "bad-libquadmath-release.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        tar.add(release, arcname=root)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_archive, root)
    except RuntimeError as exc:
        assert "source RPM does not match" in str(exc)
    else:
        raise AssertionError("archive validator accepted a mismatched libquadmath source")


def test_linux_archive_requires_separate_verified_libgfortran_evidence(tmp_path, monkeypatch):
    import hashlib

    root = "MiLaS-linux-x86_64"
    release = tmp_path / root
    for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    (release / "LICENSE").write_text("MiLaS license", encoding="utf-8")
    (release / "NOTICE").write_text("MiLaS notice", encoding="utf-8")
    (release / "README.md").write_text(
        "Linux runtime uses libquadmath and libgfortran. "
        "Source: gcc-8.5.0-28.el8_10.alma.1.src.rpm.\n", encoding="utf-8"
    )
    (release / "THIRD_PARTY_NOTICES.md").write_text(
        "Linux GCC runtime: libquadmath and libgfortran; "
        "gcc-8.5.0-28.el8_10.alma.1.src.rpm.\n", encoding="utf-8"
    )
    binary_rel = "libgfortran-fixture.so.5"
    binary = _elf64_with_gnu_build_id(_ARCHIVE_VALIDATOR.LIBGFORTRAN_BUILD_ID)
    (release / binary_rel).write_bytes(binary)
    binary_digest = hashlib.sha256(binary).hexdigest()
    source_data = b"pinned GCC source RPM fixture"
    source_path = release / _ARCHIVE_VALIDATOR.LIBGFORTRAN_SOURCE
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_data)
    source_digest = hashlib.sha256(source_data).hexdigest()
    license_data = {
        _ARCHIVE_VALIDATOR.LIBGFORTRAN_GPL: b"complete GPL fixture",
        _ARCHIVE_VALIDATOR.LIBGFORTRAN_EXCEPTION: b"complete GCC exception fixture",
    }
    for relative, payload in license_data.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    gpl_digest = hashlib.sha256(license_data[_ARCHIVE_VALIDATOR.LIBGFORTRAN_GPL]).hexdigest()
    exception_digest = hashlib.sha256(license_data[_ARCHIVE_VALIDATOR.LIBGFORTRAN_EXCEPTION]).hexdigest()
    monkeypatch.setattr(_ARCHIVE_VALIDATOR, "LIBGFORTRAN_BINARY_SHA256", binary_digest)
    monkeypatch.setattr(_ARCHIVE_VALIDATOR, "LIBGFORTRAN_SOURCE_SHA256", source_digest)
    monkeypatch.setattr(_ARCHIVE_VALIDATOR, "LIBGFORTRAN_GPL_SHA256", gpl_digest)
    monkeypatch.setattr(_ARCHIVE_VALIDATOR, "LIBGFORTRAN_EXCEPTION_SHA256", exception_digest)
    manifest_path = release / _ARCHIVE_VALIDATOR.LIBGFORTRAN_MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "# libgfortran source and binary provenance\n"
        "- Source package version: `8.5.0-28.el8_10.alma.1` (upstream GCC `8.5.0`); shared-library ABI major: `5`.\n"
        f"- ELF build ID: `{_ARCHIVE_VALIDATOR.LIBGFORTRAN_BUILD_ID}`\n"
        f"- Staged binary SHA-256: `{binary_digest}`\n"
        f"- Source RPM SHA-256: `{source_digest}`\n"
        f"- GPL SHA-256: `{gpl_digest}`\n- Exception SHA-256: `{exception_digest}`\n"
        "- Source header: `libgfortran/runtime/error.c`, `COPYING3`, `COPYING.RUNTIME`.\n\n"
        f"### `{binary_rel}`\n\n- Archive-relative binary SHA-256: `{binary_digest}`\n"
        f"- ELF build ID: `{_ARCHIVE_VALIDATOR.LIBGFORTRAN_BUILD_ID}`\n",
        encoding="utf-8",
    )
    inventory = (
        "# Bundled runtime inventory\n\n"
        + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
        + "| --- | --- | --- | --- | --- | --- |\n"
        + f"| `{binary_rel}` | GCC libgfortran (AlmaLinux GCC source RPM) | 8.5.0-28.el8_10.alma.1 (GCC 8.5.0; ABI 5) | GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1 | { _ARCHIVE_VALIDATOR.LIBGFORTRAN_GPL}; { _ARCHIVE_VALIDATOR.LIBGFORTRAN_EXCEPTION}; { _ARCHIVE_VALIDATOR.LIBGFORTRAN_SOURCE}; { _ARCHIVE_VALIDATOR.LIBGFORTRAN_MANIFEST} | build ID {_ARCHIVE_VALIDATOR.LIBGFORTRAN_BUILD_ID}; binary SHA-256 {binary_digest}; source RPM SHA-256 {source_digest} |\n\n"
        + "Unresolved native payload count: 0.\n"
    )
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(inventory, encoding="utf-8")
    archive = tmp_path / "libgfortran-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(release, arcname=root)

    verified = _ARCHIVE_VALIDATOR.validate_archive(archive, root)
    assert f"{root}/{_ARCHIVE_VALIDATOR.LIBGFORTRAN_MANIFEST}" in verified

    wrong_inventory = inventory.replace("GCC libgfortran (AlmaLinux GCC source RPM)", "Python distribution numpy")
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(wrong_inventory, encoding="utf-8")
    bad_archive = tmp_path / "misattributed-libgfortran.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        tar.add(release, arcname=root)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_archive, root)
    except RuntimeError as exc:
        assert "libgfortran provenance is not correctly identified" in str(exc)
    else:
        raise AssertionError("archive validator accepted libgfortran under NumPy metadata")


def test_elf_linkage_evidence_uses_archive_relative_paths(tmp_path, monkeypatch):
    release = tmp_path / "release"
    release.mkdir()
    extension = release / "_ctypes.cpython-312-x86_64-linux-gnu.so"
    extension.write_bytes(b"\x7fELFfixture")
    bundled = release / "libffi.so.8"
    bundled.write_bytes(b"\x7fELFfixture-lib")
    external = tmp_path / "system" / "libc.so.6"
    external.parent.mkdir()
    external.write_bytes(b"\x7fELFexternal")

    class Result:
        stdout = (
            f"\tlibffi.so.8 => {bundled} (0x00007f)\n"
            f"\tlibc.so.6 => {external} (0x00007f)\n"
        )

    monkeypatch.setattr(_STAGER.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = _STAGER._elf_linkage_evidence(extension, release)

    assert "libffi.so.8 (bundled as `libffi.so.8`)" in evidence
    assert "libc.so.6 (system-provided; not bundled at this path)" in evidence
    assert str(tmp_path) not in evidence


def test_system_package_provenance_uses_exact_elf_build_id_when_nuitka_patches_file(
    tmp_path, monkeypatch
):
    licenses = tmp_path / "release" / "LICENSES"
    licenses.mkdir(parents=True)
    staged = tmp_path / "release" / "libfontconfig.so.1"
    staged.write_bytes(b"Nuitka-patched ELF bytes")
    host = tmp_path / "usr" / "libfontconfig.so.1.12.0"
    host.parent.mkdir()
    host.write_bytes(b"Original package-managed ELF bytes")
    copyright_file = tmp_path / "copyright"
    copyright_file.write_text("Format: fixture\nFiles: *\nLicense: MIT\n", encoding="utf-8")
    monkeypatch.setattr(_STAGER, "_python_provenance_index", lambda names, python_source="": {})
    monkeypatch.setattr(_STAGER, "_host_library_candidates", lambda: [host])
    monkeypatch.setattr(_STAGER, "_dpkg_package_for_path", lambda path: (
        "libfontconfig1", "2.13.1-4.2ubuntu5", copyright_file, "MIT"
    ))
    build_ids = {staged: "abcd1234", host: "abcd1234"}
    monkeypatch.setattr(
        _STAGER, "elf_build_id", lambda path: build_ids.get(Path(path))
    )

    result = _STAGER._native_provenance_index(
        licenses, {_STAGER.sha256_file(staged)}, {staged.name}, set(), [staged]
    )

    provenance = result[_STAGER.sha256_file(staged)]
    assert provenance.component == "Debian package libfontconfig1"
    assert provenance.version == "2.13.1-4.2ubuntu5"
    assert provenance.license == "MIT"
    assert "ELF build ID abcd1234" in provenance.evidence
    assert (tmp_path / "release" / provenance.notice).is_file()


def test_dpkg_lookup_retries_usr_merged_target_and_reads_package_copyright(
    tmp_path, monkeypatch
):
    """Query the canonical dpkg path after a staged/loader path fails."""
    source = tmp_path / "lib" / "libbz2.so.1.0"
    resolved = tmp_path / "usr" / "lib" / "libbz2.so.1.0.4"
    resolved.parent.mkdir(parents=True)
    resolved.write_bytes(b"ELF")
    source.parent.mkdir()
    source.symlink_to(resolved)
    copyright_file = Path("/usr/share/doc/libbz2-1.0/copyright")
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: Path(path) == copyright_file or original_is_file(path)
    )
    calls = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["dpkg-query", "-S"]:
            if args[-1] == str(source):
                return Result(1, "", f"dpkg-query: no path found matching pattern {source}")
            if args[-1] == str(resolved):
                return Result(0, f"libbz2-1.0:amd64: {resolved}\n", "")
            return Result(1, "", "not found")
        if args[:2] == ["dpkg-query", "-W"]:
            assert args[2] == "-f=${Version}\\n"
            assert args[3] == "libbz2-1.0:amd64"
            return Result(0, "1.0.8-5build1\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(_STAGER.subprocess, "run", run)
    monkeypatch.setattr(_STAGER, "_debian_copyright_license", lambda path: "bzip2 license; see package copyright")

    record, attempts = _STAGER._dpkg_package_lookup(source)

    assert record is not None
    assert record["package"] == "libbz2-1.0"
    assert record["version"] == "1.0.8-5build1"
    assert record["copyright_file"] == copyright_file
    assert record["license"] == "bzip2 license; see package copyright"
    assert record["owner_path"] == str(resolved)
    assert len(attempts) == 1
    assert attempts[0]["path"] == str(source)


def test_dpkg_lookup_rejects_ambiguous_multiarch_version_output(tmp_path, monkeypatch):
    library = tmp_path / "libdemo.so.1"
    library.write_bytes(b"ELF")
    copyright_file = Path("/usr/share/doc/libdemo1/copyright")
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: Path(path) == copyright_file or original_is_file(path)
    )

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(args, **kwargs):
        if args[:2] == ["dpkg-query", "-S"]:
            return Result(0, f"libdemo1:amd64: {library}\n", "")
        if args[:2] == ["dpkg-query", "-W"]:
            assert args[3] == "libdemo1:amd64"
            return Result(0, "1.2.3\n1.2.3\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(_STAGER.subprocess, "run", run)
    monkeypatch.setattr(_STAGER, "_debian_copyright_license", lambda path: "MIT")

    record, _attempts = _STAGER._dpkg_package_lookup(library)

    assert record is not None
    assert record["version"] is None
    assert _STAGER._dpkg_package_for_path(library) is None


def test_debian_copyright_parser_uses_applicable_file_stanzas_only(tmp_path):
    record = tmp_path / "copyright"
    record.write_text(
        "Format: DEP-5\n\n"
        "Files: *\nLicense: MIT\n\n"
        "License: MIT\n Permission text\n\n"
        "Files: debian/*\nLicense: GPL-2+\n",
        encoding="utf-8",
    )

    label = _STAGER._debian_copyright_license(record)

    assert label == (
        "Per-file license declarations in included Debian copyright record: "
        "Files *: MIT; Files debian/*: GPL-2+"
    )


def test_debian_copyright_parser_leaves_unknown_prose_unresolved(tmp_path):
    record = tmp_path / "copyright"
    record.write_text("Copyright and license details are in the source tree.\n", encoding="utf-8")

    assert _STAGER._debian_copyright_license(record) is None


def test_nuitka_source_expands_sys_real_prefix_for_python_extensions(tmp_path, monkeypatch):
    base = tmp_path / "python-3.12.14"
    source = base / "lib" / "python3.12" / "lib-dynload" / "_ssl.cpython-312-x86_64-linux-gnu.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"CPython extension fixture")
    monkeypatch.setattr(_STAGER.sys, "base_prefix", str(base))
    monkeypatch.setattr(_STAGER.sys, "prefix", str(tmp_path / ".venv-xft"))

    expanded = _STAGER._expand_nuitka_source_path(
        "${sys.real_prefix}/lib/python3.12/lib-dynload/_ssl.cpython-312-x86_64-linux-gnu.so"
    )

    assert expanded == source
    assert expanded.is_file()


def test_diagnostic_classifies_cpython_extension_from_report_and_binary_match(
    tmp_path, monkeypatch
):
    base = tmp_path / "python-3.12.14"
    source = base / "lib" / "python3.12" / "lib-dynload" / "_ssl.cpython-312-x86_64-linux-gnu.so"
    source.parent.mkdir(parents=True)
    payload = b"\x7fELF CPython extension fixture"
    source.write_bytes(payload)
    release = tmp_path / "release"
    release.mkdir()
    (release / "_ssl.so").write_bytes(payload)
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(
        "# Inventory\n\n" + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| `_ssl.so` | UNRESOLVED native payload | UNRESOLVED | UNRESOLVED | UNRESOLVED | no provenance |\n",
        encoding="utf-8",
    )
    report = tmp_path / "nuitka-report.xml"
    report.write_text(
        '<nuitka-compilation-report><module name="_ssl" '
        'kind="PythonExtensionModule" '
        'source_path="${sys.real_prefix}/lib/python3.12/lib-dynload/'
        '_ssl.cpython-312-x86_64-linux-gnu.so" /></nuitka-compilation-report>',
        encoding="utf-8",
    )
    monkeypatch.setattr(_STAGER.sys, "base_prefix", str(base))
    monkeypatch.setattr(
        _DIAGNOSTIC.sysconfig, "get_config_var", lambda key: str(source.parent)
    )
    monkeypatch.setattr(_STAGER, "_host_library_candidates", lambda: [])

    result = _DIAGNOSTIC.diagnose(tmp_path / "deploy", release, report)
    source_row = next(
        entry for entry in result["payloads"][0]["nuitka_report_sources"]
        if entry["record_type"] == "module"
    )

    assert source_row["classification_status"] == "verified CPython extension"
    assert source_row["source_matches_staged_payload"] is True
    assert source_row["component"] == "CPython runtime"
    assert source_row["component_version"] == _DIAGNOSTIC.sys.version.split()[0]
    assert source_row["component_license"] == "PSF-2.0"


def test_diagnostic_does_not_treat_dpkg_error_text_as_package(monkeypatch, tmp_path):
    def no_owner(path):
        return None, [{
            "path": str(path), "returncode": "1",
            "stderr": f"dpkg-query: no path found matching pattern {path}",
        }]

    monkeypatch.setattr(_STAGER, "_dpkg_package_lookup", no_owner)
    owner = _DIAGNOSTIC._package_owner(tmp_path / "libunknown.so")

    assert owner["package"] is None
    assert owner["version"] is None
    assert owner["attempted_queries"][0]["stderr"].startswith("dpkg-query:")


def test_native_diagnostic_reports_local_paths_and_unresolved_dependencies(tmp_path):
    deployment = tmp_path / "MiLaS.dist"
    release = tmp_path / "release"
    deployment.mkdir()
    release.mkdir()
    payload = release / "libffi.so.8"
    payload.write_bytes(b"\x7fELF fixture")
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(
        "# Inventory\n\n" + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| `libffi.so.8` | UNRESOLVED native payload | UNRESOLVED | UNRESOLVED | UNRESOLVED | no provenance |\n",
        encoding="utf-8",
    )

    result = _DIAGNOSTIC.diagnose(deployment, release, None)

    assert result["unresolved_payload_count"] == 1
    item = result["payloads"][0]
    assert item["archive_relative_path"] == "libffi.so.8"
    assert item["staged_path"] == str(payload)
    assert "elf_dynamic_section" in item and "ldd" in item
    assert "absolute paths" in result["warning"]


def test_archive_validator_still_rejects_unresolved_native_rows(tmp_path):
    archive = _make_representative_tar(tmp_path)
    with tarfile.open(archive, "r:gz") as source:
        root = "MiLaS-linux-x86_64"
        release = tmp_path / "unresolved" / root
        release.mkdir(parents=True)
        for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
            target = release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(f"{root}/{relative}").read())
        (release / "LICENSE").write_text("fixture", encoding="utf-8")
        (release / "NOTICE").write_text("fixture", encoding="utf-8")
        (release / "MiLaS.bin").write_bytes(b"\x7fELFfixture executable")
        unresolved = (release / "BUNDLED_RUNTIME_INVENTORY.md").read_text(encoding="utf-8")
        unresolved = unresolved.replace("MiLaS executable", "UNRESOLVED native payload")
        unresolved = unresolved.replace("Unresolved native payload count: 0.", "Unresolved native payload count: 1.")
        (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(unresolved, encoding="utf-8")
    bad_archive = tmp_path / "unresolved.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as target:
        target.add(release, arcname=root)

    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_archive, "MiLaS-linux-x86_64")
    except RuntimeError as exc:
        assert "lacks verified classification" in str(exc)
        assert "reports 1 unresolved native payload" in str(exc)
    else:
        raise AssertionError("archive validator accepted an unresolved native payload")


def test_windows_validator_enforces_openblas_kiwisolver_and_msvc_evidence(tmp_path):
    root = "MiLaS-windows-x64"
    native = {
        "MiLaS.exe": ("MiLaS standalone executable", "0.1.0", "PolyForm Noncommercial License 1.0.0", "LICENSE; NOTICE", "build output"),
            "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd": (
                "Python distribution numpy", "2.5.4", "BSD-3-Clause",
                "LICENSES/Python/numpy-2.5.4/", "RECORD entry; msvcp140.dll import rewrite; source SHA-256 " + "1" * 64 + "; patched payload SHA-256 " + hashlib.sha256(b"MZ fixture native file").hexdigest(),
            ),
            "numpy/fft/_pocketfft_umath.cp312-win_amd64.pyd": (
                "Python distribution numpy", "2.5.4", "BSD-3-Clause",
                "LICENSES/Python/numpy-2.5.4/", "RECORD entry; msvcp140.dll import rewrite; source SHA-256 " + "2" * 64 + "; patched payload SHA-256 " + hashlib.sha256(b"MZ fixture native file").hexdigest(),
            ),
        "numpy.libs/libscipy_openblas64_-fixture.dll": (
            "OpenBLAS with LAPACK bundled in the NumPy wheel", "NumPy 2.5.4 wheel; OpenBLAS release version not verified",
            "BSD-3-Clause AND BSD-3-Clause-Attribution",
            "LICENSES/OpenBLAS-BSD-3-Clause.txt; LICENSES/OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt",
            "NumPy wheel SHA-256 match to its RECORD",
        ),
        "kiwisolver/_cext.pyd": (
            "Python distribution kiwisolver", "1.5.1", "BSD-3-Clause",
            "LICENSES/Python/kiwisolver-1.5.1/LICENSE", "wheel hash match",
        ),
        "vcruntime140.dll": (
            "Microsoft Visual C++ Runtime", "14.44.35211.0",
            "Microsoft Visual C++ Redistributable terms", "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
            "CompanyName=Microsoft Corporation; OriginalFilename=vcruntime140.dll; ProductVersion=14.44.35211.0; Microsoft Visual Studio 2022 REDIST; source SHA-256 abc",
        ),
        "vcruntime140_1.dll": (
            "Microsoft Visual C++ Runtime", "14.44.35211.0",
            "Microsoft Visual C++ Redistributable terms", "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
            "CompanyName=Microsoft Corporation; OriginalFilename=vcruntime140_1.dll; ProductVersion=14.44.35211.0; Microsoft Visual Studio 2022 REDIST; source SHA-256 def",
        ),
        "numpy.libs/msvcp140.dll": (
            "Microsoft Visual C++ Runtime", "14.40.33810.0",
            "Microsoft Visual C++ Redistributable terms", "LICENSES/Microsoft-Visual-C-Redistributable-terms.md",
            "CompanyName=Microsoft Corporation; OriginalFilename=msvcp140.dll; ProductVersion=14.40.33810.0; Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC143.CRT; source SHA-256; staged payload SHA-256",
        ),
        "libffi-8.dll": (
            "libffi", "Windows ABI 8; upstream release version not encoded", "MIT",
            "LICENSES/libffi-LICENSE.txt", "PE image identifies ABI 8 but does not record the upstream release version",
        ),
    }
    licenses_root = Path(__file__).parents[1] / "packaging" / "licenses"
    openblas_text = (licenses_root / "OpenBLAS-BSD-3-Clause.txt").read_text(encoding="utf-8")
    lapack_text = (
        licenses_root / "OpenBLAS-LAPACK-BSD-3-Clause-Attribution.txt"
    ).read_text(encoding="utf-8")

    def make_archive(
        path: Path, *, omit_lapack=False, malformed_kiwi=False,
        hashed_msvcp=False, msvc_hash_mismatch=False,
    ):
        lines = ["# Runtime inventory", "", _ARCHIVE_VALIDATOR.INVENTORY_HEADER,
                 "| --- | --- | --- | --- | --- | --- |"]
        fixture_native = dict(native)
        if hashed_msvcp:
            fixture_native["numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"] = native["numpy.libs/msvcp140.dll"]
        payload_hash = hashlib.sha256(b"MZ fixture native file").hexdigest()
        for relative, values in fixture_native.items():
            component, version, license_name, notice, evidence = values
            if malformed_kiwi and relative == "kiwisolver/_cext.pyd":
                license_name, notice = "==================", "LICENSES/Python/kiwisolver-1.5.1/"
            if relative in {
                "numpy.libs/msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
            }:
                source_hash = "0" * 64 if msvc_hash_mismatch and relative == "numpy.libs/msvcp140.dll" else payload_hash
                evidence = (
                    f"CompanyName=Microsoft Corporation; OriginalFilename={relative.rsplit('/', 1)[-1]}; "
                    f"ProductVersion={version}; Visual Studio VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC143.CRT; "
                    f"source SHA-256 {source_hash}; staged payload SHA-256 {payload_hash}"
                )
            lines.append(
                f"| `{relative}` | {component} | {version} | {license_name} | {notice} | {evidence} |"
            )
        lines += ["", "Unresolved native payload count: 0."]
        with zipfile.ZipFile(path, "w") as handle:
            for required in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
                body = "\n".join(lines) if required == "BUNDLED_RUNTIME_INVENTORY.md" else "fixture"
                handle.writestr(f"{root}/{required}", body)
            extras = (
                "LICENSE", "NOTICE", "README.md", "THIRD_PARTY_NOTICES.md",
                "LICENSES/libffi-LICENSE.txt",
                "LICENSES/Python/kiwisolver-1.5.1/LICENSE",
                "LICENSES/Python/numpy-2.5.4/LICENSE.txt",
                _ARCHIVE_VALIDATOR.WINDOWS_OPENBLAS_LICENSE,
                _ARCHIVE_VALIDATOR.WINDOWS_LAPACK_LICENSE,
                _ARCHIVE_VALIDATOR.WINDOWS_MSVCRT_TERMS,
            )
            for extra in extras:
                if omit_lapack and extra == _ARCHIVE_VALIDATOR.WINDOWS_LAPACK_LICENSE:
                    continue
                if extra.endswith("OpenBLAS-BSD-3-Clause.txt"):
                    body = openblas_text
                elif extra.endswith("Attribution.txt"):
                    body = lapack_text
                elif extra == "README.md":
                    body = "Windows release fixture\n"
                elif extra == "THIRD_PARTY_NOTICES.md":
                    body = "Windows runtime fixture; Visual Studio REDIST and OpenBLAS.\n"
                else:
                    body = "fixture license"
                handle.writestr(f"{root}/{extra}", body)
            for relative in fixture_native:
                handle.writestr(f"{root}/{relative}", b"MZ fixture native file")

    valid = tmp_path / "windows-valid.zip"
    make_archive(valid)
    _ARCHIVE_VALIDATOR.validate_archive(valid, root)

    missing_lapack = tmp_path / "windows-missing-lapack.zip"
    make_archive(missing_lapack, omit_lapack=True)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(missing_lapack, root)
    except RuntimeError as exc:
        assert "OpenBLAS payload requires license notice" in str(exc)
    else:
        raise AssertionError("validator accepted an OpenBLAS payload without the LAPACK notice")

    bad_kiwi = tmp_path / "windows-malformed-kiwi.zip"
    make_archive(bad_kiwi, malformed_kiwi=True)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_kiwi, root)
    except RuntimeError as exc:
        assert "kiwisolver extension has incorrect license classification" in str(exc)
    else:
        raise AssertionError("validator accepted the malformed kiwisolver license row")

    hashed_msvcp_archive = tmp_path / "windows-hashed-msvcp.zip"
    make_archive(hashed_msvcp_archive, hashed_msvcp=True)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(hashed_msvcp_archive, root)
    except RuntimeError as exc:
        assert "Hash-named Microsoft CRT DLLs are not permitted" in str(exc)
    else:
        raise AssertionError("validator accepted a hash-named NumPy MSVC runtime DLL")

    msvc_hash_mismatch_archive = tmp_path / "windows-msvc-hash-mismatch.zip"
    make_archive(msvc_hash_mismatch_archive, msvc_hash_mismatch=True)
    try:
        _ARCHIVE_VALIDATOR.validate_archive(msvc_hash_mismatch_archive, root)
    except RuntimeError as exc:
        assert "Microsoft runtime DLL hash does not match its recorded REDIST source" in str(exc)
    else:
        raise AssertionError("validator accepted a Microsoft CRT source hash mismatch")


def test_archive_validator_reads_windows_backslash_zip_members_without_weakening_checks(
    tmp_path,
):
    archive = tmp_path / "windows-backslash.zip"
    root = "MiLaS-windows-x64"
    files = {
        "BUNDLED_RUNTIME_INVENTORY.md": (
            "# Inventory\n\n" + _ARCHIVE_VALIDATOR.INVENTORY_HEADER + "\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| `_socket.pyd` | UNRESOLVED native payload | UNRESOLVED | "
            "UNRESOLVED | notice.txt | no verified source evidence |\n\n"
            "Unresolved native payload count: 1.\n"
        ),
        "LICENSES/Tcl-Tk-runtime-files.txt": "files\n",
        "LICENSES/Tcl-license.terms": "tcl\n",
        "LICENSES/Tk-license.terms": "tk\n",
        "LICENSES/OpenSSL-Apache-2.0.txt": "openssl\n",
        "README.md": "Windows fixture\n",
        "THIRD_PARTY_NOTICES.md": "Windows runtime fixture\n",
        "notice.txt": "notice\n",
        "_socket.pyd": "MZ fixture extension",
    }
    with zipfile.ZipFile(archive, "w") as handle:
        for relative, contents in files.items():
            handle.writestr(f"{root}\\{relative.replace('/', chr(92))}", contents)

    try:
        _ARCHIVE_VALIDATOR.validate_archive(archive, root)
    except RuntimeError as exc:
        assert "Native payload lacks verified classification: _socket.pyd" in str(exc)
        assert "unresolved-payload" not in str(exc)
    else:
        raise AssertionError("archive validator accepted an unresolved Windows payload")


def test_archive_validation_rejects_missing_referenced_license(tmp_path):
    archive = _make_representative_tar(tmp_path)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        inventory = tar.extractfile("MiLaS-linux-x86_64/BUNDLED_RUNTIME_INVENTORY.md").read().decode()
    inventory = inventory.replace("LICENSE; NOTICE", "LICENSES/Missing-License.txt")
    release = tmp_path / "replacement" / "MiLaS-linux-x86_64"
    release.mkdir(parents=True)
    for relative in _ARCHIVE_VALIDATOR.REQUIRED_FILES:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as source_tar:
            path.write_bytes(source_tar.extractfile(f"MiLaS-linux-x86_64/{relative}").read())
    (release / "BUNDLED_RUNTIME_INVENTORY.md").write_text(inventory, encoding="utf-8")
    (release / "LICENSE").write_text("license", encoding="utf-8")
    (release / "NOTICE").write_text("notice", encoding="utf-8")
    (release / "MiLaS.bin").write_bytes(b"\x7fELFfixture executable")
    bad_archive = tmp_path / "missing-notice.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as target:
        target.add(release, arcname="MiLaS-linux-x86_64")

    try:
        _ARCHIVE_VALIDATOR.validate_archive(bad_archive, "MiLaS-linux-x86_64")
    except RuntimeError as exc:
        assert "Notice referenced by MiLaS.bin is missing" in str(exc)
    else:
        raise AssertionError("archive validator accepted a missing license notice")
