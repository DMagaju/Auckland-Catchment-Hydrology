# -*- coding: utf-8 -*-
"""
Shared utilities for Subcatchment Assessment processing algorithms.
Imported by Subcatch_StreamGeneration and Subcatch_SubBasinGenerator.
"""

__author__ = "Dipendra Magaju"

import math
import re

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsProject,
    QgsUnitTypes,
)


class _QuietFeedback(QgsProcessingFeedback):
    """Wraps a parent feedback, forwarding progress and errors but suppressing
    verbose GRASS shell-command lines that clutter the log panel."""

    _SUPPRESS = (
        'SetColorTable() only supported for Byte or UInt16',
        'ERROR 6:',
        'WKT not recognized',
    )

    def __init__(self, parent):
        super().__init__()
        self._parent = parent
        self.progressChanged.connect(parent.setProgress)

    def pushCommandInfo(self, info): pass
    def pushDebugInfo(self, info):   pass
    def pushConsoleInfo(self, info): pass

    def pushInfo(self, info):
        self._parent.pushInfo(info)

    def reportError(self, error, fatal=False):
        if not fatal and any(s in error for s in self._SUPPRESS):
            return
        self._parent.reportError(error, fatal)

    def isCanceled(self):
        return self._parent.isCanceled()


def _grass_prefix():
    """Return 'grass' or 'grass7' depending on which GRASS provider is active,
    or None if GRASS is not available."""
    ids = {p.id() for p in QgsApplication.processingRegistry().providers()}
    for candidate in ('grass', 'grass7'):
        if candidate in ids:
            return candidate
    return None


def _validate_metric_dem(dem):
    """Check DEM uses a projected CRS in metres.
    Returns the CRS, or raises QgsProcessingException suggesting the
    appropriate projected CRS to reproject to (NZTM2000 if the DEM falls
    within NZ, otherwise the UTM zone covering its location)."""
    crs = dem.crs()
    if crs.mapUnits() != QgsUnitTypes.DistanceMeters:
        label, suggested_crs = _suggest_metric_crs_choices(dem)[0]
        raise QgsProcessingException(
            f"DEM CRS '{crs.authid() or crs.description()}' is a geographic "
            "coordinate system (degrees), not a projected CRS in metres — "
            "this tool needs a projected, metric CRS to compute distances "
            "and areas correctly.\n\n"
            f"Reproject the DEM first, using {label}.\n"
            "In QGIS: Processing Toolbox -> Raster -> Projections -> "
            f"Warp (Reproject using GDAL), with target CRS "
            f"{suggested_crs.authid()}, then run this tool again on the "
            "reprojected DEM."
        )
    return crs


def _dem_center_lonlat(dem):
    """Returns (longitude, latitude) of the DEM extent's centre, in WGS84 —
    used to pick a sensible target CRS when the DEM itself is geographic."""
    centre = dem.extent().center()
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    if dem.crs() != wgs84:
        xform = QgsCoordinateTransform(dem.crs(), wgs84, QgsProject.instance())
        centre = xform.transform(centre)
    return centre.x(), centre.y()


def _is_in_new_zealand(lon, lat):
    """Rough bounding box covering mainland NZ (not Chatham Islands /
    outlying territories) — good enough to decide whether to suggest
    NZTM2000 ahead of a plain computed UTM zone."""
    return 165.0 <= lon <= 180.0 and -48.0 <= lat <= -33.0


def _utm_epsg_for(lon, lat):
    """Returns the EPSG code of the UTM zone containing (lon, lat)."""
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    base = 32600 if lat >= 0 else 32700
    return base + zone


def _suggest_metric_crs_choices(dem):
    """Returns [(label, QgsCoordinateReferenceSystem), ...] of sensible
    projected/metric CRS suggestions for reprojecting a geographic DEM,
    best choice first — NZTM2000 when the DEM falls within NZ, otherwise
    just the computed UTM zone for its location."""
    lon, lat = _dem_center_lonlat(dem)
    choices = []
    if _is_in_new_zealand(lon, lat):
        choices.append((
            'NZTM2000 (EPSG:2193) — recommended for New Zealand',
            QgsCoordinateReferenceSystem('EPSG:2193'),
        ))
    utm_epsg = _utm_epsg_for(lon, lat)
    zone = utm_epsg % 100
    hemisphere = 'N' if lat >= 0 else 'S'
    label = f'UTM Zone {zone}{hemisphere} (EPSG:{utm_epsg})'
    if not choices:
        label += ' — recommended for this location'
    choices.append((label, QgsCoordinateReferenceSystem(f'EPSG:{utm_epsg}')))
    return choices


def _safe_filename(value):
    """A string safe to use as a file name on Windows/macOS/Linux — strips
    characters that are illegal (or just awkward) in a path and collapses
    whitespace to underscores. Falls back to 'Outlet' if the result would
    otherwise be empty (e.g. a blank/None ID value). Used to turn an
    outlet's own ID field value into an '<ID>_Catchment.shp'-style file
    name (see OutletCatchmentGenerator's per-outlet output mode)."""
    s = str(value).strip() if value is not None else ''
    s = re.sub(r'[\\/:*?"<>|]', '_', s)
    s = re.sub(r'\s+', '_', s)
    return s or 'Outlet'


def _dem_pixel_info(dem, threshold_ha):
    """Return (pixel_x, pixel_y, cell_area_m2, threshold_cells) for a DEM
    and a contributing-area threshold in hectares."""
    pixel_x      = abs(dem.rasterUnitsPerPixelX())
    pixel_y      = abs(dem.rasterUnitsPerPixelY())
    cell_area_m2 = pixel_x * pixel_y
    threshold_cells = max(1, int((threshold_ha * 10_000.0) / cell_area_m2))
    return pixel_x, pixel_y, cell_area_m2, threshold_cells
