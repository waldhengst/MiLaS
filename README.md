# MiLaS — Milanković Lab for Solar Insolation

*The Milanković Lab: Explore Earth’s orbit, seasons, and solar insolation interactively.*

A compact starting point for the teaching goal: use a few controls to explore
how eccentricity, axial tilt, and precession affect seasonal and
latitude-dependent insolation.

The desktop interface uses Tkinter and Matplotlib's TkAgg backend. Tkinter is
Python's standard-library interface to Tcl/Tk; it also requires a Tcl/Tk
runtime supplied by the Python distribution.

## Standalone desktop releases

Native standalone packages are built separately for Linux x86_64 and Windows
x64; they include the application runtime and do not require Python, uv, or a
separate MiLaS dependency install. These binaries are release outputs and are
not included in the source ZIP. Reproducible native build steps are documented
in [`packaging/BUILD.md`](packaging/BUILD.md).

When published, launch the platform package as follows:

- **Linux:** download `MiLaS-linux-x86_64.tar.gz`, extract it, open the
  `MiLaS-linux-x86_64` folder, and run `./MiLaS.bin`.
- **Windows:** download and extract `MiLaS-windows-x64.zip`, open the
  `MiLaS-windows-x64` folder, and run `MiLaS.exe`.

Each standalone package includes this README, `LICENSE`, `NOTICE`, and
`THIRD_PARTY_NOTICES.md`.

<!-- platform:linux:start -->
**Linux runtime notice:** this Linux standalone package uses and bundles GCC
`libquadmath` 8.5.0, covered by GNU LGPL 2.1-or-later. The complete license
and matching AlmaLinux GCC source RPM are included in the same release archive
as `LICENSES/GNU-LGPL-2.1.txt` and
`SOURCE/gcc-8.5.0-28.el8_10.alma.1.src.rpm`. See
`SOURCE/LIBQUADMATH-SOURCE.md`, `SOURCE/LIBGFORTRAN-SOURCE.md`, and
`THIRD_PARTY_NOTICES.md` for checksums, source provenance, and license terms.

<!-- platform:linux:end -->
## Setup and launch from source on Linux

Extract the source ZIP into a `MiLaS/` project folder. From a terminal, run:

```bash
cd MiLaS
uv sync
uv run python -m milas.app
```

`uv sync` creates the project environment and installs Matplotlib and NumPy
from the project metadata and lock file. The Python interpreter must include
Tkinter and its Tcl/Tk runtime. On Linux, use a Python build with Tk support;
for distribution-provided Python, this is commonly packaged separately (for
example, as `python3-tk`).

## Quick comparisons

Quick comparisons update the current selection. Each parameter comparison
changes only the named orbital parameter; the other two values retain their
last settings. **Precession: opposite phase** shifts only the winter-solstice
longitude by 180°. **Today** resets all three values to approximate present-day
parameters.

- **Today**: approximate present-day orbital parameters.
- **Eccentricity minimum/maximum**: changes eccentricity only.
- **Axial tilt minimum/maximum**: changes axial tilt only.
- **Precession: opposite phase**: changes precession only.

The 3D view shows the selected orbit and Earth's spin axis, with the present-day
orbit and axis permanently overlaid as a dashed gray reference. Blue indicates
the selection; dashed gray indicates the present-day reference. The Sun is
orange. Earth and its axis are enlarged for clarity and are not to scale. Two
larger globes show summer and winter. Both use the same fixed geographic
camera, centered near 40°N and using the same longitude framing, so the same
regions can be compared while 65°N and the Arctic remain clearly visible. The
camera does not follow the Sun. At each location,
the map shows the change in instantaneous top-of-atmosphere (TOA) insolation:
the selected configuration minus today's configuration for that season. Both
globes use the same fixed diverging color scale (−250 to +250 W/m²): blue means
less insolation than today, and red means more. The color overlay remains
prominent while keeping the grayscale 4096×2048 Earth texture and coastlines
visible beneath it. Subtle day/night shading adds lighting without hiding the
difference colors. A compact **Selected vs Today** table below the globes
shows today's values, the selected values, and their differences for
eccentricity, obliquity, the winter solstice's true anomaly from perihelion,
and calculated Earth–Sun distances at both solstices. The anomaly difference
is wrapped to the shortest signed angle.

Latitude lines and meridians are drawn on the surface. Drag either globe with
the mouse to rotate both views together and keep their geographic framing
aligned. The globes also rotate automatically around Earth's axis; press
**Space** to pause or resume. The blue axis passes through each globe and
extends beyond the surface at the poles. The **N** marker identifies the North
Pole, and the visible axis angle shows Earth's tilt. The selected axis and
seasonal insolation respond to the orbital parameters. At the northern summer
and winter solstices, solar declination is +ε and −ε, respectively. Precession
changes each solstice's position relative to perihelion and aphelion, and thus
the Earth–Sun distance at that solstice. Perihelion and aphelion are not
solstices unless the selected configuration makes them coincide.

The annual-mean chart has fixed axes: 0–450 W/m² and −90° to +90° latitude. This
is not a climate model; it does not include atmospheric effects, clouds,
land-distribution effects, or feedbacks.

## Calculation conventions

The orbit is calculated for a semi-major axis of 1 AU by solving Kepler's
equation. Samples are equally spaced in mean anomaly. The winter-solstice angle
is Earth's true anomaly measured from perihelion. The insolation plots use the
daily-mean formula or instantaneous solar-zenith irradiance for a spherical
Earth, with a constant solar irradiance of 1361 W/m².

## Tests

```bash
uv run pytest
```

## Contribution and licensing

MiLaS is available at no charge for permitted noncommercial use, including
university teaching. Use remains subject to the applicable license terms; this
is not permission for commercial use.

Concept and product design: Stefan Caesar conceived MiLaS as an interactive
comparison model and defined its information structure, visualizations, and
design direction.

Technical implementation: Developed iteratively with ChatGPT (OpenAI), based on
Stefan Caesar’s specifications and decisions.

License: Original MiLaS project components that Stefan Caesar is entitled to
license are licensed under the PolyForm Noncommercial License 1.0.0. This
permits noncommercial use, including university teaching. Third-party
components and assets remain subject to their own terms and are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The NASA-derived Earth texture
is not relicensed under the MiLaS license.

<!-- platform:linux:start -->
For the Linux distribution with LGPL-licensed `libquadmath`, the additional
permission in [`LGPL-2.1-Section-6-Additional-Permission.md`](LGPL-2.1-Section-6-Additional-Permission.md) allows recipients to modify the MiLaS
combined program for their own use and to reverse engineer MiLaS only as
reasonably necessary to debug modifications to `libquadmath` or that combined
program, as required by LGPL section 6. This narrow permission does not
authorize commercial use or grant additional distribution rights; the other
PolyForm Noncommercial terms continue to apply.

<!-- platform:linux:end -->
Standalone release packaging and platform-specific runtime license collection
are documented in [packaging/BUILD.md](packaging/BUILD.md). Release packages
remain separate from the source ZIP and must be checked against their generated
runtime inventory before redistribution.
