# -*- coding: utf-8 -*-
"""
DEM Channel Burn — Burns one or more channel corridors into a LiDAR DEM.
All line features in the centreline layer are processed sequentially.

Burn modes:
  0 Replace              — always overwrites to (centreline_DEM - burn_depth)
  1 Lower-Only           — only cuts; never raises terrain
  2 Lower-Only Tolerance — only cuts where depth > threshold
  3 Longitudinal Gradient— DEM elevation sampled at line start/end; linear
                           gradient burned between those two levels

Library (Group): Subcatchment Assessment
Tool (Algorithm): DEM Channel Burn V01
File: Subcatch_DEMChannelBurn.py

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"
__status__  = "Beta"

import os
import math

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
    QgsUnitTypes,
)

try:
    from scipy.spatial import cKDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

try:
    from osgeo import gdal
    import numpy as np
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


# ── Module-level helpers ──────────────────────────────────────────────────────

def _densify_centerline(vertices, step):
    pts = [(vertices[0][0], vertices[0][1], 0.0)]
    s_cum = 0.0
    for i in range(1, len(vertices)):
        x0, y0 = vertices[i - 1]
        x1, y1 = vertices[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-9:
            continue
        n_steps = max(1, int(math.ceil(seg / step)))
        for k in range(1, n_steps + 1):
            t = k / n_steps
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0), s_cum + t * seg))
        s_cum += seg
    return np.array(pts, dtype=np.float64)   # shape (N, 3): x, y, cumulative_s


def _nearest_on_centerline(cl_xy, X_flat, Y_flat):
    if _HAS_SCIPY:
        tree = cKDTree(cl_xy)
        dists, idxs = tree.query(np.column_stack([X_flat, Y_flat]))
        return dists.astype(np.float64), idxs.astype(np.int64)
    n_cells = len(X_flat)
    dists = np.empty(n_cells, dtype=np.float64)
    idxs  = np.empty(n_cells, dtype=np.int64)
    CHUNK = 200
    for start in range(0, n_cells, CHUNK):
        end = min(start + CHUNK, n_cells)
        dx = X_flat[start:end, np.newaxis] - cl_xy[np.newaxis, :, 0]
        dy = Y_flat[start:end, np.newaxis] - cl_xy[np.newaxis, :, 1]
        d  = np.sqrt(dx * dx + dy * dy)
        ix = d.argmin(axis=1)
        idxs[start:end]  = ix
        dists[start:end] = d[np.arange(end - start), ix]
    return dists, idxs


def _sample_dem_at_xy(arr, gt, xy):
    cols = np.clip(((xy[:, 0] - gt[0]) / gt[1]).astype(int), 0, arr.shape[1] - 1)
    rows = np.clip(((gt[3] - xy[:, 1]) / abs(gt[5])).astype(int), 0, arr.shape[0] - 1)
    return arr[rows, cols].astype(np.float64)


def _edge_alpha(dists, half_w, blend_dist, edge_mode):
    if edge_mode == 0:
        return np.where(dists <= half_w, 1.0, 0.0)
    if edge_mode == 1:
        return np.where(dists <= half_w + max(blend_dist, 1e-9), 1.0, 0.0)
    safe_blend = max(blend_dist, 1e-9)
    in_chan  = dists <= half_w
    in_blend = (~in_chan) & (dists <= half_w + safe_blend)
    return np.where(
        in_chan, 1.0,
        np.where(
            in_blend,
            0.5 * (1.0 + np.cos(np.pi * (dists - half_w) / safe_blend)),
            0.0,
        ),
    )


def _burn_single(arr_full, orig_arr, gt, cl_verts,
                 burn_width, burn_depth, burn_mode, tolerance,
                 edge_mode, blend_dist, pixel_size, nodata,
                 nrows_full, ncols_full,
                 upstream_invert=0.0, downstream_invert=0.0):
    """
    Apply one burn line to arr_full (modified in-place).
    orig_arr is the unmodified baseline — used as the thalweg reference for
    depth-based modes so earlier burns don't compound.
    Returns (n_changed, area_m2, total_len, overlaps).
    """
    px = abs(gt[1])
    py = abs(gt[5])

    total_len = sum(
        math.hypot(cl_verts[i + 1][0] - cl_verts[i][0],
                   cl_verts[i + 1][1] - cl_verts[i][1])
        for i in range(len(cl_verts) - 1)
    )

    step     = pixel_size / 2.0
    cl_dense = _densify_centerline(cl_verts, step)   # (N, 3): x, y, s
    cl_xy    = cl_dense[:, :2]
    cl_s     = cl_dense[:, 2]                        # cumulative station

    # Sample original DEM along centreline (fixed reference elevation)
    cl_z = _sample_dem_at_xy(orig_arr, gt, cl_xy)

    half_w = burn_width / 2.0
    expand = half_w + max(blend_dist if edge_mode > 0 else 0.0, 0.0) + pixel_size * 2

    dem_xmin = gt[0];  dem_xmax = gt[0] + ncols_full * gt[1]
    dem_ymax = gt[3];  dem_ymin = gt[3] + nrows_full * gt[5]

    bbox_xmin = max(cl_dense[:, 0].min() - expand, dem_xmin)
    bbox_xmax = min(cl_dense[:, 0].max() + expand, dem_xmax)
    bbox_ymin = max(cl_dense[:, 1].min() - expand, dem_ymin)
    bbox_ymax = min(cl_dense[:, 1].max() + expand, dem_ymax)

    if bbox_xmin >= bbox_xmax or bbox_ymin >= bbox_ymax:
        return 0, 0.0, total_len, False

    col_min   = max(0, int((bbox_xmin - gt[0]) / gt[1]))
    col_max   = min(ncols_full, int(math.ceil((bbox_xmax - gt[0]) / gt[1])))
    row_min   = max(0, int((gt[3] - bbox_ymax) / abs(gt[5])))
    row_max   = min(nrows_full, int(math.ceil((gt[3] - bbox_ymin) / abs(gt[5]))))
    n_cols_bb = col_max - col_min
    n_rows_bb = row_max - row_min

    cols_c = gt[0] + (col_min + np.arange(n_cols_bb) + 0.5) * gt[1]
    rows_c = gt[3] + (row_min + np.arange(n_rows_bb) + 0.5) * gt[5]
    X_2d, Y_2d = np.meshgrid(cols_c, rows_c)
    X_flat = X_2d.ravel()
    Y_flat = Y_2d.ravel()

    dists, idxs = _nearest_on_centerline(cl_xy, X_flat, Y_flat)
    alpha         = _edge_alpha(dists, half_w, blend_dist, edge_mode)
    existing_flat = arr_full[row_min:row_max, col_min:col_max].ravel()

    valid  = (np.abs(existing_flat - nodata) > 1e-6) if nodata is not None \
             else np.ones(len(existing_flat), dtype=bool)
    active = (alpha > 0.0) & valid

    if burn_mode == 3:
        # ── Longitudinal Gradient ──────────────────────────────────────────
        # Linear invert: upstream_invert at s=0, downstream_invert at s=total_len
        total_s   = max(float(cl_s[-1]), 1e-9)
        t_flat    = np.clip(cl_s[idxs] / total_s, 0.0, 1.0)
        grad_z    = upstream_invert + t_flat * (downstream_invert - upstream_invert)
        # Blend: full gradient elevation at centreline (alpha=1), existing at edge (alpha=0)
        target_flat = existing_flat + alpha * (grad_z - existing_flat)
        # Always Lower-Only: only cut into terrain, never raise it
        new_flat = np.where(active, np.minimum(existing_flat, target_flat), existing_flat)
    else:
        # ── Depth-based modes ─────────────────────────────────────────────
        c_z_flat    = cl_z[idxs]
        target_flat = c_z_flat - alpha * burn_depth

        if burn_mode == 0:      # Replace
            new_flat = np.where(active, target_flat, existing_flat)
        elif burn_mode == 1:    # Lower-Only
            new_flat = np.where(active, np.minimum(existing_flat, target_flat), existing_flat)
        else:                   # Lower-Only with Tolerance
            apply_mask = active & ((existing_flat - target_flat) > tolerance)
            new_flat   = np.where(apply_mask, target_flat, existing_flat)

    arr_full[row_min:row_max, col_min:col_max] = new_flat.reshape(n_rows_bb, n_cols_bb)

    diff_sub  = arr_full[row_min:row_max, col_min:col_max] - orig_arr[row_min:row_max, col_min:col_max]
    n_changed = int((np.abs(diff_sub) > 1e-9).sum())
    area_m2   = n_changed * px * py
    return n_changed, area_m2, total_len, True


# ── Processing Algorithm ──────────────────────────────────────────────────────

class SubcatchDEMChannelBurn(QgsProcessingAlgorithm):

    INPUT_DEM         = 'INPUT_DEM'
    CENTERLINE        = 'CENTERLINE'
    BURN_WIDTH        = 'BURN_WIDTH'
    BURN_DEPTH        = 'BURN_DEPTH'
    BURN_MODE         = 'BURN_MODE'
    TOLERANCE         = 'TOLERANCE'
    EDGE_MODE         = 'EDGE_MODE'
    BLEND_DIST        = 'BLEND_DIST'
    OUTPUT_DEM        = 'OUTPUT_DEM'
    OUTPUT_DIFF       = 'OUTPUT_DIFF'

    _BURN_MODES = [
        'Replace (overwrite DEM inside corridor)',
        'Lower-Only (never raises terrain)',
        'Lower-Only with Tolerance (cut only if > threshold)',
        'Longitudinal Gradient (DEM-sampled levels at line endpoints, linear gradient)',
    ]
    _EDGE_MODES = [
        'None — sharp cutoff at corridor boundary',
        'Edge Buffer — flat extension beyond corridor edge',
        'Feather Blending — cosine taper over blend distance',
    ]

    def tr(self, s):
        return QCoreApplication.translate('Processing', s)

    def createInstance(self):
        return SubcatchDEMChannelBurn()

    def name(self):
        return 'sa_dem_channel_burn_v01'

    def displayName(self):
        return self.tr('DEM Channel Burn V01')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Burns one or more channel corridors into a LiDAR DEM.\n\n"
            "Burn Modes:\n"
            "  Replace — overwrites all corridor pixels to (centreline DEM - burn depth)\n"
            "  Lower-Only — only cuts; never raises terrain (safe default)\n"
            "  Lower-Only with Tolerance — only cuts where depth > threshold\n"
            "  Longitudinal Gradient — automatically samples the DEM at the line's\n"
            "    start and end vertices, then burns a straight-line gradient between\n"
            "    those two elevations along the full centreline length. Only cuts terrain.\n\n"
            "All features in the centreline layer are burned sequentially."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, self.tr('Baseline terrain DEM'),
        ))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CENTERLINE,
            self.tr('Centreline layer (all line features will be burned)'),
            [QgsProcessing.TypeVectorLine],
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.BURN_WIDTH,
            self.tr('Burn width — total corridor width (m)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=10.0, minValue=0.1,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.BURN_MODE, self.tr('Burn mode'),
            options=self._BURN_MODES, defaultValue=1,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.BURN_DEPTH,
            self.tr('Burn depth — depth below centreline DEM profile (m) [modes 0–2 only]'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.5, minValue=0.0, optional=True,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE,
            self.tr('Tolerance depth (m) [Lower-Only with Tolerance only]'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=0.05, minValue=0.0, optional=True,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.EDGE_MODE, self.tr('Edge treatment at corridor boundary'),
            options=self._EDGE_MODES, defaultValue=2,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.BLEND_DIST,
            self.tr('Blend / buffer distance (m) [Edge Buffer and Feather Blending only]'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=2.0, minValue=0.0, optional=True,
        ))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_DEM, self.tr('Output burned DEM'),
        ))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_DIFF,
            self.tr('Difference raster — Z_final minus Z_original (optional)'),
            optional=True, createByDefault=False,
        ))

    def processAlgorithm(self, parameters, context, feedback):
        if not _HAS_DEPS:
            raise QgsProcessingException(
                'GDAL and NumPy are required — check your QGIS installation.'
            )

        dem_layer    = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        cl_source    = self.parameterAsSource(parameters, self.CENTERLINE, context)
        burn_width   = self.parameterAsDouble(parameters, self.BURN_WIDTH, context)
        burn_mode    = self.parameterAsEnum(parameters, self.BURN_MODE, context)
        burn_depth   = self.parameterAsDouble(parameters, self.BURN_DEPTH, context)
        tolerance    = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        edge_mode    = self.parameterAsEnum(parameters, self.EDGE_MODE, context)
        blend_dist   = self.parameterAsDouble(parameters, self.BLEND_DIST, context)
        out_dem_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_DEM, context)

        dem_crs = dem_layer.crs()
        if dem_crs.mapUnits() != QgsUnitTypes.DistanceMeters:
            raise QgsProcessingException(
                f"DEM CRS '{dem_crs.authid()}' does not use metres. "
                "Reproject to a projected metric CRS first."
            )

        feedback.pushInfo("=== DEM Channel Burn V01 ===")
        feedback.pushInfo(f"  Burn mode  : {self._BURN_MODES[burn_mode]}")
        feedback.pushInfo(f"  Burn width : {burn_width:.2f} m")
        if burn_mode != 3:
            feedback.pushInfo(f"  Burn depth : {burn_depth:.3f} m")
        feedback.pushInfo(f"  Edge mode  : {self._EDGE_MODES[edge_mode]}")
        if not _HAS_SCIPY:
            feedback.pushWarning(
                "scipy not found — using slower numpy fallback."
            )

        feedback.pushInfo("\nReading baseline DEM...")
        ds = gdal.Open(dem_layer.source())
        if ds is None:
            raise QgsProcessingException(f"Could not open DEM: {dem_layer.source()}")
        band     = ds.GetRasterBand(1)
        arr_full = band.ReadAsArray().astype(np.float64)
        gt       = ds.GetGeoTransform()
        proj     = ds.GetProjection() or dem_crs.toWkt()
        nodata   = band.GetNoDataValue()
        ds       = None
        orig_arr = arr_full.copy()
        nrows_full, ncols_full = arr_full.shape
        px = abs(gt[1])
        py = abs(gt[5])
        pixel_size = (px + py) / 2.0
        feedback.pushInfo(f"  {ncols_full} x {nrows_full} px at {px:.3f} x {py:.3f} m")
        feedback.setProgress(8)
        if feedback.isCanceled():
            return {}

        feedback.pushInfo("\nLoading centreline features...")
        cl_feats = list(cl_source.getFeatures())
        if not cl_feats:
            raise QgsProcessingException("No features found in the centreline layer.")
        n_feats = len(cl_feats)
        feedback.pushInfo(f"  {n_feats} burn line(s) to process")
        feedback.setProgress(12)

        total_changed = 0
        total_area    = 0.0

        for i, feat in enumerate(cl_feats):
            if feedback.isCanceled():
                break

            label = f"Line {i + 1}/{n_feats}"
            feedback.pushInfo(f"\n--- {label} ---")

            cl_geom = feat.geometry()
            raw_pts = cl_geom.asMultiPolyline()[0] if cl_geom.isMultipart() \
                      else cl_geom.asPolyline()

            if len(raw_pts) < 2:
                feedback.pushWarning(f"  {label}: fewer than 2 vertices — skipping.")
                continue

            cl_verts = [(pt.x(), pt.y()) for pt in raw_pts]

            # For gradient mode, sample the existing DEM at line start/end
            if burn_mode == 3:
                upstream_invert = float(_sample_dem_at_xy(
                    orig_arr, gt, np.array([cl_verts[0]])
                )[0])
                downstream_invert = float(_sample_dem_at_xy(
                    orig_arr, gt, np.array([cl_verts[-1]])
                )[0])
                feedback.pushInfo(
                    f"  Sampled upstream RL  : {upstream_invert:.3f} m"
                )
                feedback.pushInfo(
                    f"  Sampled downstream RL: {downstream_invert:.3f} m"
                )
            else:
                upstream_invert   = 0.0
                downstream_invert = 0.0

            n_changed, area_m2, total_len, overlaps = _burn_single(
                arr_full, orig_arr, gt, cl_verts,
                burn_width, burn_depth, burn_mode, tolerance,
                edge_mode, blend_dist, pixel_size, nodata,
                nrows_full, ncols_full,
                upstream_invert=upstream_invert,
                downstream_invert=downstream_invert,
            )

            if not overlaps:
                feedback.pushWarning(
                    f"  {label}: corridor does not overlap the DEM extent — "
                    "check that the centreline and DEM share the same CRS."
                )
                continue

            total_changed += n_changed
            total_area    += area_m2
            feedback.pushInfo(f"  Length         : {total_len:.1f} m")
            feedback.pushInfo(f"  Pixels modified: {n_changed:,}  ({area_m2:.0f} m²)")
            if burn_mode == 3:
                slope_pct = abs(upstream_invert - downstream_invert) / max(total_len, 1e-9) * 100
                feedback.pushInfo(f"  Bed slope      : {slope_pct:.4f}%  ({slope_pct/100:.6f} m/m)")
            feedback.setProgress(12 + int(68 * (i + 1) / n_feats))

        feedback.pushInfo(f"\n--- All Lines Combined ---")
        feedback.pushInfo(f"  Total pixels modified : {total_changed:,}")
        feedback.pushInfo(f"  Total area modified   : {total_area:.0f} m²")

        feedback.pushInfo("\nWriting burned DEM...")
        out_dir = os.path.dirname(out_dem_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        drv    = gdal.GetDriverByName('GTiff')
        out_ds = drv.Create(
            out_dem_path, ncols_full, nrows_full, 1, gdal.GDT_Float32,
            ['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
        )
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.WriteArray(arr_full.astype(np.float32))
        if nodata is not None:
            out_band.SetNoDataValue(nodata)
        out_band.FlushCache()
        out_ds.FlushCache()
        out_ds = None
        feedback.setProgress(90)

        results = {self.OUTPUT_DEM: out_dem_path}

        diff_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_DIFF, context)
        if diff_path:
            feedback.pushInfo("Writing difference raster...")
            DIFF_NODATA = -9999.0
            diff_full   = np.full((nrows_full, ncols_full), DIFF_NODATA, dtype=np.float32)
            modified    = np.abs(arr_full - orig_arr) > 1e-9
            diff_full[modified] = (arr_full[modified] - orig_arr[modified]).astype(np.float32)
            diff_ds = drv.Create(
                diff_path, ncols_full, nrows_full, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
            )
            diff_ds.SetGeoTransform(gt)
            diff_ds.SetProjection(proj)
            diff_band = diff_ds.GetRasterBand(1)
            diff_band.SetNoDataValue(DIFF_NODATA)
            diff_band.WriteArray(diff_full)
            diff_band.FlushCache()
            diff_ds.FlushCache()
            diff_ds = None
            results[self.OUTPUT_DIFF] = diff_path

        feedback.setProgress(100)
        feedback.pushInfo("\n=== Complete ===")
        return results
