# Auckland Catchment & Hydrology

A QGIS plugin providing a complete hydrology workflow for subcatchment delineation, flow path analysis, and TP108/SWCoP V4 flow calculation. Designed for Auckland-region stormwater assessments.

## ⚠️ Known issue in V01

**Longest Flow Path V01's D8 direction table was rotated by one position** (it
started 1=E instead of the correct 1=NE), so every traced flow path read the
wrong neighbour cell, 45° off. This was fixed in V02 (plugin version 1.1).
Any `PATH_LENGTH_M`, slope, or time-of-concentration result produced using a
V01 flow path layer is invalid and should be re-run with V02 from the
original sub-catchments, drainage, and accumulation rasters.

## Features

### Data Preparation
- **Download LiDAR** — downloads the latest LINZ 1 m LiDAR DEM tiles for a user-drawn extent via the LINZ Data Service API; auto-loads rasters into the project
- **DEM Channel Burn** — conditions the DEM by burning drainage lines into the surface to enforce flow direction

### Catchment Delineation
- **Stream and Catchment** — derives flow direction and accumulation, generates stream networks, and delineates subcatchments using GRASS r.watershed; supports both grid-based and outlet-based delineation
- **Longest Flow Path** — computes the hydraulically longest flow path per subcatchment using D8 routing; outputs both `D8_LENGTH_M` (hydrological length) and `PATH_LENGTH_M` (output line length), and a `BRIDGE_CELLS` count; optional experimental stream-network extension (off by default)
- **Subcatchment Naming** — assigns user-defined prefix-based IDs (e.g. `SC_01`, `SC_02`) to subcatchments

### Hydrology Calculations
- **Slope Calculation** — calculates average slope along the longest flow path for each subcatchment
- **Impervious Area** — intersects subcatchments with a land-use layer to calculate total, impervious, and pervious areas; derives composite curve number (CN)
- **Time of Concentration** — calculates ToC using the TP108 method (kinematic wave equation)
- **Flow Calculation** — computes peak flow rates following TP108 / Stormwater Code of Practice V4 rainfall profiles, including climate change adjustments

### Editing
- **Merge / Split** — interactively merge adjacent subcatchments or split them using a drawn line

## Requirements

- QGIS 3.16 or later
- LINZ Data Service API key (free — for LiDAR download only)

## Installation

### From QGIS Plugin Manager (recommended)
1. Open QGIS → **Plugins → Manage and Install Plugins**
2. Search for **Auckland Catchment & Hydrology**
3. Click **Install**

### From ZIP
1. Download the latest ZIP from [Releases](https://github.com/DMagaju/Auckland-Catchment-Hydrology/releases)
2. In QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**

## LINZ API Key

The LiDAR download requires a free LINZ Data Service API key:

1. Register at [data.linz.govt.nz](https://data.linz.govt.nz)
2. Create an API key under your account settings
3. Enter it in the **Download LiDAR** tab — it is stored securely in QGIS settings and is never saved in the plugin files

## Workflow

The tools are designed to run in sequence via the side panel:

1. **Download LiDAR** → download DEM for the catchment extent
2. **DEM Channel Burn** → condition DEM with drainage lines
3. **Stream and Catchment** → delineate streams and subcatchments
4. **Subcatchment Naming** → assign IDs
5. **Longest Flow Path** → compute hydraulic length
6. **Slope Calculation** → derive average slope
7. **Impervious Area** → calculate imperviousness and CN
8. **Time of Concentration** → compute ToC
9. **Flow Calculation** → compute peak flows per ARI

Each tab auto-fills inputs from the previous step's outputs.

## License

GNU General Public License v2 or later (GPLv2+) — see [LICENSE](LICENSE)

## Author

Dipendra Magaju — [dipen.magaju@gmail.com](mailto:dipen.magaju@gmail.com)

## Repository

[https://github.com/DMagaju/Auckland-Catchment-Hydrology](https://github.com/DMagaju/Auckland-Catchment-Hydrology)

## Issues

[https://github.com/DMagaju/Auckland-Catchment-Hydrology/issues](https://github.com/DMagaju/Auckland-Catchment-Hydrology/issues)
