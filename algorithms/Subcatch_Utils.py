# -*- coding: utf-8 -*-
"""
Shared utilities for Subcatchment Assessment processing algorithms.
Imported by Subcatch_StreamGeneration and Subcatch_SubBasinGenerator.
"""

__author__ = "Dipendra Magaju"

from qgis.core import (
    QgsApplication,
    QgsProcessingFeedback,
    QgsProcessingException,
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
    Returns the CRS, or raises QgsProcessingException."""
    crs = dem.crs()
    if crs.mapUnits() != QgsUnitTypes.DistanceMeters:
        raise QgsProcessingException(
            f"DEM CRS '{crs.authid()}' does not use metres. "
            "Reproject to a projected metric CRS (e.g. EPSG:2193 NZTM) "
            "before running this tool."
        )
    return crs


def _dem_pixel_info(dem, threshold_ha):
    """Return (pixel_x, pixel_y, cell_area_m2, threshold_cells) for a DEM
    and a contributing-area threshold in hectares."""
    pixel_x      = abs(dem.rasterUnitsPerPixelX())
    pixel_y      = abs(dem.rasterUnitsPerPixelY())
    cell_area_m2 = pixel_x * pixel_y
    threshold_cells = max(1, int((threshold_ha * 10_000.0) / cell_area_m2))
    return pixel_x, pixel_y, cell_area_m2, threshold_cells
