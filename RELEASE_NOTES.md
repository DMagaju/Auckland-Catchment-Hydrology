# Auckland Catchment & Hydrology — v1.1.0-beta.1

## ⚠️ Upgrade notice

V01 Longest Flow Path results were wrong — the D8 direction table was
rotated 45° (`1=E` instead of the correct `1=NE`), so every traced flow
path read the wrong neighbour cell. This is fixed in V02.

**If you processed anything with v1.0, re-run it:** Longest Flow Path →
Slope Calculation → Time of Concentration → Flow Calculation, in that
order, starting from your original sub-catchments, drainage direction and
flow accumulation rasters. Anything downstream of a V01 flow path
(`PATH_LENGTH_M`, slope, ToC, peak flow) is invalid.

## What's new

- **Longest Flow Path V02** — corrected D8 direction mapping, validated
  against GRASS `r.watershed` (see `tests/`). New fields: `D8_LENGTH_M`
  (hydrological length) and `PATH_LENGTH_M` (output line length, kept
  separate rather than conflated); `BRIDGE_CELLS` count. Optional
  stream-network extension is now opt-in (off by default) rather than
  always-on.
- **Project Folder outputs** — Download DEM's output folder is now a
  shared "Project Folder" that later steps (Channel Burn, Streams and
  Catchment, Longest Flow Path, Slope Calculation, Outlet Catchment)
  default their own outputs to, as real files instead of temporary QGIS
  scratch layers.
- **Dark-theme dialog styling** — fixed illegible native popups (CRS
  selector) and unstyled combobox dropdown lists under non-default QGIS
  themes.

## Validation status

- **Longest Flow Path** ✅ — 5/5 automated tests pass (direction test on
  8 compass planes, a synthetic-terrain accumulation regression test, a
  known-geometry V-valley test, a length-consistency test, and a
  before/after comparison against the V01 table).
- **Time of Concentration** and **TP108 Flow Calculation** ⏳ — not yet
  independently verified against worked examples. Check results before
  relying on them for design.

## Known limitations

- Tested on **Windows with QGIS 3.44.2 only**. Linux, macOS, QGIS 3.40
  LTR and QGIS 4.x are not tested — please report results from any of
  these via GitHub Issues.
- The **Generate Rainfall Data** tab is unfinished and hidden from the
  dock for this release.

## Install

Download the ZIP from this release (`AucklandCatchmentHydrology-1.1.0-beta.1.zip`)
and install it via QGIS → **Plugins → Manage and Install Plugins → Install
from ZIP**. Windows only for this beta. Do not use GitHub's green
"Code → Download ZIP" button — it has the wrong folder structure for QGIS.

## Feedback wanted

This is a public beta. Reports from other QGIS versions and platforms —
even just "it installed and the Download DEM tab opened" — are useful.
Please file issues at
[github.com/DMagaju/Auckland-Catchment-Hydrology/issues](https://github.com/DMagaju/Auckland-Catchment-Hydrology/issues).
