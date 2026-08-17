# -*- coding: utf-8 -*-
"""
Delineate Longest Flow Path using D8 drainage direction raster.

For each sub-catchment polygon, traces the longest flow path from the most
upstream headwater cell to the sub-catchment outlet by walking the GRASS
r.watershed D8 drainage direction grid, then converts the cell chain to a
simplified polyline.  No stream vector layer is required.
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"
__status__  = "Stable"

import math
from collections import deque

import numpy as np
from osgeo import gdal, ogr

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsField,
    QgsFields,
    QgsProcessingException,
    QgsFeatureSink,
    QgsDistanceArea,
    QgsUnitTypes,
    QgsSpatialIndex,
)


# GRASS r.watershed D8 direction codes → (row_offset, col_offset).
# Row 0 is north; row increases southward.  Col 0 is west; col increases eastward.
# Negative codes (-1 … -8) encode boundary/outlet cells; we take abs() before lookup.
_D8 = {
    1: ( 0, +1),   # E
    2: (-1, +1),   # NE
    3: (-1,  0),   # N
    4: (-1, -1),   # NW
    5: ( 0, -1),   # W
    6: (+1, -1),   # SW
    7: (+1,  0),   # S
    8: (+1, +1),   # SE
}


class LongestFlowPathAlgorithm(QgsProcessingAlgorithm):

    INPUT_SUBCATCHMENTS = 'INPUT_SUBCATCHMENTS'
    INPUT_DRAINAGE      = 'INPUT_DRAINAGE'
    INPUT_ACCUMULATION  = 'INPUT_ACCUMULATION'
    INPUT_STREAMS       = 'INPUT_STREAMS'
    INPUT_DEM           = 'INPUT_DEM'
    SIMPLIFY_TOLERANCE  = 'SIMPLIFY_TOLERANCE'
    MIN_AREA_HA         = 'MIN_AREA_HA'
    OUTPUT              = 'OUTPUT'

    def tr(self, s):
        return QCoreApplication.translate('Processing', s)

    def createInstance(self):
        return LongestFlowPathAlgorithm()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'sch_longest_flow_path_v01'

    def displayName(self):
        return self.tr('Subcatchment Longest Flow Path V01')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Delineate Longest Flow Path\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "For each sub-catchment polygon, traces the longest D8 flow path from "
            "the most upstream headwater cell to the sub-catchment outlet using the "
            "drainage direction raster from the Stream and Catchment Generator.\n\n"
            "The path covers both overland and channel portions of the flow route — "
            "no stream vector layer is required.\n\n"
            "Inputs:\n"
            "  • Sub-catchment polygons\n"
            "  • Drainage direction raster (OUTPUT_DRAINAGE from Stream and Catchment Generator)\n"
            "  • Flow accumulation raster (OUTPUT_ACCUMULATION from Stream and Catchment Generator)\n"
            "  • Simplification tolerance (m) — smooths D8 staircase; 0 = auto (2× cell size)\n"
            "  • Minimum sub-catchment area — skip small/sliver polygons\n\n"
            "Output:\n"
            "  • One polyline per sub-catchment\n"
            "    Fields: FLOWPATH_ID, SUBCAT_FID, PATH_LENGTH_M\n\n"
            "Notes:\n"
            "  • DEM CRS must be projected and use metres (e.g. EPSG:2193 NZTM)\n"
            "  • Processing time scales with sub-catchment area × DEM resolution"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_SUBCATCHMENTS,
                self.tr('Sub-catchment polygons'),
                [QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DRAINAGE,
                self.tr('Drainage direction raster (from Stream and Catchment Generator)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_ACCUMULATION,
                self.tr('Flow accumulation raster (from Stream and Catchment Generator)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_STREAMS,
                self.tr('Stream network — optional, improves outlet location'),
                types=[QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DEM,
                self.tr('DEM — optional, tiebreaks outlet snapping by elevation'),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SIMPLIFY_TOLERANCE,
                self.tr('Simplification tolerance (m) — 0 = no smoothing (raw D8 staircase)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0, minValue=0.0, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_AREA_HA,
                self.tr('Minimum sub-catchment area (ha) — 0 = process all'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0, minValue=0.0, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Longest Flow Path'),
            )
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        subcat_layer   = self.parameterAsVectorLayer(parameters, self.INPUT_SUBCATCHMENTS, context)
        drainage_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DRAINAGE, context)
        accum_layer    = self.parameterAsRasterLayer(parameters, self.INPUT_ACCUMULATION, context)
        stream_layer   = self.parameterAsVectorLayer(parameters, self.INPUT_STREAMS, context)
        dem_layer      = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        simplify_tol   = self.parameterAsDouble(parameters, self.SIMPLIFY_TOLERANCE, context)
        min_area_ha    = self.parameterAsDouble(parameters, self.MIN_AREA_HA, context)

        if subcat_layer is None:
            raise QgsProcessingException("Sub-catchment layer not found.")
        if drainage_layer is None:
            raise QgsProcessingException("Drainage direction raster not found.")
        if accum_layer is None:
            raise QgsProcessingException("Flow accumulation raster not found.")

        # ── Optional: stream network for outlet correction ────────────────────
        stream_index = None
        stream_feats = {}
        if stream_layer is not None:
            feedback.pushInfo("Building stream spatial index for outlet correction…")
            stream_index = QgsSpatialIndex()
            for f in stream_layer.getFeatures():
                stream_index.insertFeature(f)
                stream_feats[f.id()] = f

        # ── Optional: DEM for elevation-based outlet tiebreaking ─────────────
        dem_ds      = None
        dem_nodata  = None
        if dem_layer is not None:
            dem_path = dem_layer.source().split('|')[0]
            dem_ds   = gdal.Open(dem_path)
            if dem_ds is None:
                feedback.pushWarning(f"Cannot open DEM raster: {dem_path} — ignoring.")
            else:
                dem_nodata = dem_ds.GetRasterBand(1).GetNoDataValue()

        # Open drainage raster via GDAL
        drain_path = drainage_layer.source().split('|')[0]
        drain_ds   = gdal.Open(drain_path)
        if drain_ds is None:
            raise QgsProcessingException(f"Cannot open drainage raster: {drain_path}")

        gt     = drain_ds.GetGeoTransform()   # (x0, px_w, 0, y0, 0, -px_h)
        n_rows = drain_ds.RasterYSize
        n_cols = drain_ds.RasterXSize
        cell_w = abs(gt[1])
        cell_h = abs(gt[5])
        cell_sz = (cell_w + cell_h) / 2.0


        ap = accum_layer.source().split('|')[0]
        accum_ds = gdal.Open(ap)
        if accum_ds is None:
            raise QgsProcessingException(f"Cannot open accumulation raster: {ap}")

        drain_nodata = drain_ds.GetRasterBand(1).GetNoDataValue()
        accum_nodata = accum_ds.GetRasterBand(1).GetNoDataValue()

        # Output sink
        out_fields = QgsFields()
        out_fields.append(QgsField('FLOWPATH_ID',   QVariant.String, len=20))
        out_fields.append(QgsField('SUBCAT_FID',    QVariant.Int))
        out_fields.append(QgsField('PATH_LENGTH_M', QVariant.Double))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, QgsWkbTypes.LineString, subcat_layer.crs(),
        )
        if sink is None:
            raise QgsProcessingException("Could not create output layer.")

        area_calc = QgsDistanceArea()
        area_calc.setSourceCrs(subcat_layer.crs(), context.transformContext())
        area_calc.setEllipsoid(
            context.project().ellipsoid() if context.project() else 'WGS84'
        )

        n_feats = subcat_layer.featureCount()
        pct     = 100.0 / n_feats if n_feats else 0
        fp_num  = 1

        feedback.pushInfo(
            f"  {n_feats} sub-catchment(s) | "
            f"cell {cell_sz:.3f} m | simplify {simplify_tol:.3f} m"
        )

        for i, feat in enumerate(subcat_layer.getFeatures()):
            if feedback.isCanceled():
                break

            fid  = feat.id()
            geom = feat.geometry()

            if min_area_ha > 0:
                a_ha = area_calc.convertAreaMeasurement(
                    area_calc.measureArea(geom), QgsUnitTypes.AreaHectares
                )
                if a_ha < min_area_ha:
                    feedback.pushInfo(f"  FID {fid}: {a_ha:.2f} ha < min — skipped.")
                    feedback.setProgress(int((i + 1) * pct))
                    continue

            try:
                path_geom, path_len = self._trace(
                    geom, drain_ds, accum_ds,
                    gt, n_rows, n_cols, cell_w, cell_h, simplify_tol,
                    drain_nodata, accum_nodata,
                )
            except Exception as exc:
                feedback.pushWarning(f"  FID {fid}: {exc} — skipped.")
                feedback.setProgress(int((i + 1) * pct))
                continue

            if path_geom is None or path_geom.isEmpty():
                feedback.pushWarning(f"  FID {fid}: no flow path found — skipped.")
                feedback.setProgress(int((i + 1) * pct))
                continue

            # Optional: extend LFP endpoint along stream to catchment boundary
            if stream_index is not None:
                path_geom, path_len = self._extend_along_stream(
                    path_geom, geom, stream_feats, stream_index, cell_sz,
                    accum_ds=accum_ds, gt=gt, accum_nodata=accum_nodata,
                )

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(path_geom)
            out_f.setAttribute('FLOWPATH_ID',   f"FP-{fp_num:05d}")
            out_f.setAttribute('SUBCAT_FID',    fid)
            out_f.setAttribute('PATH_LENGTH_M', round(path_len, 3))
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)
            fp_num += 1
            feedback.setProgress(int((i + 1) * pct))

        drain_ds = None
        accum_ds = None
        feedback.pushInfo(f"  {fp_num - 1} flow path(s) written.")
        return {self.OUTPUT: dest_id}

    # ── Per-polygon flow path tracer ──────────────────────────────────────────

    def _trace(self, poly_geom, drain_ds, accum_ds,
               gt, n_rows, n_cols, cell_w, cell_h, simplify_tol,
               drain_nodata=None, accum_nodata=None):
        """
        Return (QgsGeometry polyline, path_length_m) for the longest D8 flow
        path within poly_geom, or (None, 0) if the polygon contains no valid
        drainage cells.

        Uses DP on the D8 upstream tree:
          1. BFS from the outlet backwards through the upstream graph computes
             dist_to_outlet[cell] for every reachable polygon cell.
          2. The cell with the maximum dist_to_outlet is the true LFP headwater.
          3. D8 is followed forward from that headwater to produce the output line.
        """
        bbox = poly_geom.boundingBox()

        _PAD = 1
        col0 = max(0,        int(math.floor((bbox.xMinimum() - gt[0]) / gt[1])) - _PAD)
        row0 = max(0,        int(math.floor((bbox.yMaximum() - gt[3]) / gt[5])) - _PAD)
        col1 = min(n_cols-1, int(math.ceil( (bbox.xMaximum() - gt[0]) / gt[1])) + _PAD)
        row1 = min(n_rows-1, int(math.ceil( (bbox.yMinimum() - gt[3]) / gt[5])) + _PAD)

        win_w = col1 - col0 + 1
        win_h = row1 - row0 + 1
        if win_w < 1 or win_h < 1:
            return None, 0.0

        drain_arr = drain_ds.GetRasterBand(1).ReadAsArray(col0, row0, win_w, win_h)
        if drain_arr is None:
            return None, 0.0

        mask = self._rasterize_poly(poly_geom, gt, col0, row0, win_w, win_h)
        if not mask.any():
            return None, 0.0

        accum_arr = accum_ds.GetRasterBand(1).ReadAsArray(col0, row0, win_w, win_h)
        if accum_arr is None:
            return None, 0.0

        def _step(dr, dc):
            return (math.sqrt(cell_w**2 + cell_h**2)
                    if (dr != 0 and dc != 0) else
                    (cell_h if dc == 0 else cell_w))

        # ── Build upstream tree ───────────────────────────────────────────────
        # upstream[(r,c)] = [((ur, uc), step_dist), …]
        # step_dist = full geometric distance from (ur, uc) to (r, c) including
        # any bridge hops, so dist_to_outlet can be summed directly.
        upstream = {}
        outlets  = []

        rows_idx, cols_idx = np.where(mask)
        for r, c in zip(rows_idx.tolist(), cols_idx.tolist()):
            d    = int(drain_arr[r, c])
            if drain_nodata is not None and d == int(drain_nodata):
                continue
            absd = abs(d)
            if absd not in _D8:
                outlets.append((r, c))
                continue
            dr, dc         = _D8[absd]
            down_r, down_c = r + dr, c + dc
            step           = _step(dr, dc)

            if d < 0:
                outlets.append((r, c))
            elif not (0 <= down_r < win_h and 0 <= down_c < win_w
                      and bool(mask[down_r, down_c])):
                tr, tc, extra = self._bridge_to_mask(
                    down_r, down_c, drain_arr, mask, win_h, win_w,
                    cell_w, cell_h, drain_nodata,
                )
                if tr is not None:
                    upstream.setdefault((tr, tc), []).append(((r, c), step + extra))
                else:
                    outlets.append((r, c))
            else:
                upstream.setdefault((down_r, down_c), []).append(((r, c), step))

        if not outlets:
            return None, 0.0

        # ── Identify the true outlet ──────────────────────────────────────────
        def _accum_val(rc):
            v = float(accum_arr[rc[0], rc[1]])
            if accum_nodata is not None and v == accum_nodata:
                return -1e38
            return v

        connected  = [o for o in outlets if upstream.get(o)]
        candidates = connected if connected else outlets
        outlet     = (candidates[0] if len(candidates) == 1
                      else max(candidates,
                               key=lambda o: (self._count_upstream(o, upstream),
                                              _accum_val(o))))

        # ── DP: dist_to_outlet for every reachable cell ───────────────────────
        # BFS outwards from the outlet through the upstream tree.
        # Each entry in upstream[(node)] is an (upstream_cell, step) pair, so
        # dist_to_outlet[upstream_cell] = dist_to_outlet[node] + step.
        dist_to_outlet = {outlet: 0.0}
        q = deque([outlet])
        while q:
            node   = q.popleft()
            d_node = dist_to_outlet[node]
            for (up_cell, step) in upstream.get(node, []):
                new_d = d_node + step
                if up_cell not in dist_to_outlet or new_d > dist_to_outlet[up_cell]:
                    dist_to_outlet[up_cell] = new_d
                    q.append(up_cell)

        # ── Find the true headwater (farthest cell from outlet) ───────────────
        # Headwaters are polygon cells that no other polygon cell drains into.
        has_upstream = set(upstream.keys())
        headwaters   = [c for c in dist_to_outlet if c not in has_upstream]
        if not headwaters:
            headwaters = list(dist_to_outlet.keys())

        best_hw  = max(headwaters, key=lambda c: dist_to_outlet.get(c, 0.0))
        path_len = dist_to_outlet.get(best_hw, 0.0)

        if path_len <= 0.0 or best_hw == outlet:
            return None, 0.0

        # ── Trace forward from headwater to outlet ────────────────────────────
        chain       = [best_hw]
        current     = best_hw
        visited_fwd = {best_hw}

        for _ in range(win_h * win_w + 100):
            if current == outlet:
                break
            r, c = current
            d    = int(drain_arr[r, c])
            absd = abs(d)
            if absd not in _D8 or (drain_nodata is not None and d == int(drain_nodata)):
                break
            dr, dc = _D8[absd]
            nr, nc = r + dr, c + dc

            if 0 <= nr < win_h and 0 <= nc < win_w and bool(mask[nr, nc]):
                next_cell = (nr, nc)
            else:
                tr, tc, _ = self._bridge_to_mask(
                    nr, nc, drain_arr, mask, win_h, win_w,
                    cell_w, cell_h, drain_nodata,
                )
                if tr is None:
                    break
                next_cell = (tr, tc)

            if next_cell in visited_fwd:
                break
            visited_fwd.add(next_cell)
            chain.append(next_cell)
            current = next_cell

        if len(chain) < 2:
            return None, 0.0

        # ── Convert cell indices to map coordinates (cell centres) ────────────
        pts = [QgsPointXY(gt[0] + (col0 + c + 0.5) * gt[1],
                          gt[3] + (row0 + r + 0.5) * gt[5])
               for (r, c) in chain]

        line = QgsGeometry.fromPolylineXY(pts)
        if simplify_tol > 0 and not line.isEmpty():
            line = line.simplify(simplify_tol)

        return line, path_len

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extend_along_stream(self, path_geom, catchment_geom,
                              stream_feats, stream_index, cell_sz,
                              accum_ds=None, gt=None, accum_nodata=None):
        """
        Two-step approach that needs no direction guessing:

        Step 1 — Find the true outlet: scan every stream-boundary crossing inside
                  the catchment and pick the one with the highest flow accumulation.
                  That crossing vertex IS the outlet; the stream segment that ends
                  there IS the outlet stream.

        Step 2 — Extend: locate the snap vertex (outlet-stream vertex nearest to the
                  LFP endpoint), then extract every vertex that lies between snap_idx
                  and outlet_idx.  Both indices are known, so the direction is explicit.
        """
        pts = path_geom.asPolyline()
        if len(pts) < 2:
            return path_geom, path_geom.length()

        endpoint      = pts[-1]
        endpoint_geom = QgsGeometry.fromPointXY(endpoint)
        snap_dist     = cell_sz * 20
        bnd_tol       = cell_sz * 3

        try:
            _line_type = QgsWkbTypes.GeometryType.LineGeometry
        except AttributeError:
            _line_type = QgsWkbTypes.LineGeometry   # type: ignore

        # Build catchment boundary linestring
        if catchment_geom.isMultipart():
            all_rings = [r for poly in catchment_geom.asMultiPolygon() for r in poly if r]
        else:
            all_rings = [r for r in catchment_geom.asPolygon() if r]
        if not all_rings:
            return path_geom, path_geom.length()
        boundary = (QgsGeometry.fromPolylineXY(all_rings[0])
                    if len(all_rings) == 1
                    else QgsGeometry.fromMultiPolylineXY(all_rings))

        # ── Step 1: identify the true outlet ─────────────────────────────────
        best_acc        = -1e38
        outlet_line_pts = None
        outlet_idx      = None   # index inside outlet_line_pts

        for fid in stream_index.intersects(catchment_geom.boundingBox()):
            sf = stream_feats.get(fid)
            if sf is None:
                continue
            clipped = sf.geometry().intersection(catchment_geom)
            if clipped is None or clipped.isEmpty():
                continue
            if QgsWkbTypes.geometryType(clipped.wkbType()) != _line_type:
                continue
            lines = (clipped.asMultiPolyline() if clipped.isMultipart()
                     else [clipped.asPolyline()])
            for line_pts in lines:
                if len(line_pts) < 2:
                    continue
                # Check both endpoints — the one on the boundary is an outlet candidate
                for e_idx, e_pt in [(0, line_pts[0]),
                                    (len(line_pts) - 1, line_pts[-1])]:
                    if boundary.distance(QgsGeometry.fromPointXY(e_pt)) > bnd_tol:
                        continue
                    acc = self._sample_accum(e_pt, accum_ds, gt, accum_nodata)
                    if acc > best_acc:
                        best_acc        = acc
                        outlet_line_pts = line_pts
                        outlet_idx      = e_idx

        if outlet_line_pts is None:
            return path_geom, path_geom.length()

        # ── Step 2: snap LFP endpoint to outlet stream, extract extension ────
        snap_idx = 0
        snap_d   = float('inf')
        for idx, p in enumerate(outlet_line_pts):
            d = endpoint_geom.distance(QgsGeometry.fromPointXY(p))
            if d < snap_d:
                snap_d   = d
                snap_idx = idx

        if snap_d > snap_dist or snap_idx == outlet_idx:
            return path_geom, path_geom.length()

        # Extract vertices between snap and outlet — direction is explicit
        if outlet_idx > snap_idx:
            ext = outlet_line_pts[snap_idx + 1 : outlet_idx + 1]
        else:
            ext = list(reversed(outlet_line_pts[outlet_idx : snap_idx]))

        if not ext:
            return path_geom, path_geom.length()

        # Sanity check: extension must move us TOWARD the outlet, not away.
        # If the D8 trace has already gone past the snap vertex (further downstream),
        # ext[0] will be upstream of pts[-1] and this check prevents backtracking.
        outlet_pt_geom = QgsGeometry.fromPointXY(outlet_line_pts[outlet_idx])
        d_before = endpoint_geom.distance(outlet_pt_geom)
        d_after  = QgsGeometry.fromPointXY(ext[0]).distance(outlet_pt_geom)
        if d_after >= d_before:
            return path_geom, path_geom.length()

        # Include the snap vertex as the explicit bridge between D8 endpoint
        # and the stream sequence so the joined geometry has no gap.
        snap_pt  = outlet_line_pts[snap_idx]
        new_pts  = pts + [snap_pt] + ext
        new_geom = QgsGeometry.fromPolylineXY(new_pts)
        return new_geom, new_geom.length()

    def _sample_accum(self, pt, accum_ds, gt, accum_nodata):
        """Sample the flow accumulation raster at a map-coordinate point."""
        if accum_ds is None or gt is None:
            return -1e38
        col = int((pt.x() - gt[0]) / gt[1])
        row = int((pt.y() - gt[3]) / gt[5])
        n_cols = accum_ds.RasterXSize
        n_rows = accum_ds.RasterYSize
        if not (0 <= col < n_cols and 0 <= row < n_rows):
            return -1e38
        try:
            arr = accum_ds.GetRasterBand(1).ReadAsArray(col, row, 1, 1)
        except Exception:
            return -1e38
        if arr is None:
            return -1e38
        v = float(arr[0, 0])
        if accum_nodata is not None and v == accum_nodata:
            return -1e38
        return v

    def _count_upstream(self, start, upstream):
        """Count polygon cells reachable upstream from start via the upstream graph."""
        count = 0
        stack = [start]
        seen  = {start}
        while stack:
            node = stack.pop()
            count += 1
            for (nb, _) in upstream.get(node, []):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return count

    def _bridge_to_mask(self, r, c, drain_arr, mask, win_h, win_w,
                        cell_w, cell_h, drain_nodata, max_hops=5):
        """
        From (r, c) outside the polygon mask, follow D8 up to max_hops steps
        looking for a cell that re-enters the mask.  Handles irregular polygon
        boundaries where a flow path temporarily exits before returning.
        Returns (target_r, target_c, bridge_distance) or (None, None, 0).
        """
        dist = 0.0
        for _ in range(max_hops):
            if not (0 <= r < win_h and 0 <= c < win_w):
                return None, None, 0.0
            d = int(drain_arr[r, c])
            if drain_nodata is not None and d == int(drain_nodata):
                return None, None, 0.0
            absd = abs(d)
            if absd not in _D8:
                return None, None, 0.0
            dr_step, dc_step = _D8[absd]
            diag = (dr_step != 0) and (dc_step != 0)
            dist += (math.sqrt(cell_w**2 + cell_h**2) if diag
                     else (cell_h if dc_step == 0 else cell_w))
            r, c = r + dr_step, c + dc_step
            if not (0 <= r < win_h and 0 <= c < win_w):
                return None, None, 0.0
            if mask[r, c]:
                return r, c, dist
        return None, None, 0.0

    def _rasterize_poly(self, poly_geom, gt, col0, row0, win_w, win_h):
        """Rasterize a QgsGeometry polygon to a (win_h × win_w) boolean numpy array."""
        mem_drv = gdal.GetDriverByName('MEM')
        mask_ds = mem_drv.Create('', win_w, win_h, 1, gdal.GDT_Byte)
        mask_ds.SetGeoTransform((
            gt[0] + col0 * gt[1],
            gt[1], 0.0,
            gt[3] + row0 * gt[5],
            0.0, gt[5],
        ))
        mask_ds.GetRasterBand(1).Fill(0)

        ogr_ds  = ogr.GetDriverByName('Memory').CreateDataSource('')
        ogr_lyr = ogr_ds.CreateLayer('')
        feat    = ogr.Feature(ogr_lyr.GetLayerDefn())
        feat.SetGeometry(ogr.CreateGeometryFromWkt(poly_geom.asWkt()))
        ogr_lyr.CreateFeature(feat)

        gdal.RasterizeLayer(mask_ds, [1], ogr_lyr, burn_values=[1],
                            options=['ALL_TOUCHED=TRUE'])
        arr     = mask_ds.GetRasterBand(1).ReadAsArray().astype(bool)
        mask_ds = None
        ogr_ds  = None
        return arr
