"""Frozen application entry point for native MiLaS release builds."""

import sys


if __name__ == "__main__":
    if sys.argv[1:] == ["--verify-numpy-runtime"]:
        import numpy

        print(f"Standalone NumPy import succeeded: {numpy.__version__}")
        raise SystemExit(0)

    from milas.app import main

    raise SystemExit(main())
