import ast
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_runtime_project_uses_tkinter_tkagg_without_qt_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert not any("pyside" in item.lower() or "pyqt" in item.lower() for item in dependencies)

    app_source = (ROOT / "src" / "milas" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert "tkinter" in imported
    assert "matplotlib.backends.backend_tkagg" in imported
    assert "matplotlib.backends.backend_qtagg" not in imported
    assert not any("PySide6" in item or item.startswith("PyQt") for item in imported)
    assert 'matplotlib.use("TkAgg")' in app_source


def test_native_builds_use_current_nuitka_tk_inter_configuration():
    linux = (ROOT / "packaging" / "build-linux.sh").read_text(encoding="utf-8")
    windows = (ROOT / "packaging" / "build-windows.ps1").read_text(encoding="utf-8")
    for script in (linux, windows):
        assert "--mode=standalone" in script
        assert "--enable-plugin=tk-inter" in script
        assert "--enable-plugin=numpy" not in script
        assert "--include-package-data=milas" in script
        assert "--include-package-data=matplotlib" not in script
        assert "_multiarray_umath" in script
        assert "_umath_linalg" in script
        assert "matplotlib" in script
        assert "pyside6-deploy" not in script.lower()
        assert "--enable-plugin=pyside6" not in script.lower()

    assert not (ROOT / "packaging" / "pysidedeploy.spec").exists()


def test_native_builds_stage_and_archive_license_inventory_files():
    linux = (ROOT / "packaging" / "build-linux.sh").read_text(encoding="utf-8")
    windows = (ROOT / "packaging" / "build-windows.ps1").read_text(encoding="utf-8")
    required = (
        "BUNDLED_RUNTIME_INVENTORY.md",
        "Tcl-Tk-runtime-files.txt",
        "Tcl-license.terms",
        "Tk-license.terms",
        "OpenSSL-Apache-2.0.txt",
    )
    for item in required:
        assert item in linux
        assert item in windows
    assert "--python-source" in linux
    assert "--report=\"$build_dir/nuitka-report.xml\"" in linux
    assert "--nuitka-report \"$build_dir/nuitka-report.xml\"" in linux
    assert "diagnose_native_provenance.py" in linux
    assert "native-provenance-diagnostics.json" in linux
    assert "--python-source" in windows
    assert "verify_release_archive.py" in linux
    assert "verify_release_archive.py" in windows


def test_linux_build_pins_and_stages_matching_libquadmath_source():
    linux = (ROOT / "packaging" / "build-linux.sh").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "gcc-8.5.0-28.el8_10.alma.1.src.rpm" in linux
    assert "sha256sum --check" in linux
    assert "GNU-LGPL-2.1.txt" in linux
    assert "SOURCE/LIBQUADMATH-SOURCE.md" in linux
    assert "OpenSSL 3.5.9" not in notices
