# -*- coding: utf-8 -*-
"""
Validation suite for Subcatch_LongestFlowPath.py V02 (Issue 1-6 fixes).

Runs real GRASS r.watershed via QGIS Processing - no QGIS GUI needed, but
DOES need a full QgsApplication + the GRASS provider, which on this
standalone-QGIS-on-Windows install needs two class-attribute patches
below because grassprovider.grass_utils.GrassUtils's folder/binary
auto-detection doesn't recognise this install's "grass84" (major+minor,
no dash) naming - see the comments at the GrassUtils.command/.path lines.
If GRASS is set up differently on another machine, those two lines are
the only thing that should need changing.

Run with:
    "<QGIS install>/bin/python-qgis-qt6.bat" tests/test_longest_flow_path.py

Environment variables (all optional, sensible defaults for this machine):
    QGIS_PREFIX_PATH   QGIS install's apps/qgis-qt6 folder
    ACH_PLUGINS_PARENT Folder containing the AucklandCatchmentHydrology
                        plugin folder (defaults to two levels above this
                        file, i.e. the Plugins folder it normally lives in)
    ACH_GRASS_BAT       Path to the GRASS launcher .bat (only needed if
                        GrassUtils can't auto-detect it on your install)
    ACH_GRASS_DIR       Path to the GRASS install folder (same caveat)
"""
import sys
import os
import math
import time
import tempfile
import csv

QGIS_PREFIX = os.environ.get('QGIS_PREFIX_PATH', r"C:\Program Files\QGISQT6 3.44.2\apps\qgis-qt6")
PLUGINS_PARENT = os.environ.get(
    'ACH_PLUGINS_PARENT',
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

sys.path.insert(0, os.path.join(QGIS_PREFIX, 'python', 'plugins'))
sys.path.insert(0, PLUGINS_PARENT)

from qgis.core import QgsApplication
QgsApplication.setPrefixPath(QGIS_PREFIX, True)
qgs = QgsApplication([], False)
qgs.initQgis()

import numpy as np
from osgeo import gdal, ogr, osr

import processing
try:
    from grassprovider.grass_utils import GrassUtils
except ImportError:
    from grassprovider.Grass7Utils import Grass7Utils as GrassUtils
# --- environment-specific GRASS auto-detect workaround (see module docstring) ---
_GRASS_BAT = os.environ.get('ACH_GRASS_BAT', r"C:\Program Files\QGISQT6 3.44.2\bin\grass84.bat")
_GRASS_DIR = os.environ.get('ACH_GRASS_DIR', r"C:\Program Files\QGISQT6 3.44.2\apps\grass\grass84")
if os.path.isfile(_GRASS_BAT):
    GrassUtils.command = _GRASS_BAT
if os.path.isdir(_GRASS_DIR):
    GrassUtils.path = _GRASS_DIR
from processing.core.Processing import Processing
Processing.initialize()
_WATERSHED_ALGO = (
    "grass:r.watershed"
    if QgsApplication.processingRegistry().algorithmById("grass:r.watershed")
    else "grass7:r.watershed"
)
# ----------------------------------------------------------------------------

from qgis.core import (
    QgsGeometry, QgsPointXY, QgsRasterLayer, QgsRectangle,
)

import importlib
mod = importlib.import_module("AucklandCatchmentHydrology.algorithms.Subcatch_LongestFlowPath")
Algo = mod.LongestFlowPathAlgorithm
_D8 = mod._D8

EPSG = 2193
SRS = osr.SpatialReference()
SRS.ImportFromEPSG(EPSG)

RESULTS = []  # (test_name, passed, detail)


def record(name, passed, detail=''):
    RESULTS.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}  {detail}")


def write_raster(path, arr, dtype, gt, nodata=-9999):
    drv = gdal.GetDriverByName('GTiff')
    h, w = arr.shape
    ds = drv.Create(path, w, h, 1, dtype)
    ds.SetGeoTransform(gt)
    ds.SetProjection(SRS.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(nodata)
    ds = None


def run_watershed(dem_path, n_x, n_y, gt, threshold, tmp):
    drainage_path = os.path.join(tmp, f"drain_{int(time.time()*1000)}.tif")
    accum_path    = os.path.join(tmp, f"accum_{int(time.time()*1000)}.tif")
    basin_path    = os.path.join(tmp, f"basin_{int(time.time()*1000)}.tif")
    dem_layer = QgsRasterLayer(dem_path, "dem")
    assert dem_layer.isValid(), f"invalid DEM layer at {dem_path}"
    extent = QgsRectangle(gt[0], gt[3] + n_y * gt[5], gt[0] + n_x * gt[1], gt[3])
    processing.run(
        _WATERSHED_ALGO,
        {
            'elevation': dem_layer,
            'threshold': threshold,
            '-4': False, '-s': True, '-m': False, '-b': False, '-a': True,
            'accumulation': accum_path,
            'drainage': drainage_path,
            'basin': basin_path,
            'stream': 'TEMPORARY_OUTPUT',
            'GRASS_REGION_PARAMETER': extent,
            'GRASS_REGION_CELLSIZE_PARAMETER': abs(gt[1]),
            'GRASS_RASTER_FORMAT_OPT': '',
            'GRASS_RASTER_FORMAT_META': '',
        },
    )
    return drainage_path, accum_path, basin_path


# ============================================================================
# Test 1 — Direction test: 8 compass-direction planes, real r.watershed
# ============================================================================
def test_1_direction():
    tmp = tempfile.mkdtemp(prefix='lfp_t1_')
    n = 21
    gt = (0, 1, 0, n, 0, -1)
    planes = {
        'N': (-1, 0), 'NE': (-1, 1), 'E': (0, 1), 'SE': (1, 1),
        'S': (1, 0), 'SW': (1, -1), 'W': (0, -1), 'NW': (-1, -1),
    }
    all_ok = True
    detail_bits = []
    for name, (dr, dc) in planes.items():
        elev = np.zeros((n, n), dtype=np.float32)
        for r in range(n):
            for c in range(n):
                elev[r, c] = 100.0 - (dr * r + dc * c) * 1.0
        dem_path = os.path.join(tmp, f"dem_{name}.tif")
        write_raster(dem_path, elev, gdal.GDT_Float32, gt)
        drain_path, _, _ = run_watershed(dem_path, n, n, gt, threshold=10, tmp=tmp)
        drain_ds = gdal.Open(drain_path)
        arr = drain_ds.GetRasterBand(1).ReadAsArray()
        code = int(arr[n // 2, n // 2])
        offset = _D8.get(abs(code))
        ok = offset == (dr, dc)
        all_ok = all_ok and ok
        detail_bits.append(f"{name}:{'ok' if ok else f'FAIL(code={code},offset={offset},expected={(dr,dc)})'}")
        drain_ds = None
    record("1. Direction test (8 planes, real r.watershed)", all_ok, "; ".join(detail_bits))


# ============================================================================
# Test 2 — Accumulation test (the key regression test)
# ============================================================================
def test_2_accumulation():
    tmp = tempfile.mkdtemp(prefix='lfp_t2_')
    rng = np.random.RandomState(42)
    n = 400
    gt = (0, 1, 0, n, 0, -1)

    # Tilted surface (draining broadly south) + random Gaussian bumps.
    yy, xx = np.mgrid[0:n, 0:n]
    elev = (n - yy) * 0.05 + xx * 0.01
    for _ in range(25):
        cx, cy = rng.uniform(0, n), rng.uniform(0, n)
        amp = rng.uniform(1.0, 4.0)
        sigma = rng.uniform(15, 40)
        elev += amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2)))
    elev = elev.astype(np.float32)

    dem_path = os.path.join(tmp, "dem.tif")
    write_raster(dem_path, elev, gdal.GDT_Float32, gt)

    threshold = 4000
    drain_path, accum_path, basin_path = run_watershed(dem_path, n, n, gt, threshold, tmp)

    drain_ds = gdal.Open(drain_path)
    accum_ds = gdal.Open(accum_path)
    basin_ds = gdal.Open(basin_path)
    basin_arr = basin_ds.GetRasterBand(1).ReadAsArray()
    drain_nodata = drain_ds.GetRasterBand(1).GetNoDataValue()
    accum_nodata = accum_ds.GetRasterBand(1).GetNoDataValue()

    basin_ids, counts = np.unique(basin_arr, return_counts=True)
    algo = Algo()

    n_basins_tested = 0
    n_failed = 0
    fail_details = []

    for bid, cell_count in zip(basin_ids.tolist(), counts.tolist()):
        if bid <= 0:
            continue
        if cell_count <= 3000:   # only basins over 3000 m^2 (1 m cells)
            continue

        # Exclude basins touching the raster's outer edge. GRASS's own
        # r.watershed docs warn these can be incomplete ("Areas along edges
        # may not be large enough to create an exterior watershed basin"),
        # and in practice the flow direction right at a DEM's edge is
        # unreliable (no neighbour beyond the edge to rank against) -
        # exactly the kind of edge effect any hydrology DEM workflow avoids
        # by buffering the DEM well beyond the area of interest.
        rows_idx, cols_idx = np.where(basin_arr == bid)
        if rows_idx.min() == 0 or rows_idx.max() == n - 1 or \
           cols_idx.min() == 0 or cols_idx.max() == n - 1:
            continue

        # Polygonize this one basin id directly with gdal.Polygonize on a
        # binary mask, then take the largest-area resulting polygon (basin
        # ids can be split into multiple disconnected blobs at image edges).
        mask = (basin_arr == bid).astype(np.uint8)
        mem_drv = gdal.GetDriverByName('MEM')
        mask_ds = mem_drv.Create('', n, n, 1, gdal.GDT_Byte)
        mask_ds.SetGeoTransform(gt)
        mask_ds.GetRasterBand(1).WriteArray(mask)

        ogr_ds = ogr.GetDriverByName('Memory').CreateDataSource('')
        ogr_lyr = ogr_ds.CreateLayer('poly')
        ogr_lyr.CreateField(ogr.FieldDefn('val', ogr.OFTInteger))
        gdal.Polygonize(mask_ds.GetRasterBand(1), mask_ds.GetRasterBand(1), ogr_lyr, 0)

        best_feat = None
        best_area = 0.0
        for f in ogr_lyr:
            if f.GetField('val') != 1:
                continue
            g = f.GetGeometryRef()
            a = g.GetArea()
            if a > best_area:
                best_area = a
                best_feat = f
        if best_feat is None:
            continue
        poly_geom = QgsGeometry.fromWkt(best_feat.GetGeometryRef().ExportToWkt())

        n_basins_tested += 1
        line, d8_length, bridge_cells = algo._trace(
            poly_geom, drain_ds, accum_ds, gt, n, n, abs(gt[1]), abs(gt[5]), 0.0,
            drain_nodata=drain_nodata, accum_nodata=accum_nodata,
        )
        if line is None:
            n_failed += 1
            fail_details.append(f"basin {bid}: no path traced")
            continue

        pts = line.asPolyline()
        # Re-derive (row, col) for each vertex (simplify=0, so every vertex
        # is a visited cell centre) and sample accumulation at each.
        def rc_of(pt):
            col = int((pt.x() - gt[0]) / gt[1])
            row = int((pt.y() - gt[3]) / gt[5])
            return row, col

        accum_vals = []
        bridge_steps = []
        prev_rc = None
        for pt in pts:
            r, c = rc_of(pt)
            accum_vals.append(float(accum_ds.GetRasterBand(1).ReadAsArray(c, r, 1, 1)[0, 0]))
            if prev_rc is not None:
                dr = abs(r - prev_rc[0])
                dc = abs(c - prev_rc[1])
                bridge_steps.append(dr > 1 or dc > 1)
            prev_rc = (r, c)

        # (a) accumulation never decreases headwater->outlet, ignoring bridge steps
        decreases = sum(
            1 for i in range(len(accum_vals) - 1)
            if not bridge_steps[i] and accum_vals[i + 1] < accum_vals[i] - 1e-6
        )
        # (b) outlet accumulation >= 95% of basin cell count
        outlet_acc = accum_vals[-1]
        pct_of_basin = outlet_acc / cell_count if cell_count else 0.0
        # (c) headwater lies inside the cell-centre mask
        bbox = poly_geom.boundingBox()
        _PAD = 1
        col0 = max(0, int(math.floor((bbox.xMinimum() - gt[0]) / gt[1])) - _PAD)
        row0 = max(0, int(math.floor((bbox.yMaximum() - gt[3]) / gt[5])) - _PAD)
        col1 = min(n - 1, int(math.ceil((bbox.xMaximum() - gt[0]) / gt[1])) + _PAD)
        row1 = min(n - 1, int(math.ceil((bbox.yMinimum() - gt[3]) / gt[5])) + _PAD)
        win_w, win_h = col1 - col0 + 1, row1 - row0 + 1
        mask_centre = algo._rasterize_poly(poly_geom, gt, col0, row0, win_w, win_h, all_touched=False)
        hw_r, hw_c = rc_of(pts[0])
        hw_inside = (0 <= hw_r - row0 < win_h and 0 <= hw_c - col0 < win_w
                     and bool(mask_centre[hw_r - row0, hw_c - col0]))

        ok = (decreases == 0) and (pct_of_basin >= 0.95) and hw_inside
        if not ok:
            n_failed += 1
            fail_details.append(
                f"basin {bid}: decreases={decreases} outlet%={pct_of_basin:.3f} hw_inside={hw_inside}"
            )

    passed = n_basins_tested > 0 and n_failed == 0
    record(
        "2. Accumulation test (400x400 synthetic terrain, real r.watershed)",
        passed,
        f"{n_basins_tested} basin(s) tested, {n_failed} failed"
        + (("; " + "; ".join(fail_details[:5])) if fail_details else ""),
    )


# ============================================================================
# Test 3 — Known-geometry V-valley test
# ============================================================================
def test_3_known_geometry():
    tmp = tempfile.mkdtemp(prefix='lfp_t3_')
    n = 301
    gt = (0, 1, 0, n, 0, -1)
    long_slope = 0.02    # 2% longitudinal, draining north (toward row 0)
    side_slope = 0.10    # 10% side slopes
    cx = n // 2

    yy, xx = np.mgrid[0:n, 0:n]
    # Longitudinal: elevation rises going south (away from the row-0 outlet).
    long_elev = (n - 1 - yy) * long_slope
    # Side slopes: valley centre at col cx, rising with distance from centre.
    side_elev = np.abs(xx - cx) * side_slope
    elev = (long_elev + side_elev).astype(np.float32)

    dem_path = os.path.join(tmp, "dem.tif")
    write_raster(dem_path, elev, gdal.GDT_Float32, gt)

    drain_path, accum_path, _ = run_watershed(dem_path, n, n, gt, threshold=500, tmp=tmp)
    drain_ds = gdal.Open(drain_path)
    accum_ds = gdal.Open(accum_path)
    drain_nodata = drain_ds.GetRasterBand(1).GetNoDataValue()
    accum_nodata = accum_ds.GetRasterBand(1).GetNoDataValue()

    poly = QgsGeometry.fromPolygonXY([[
        QgsPointXY(0, 0), QgsPointXY(n, 0), QgsPointXY(n, n), QgsPointXY(0, n), QgsPointXY(0, 0),
    ]])

    algo = Algo()
    line, d8_length, bridge_cells = algo._trace(
        poly, drain_ds, accum_ds, gt, n, n, abs(gt[1]), abs(gt[5]), 0.0,
        drain_nodata=drain_nodata, accum_nodata=accum_nodata,
    )
    ok = line is not None
    detail = ""
    if ok:
        pts = line.asPolyline()
        hw, outlet = pts[0], pts[-1]
        # Direct check on what the user actually asked:
        # ~450 m +/- 2 m total length from a southern corner to the valley-centre outlet.
        near_south_corner = hw.y() > (n - 10)
        near_outlet_row = outlet.y() < 10
        near_centre_col = abs(outlet.x() - cx) < 3
        length_ok = abs(d8_length - 450.0) <= 2.0
        ok = near_south_corner and near_outlet_row and near_centre_col and length_ok
        detail = (f"headwater={hw}, outlet={outlet}, d8_length={d8_length:.2f}m "
                  f"(expect ~450m), near_south_corner={near_south_corner}, "
                  f"near_outlet_row={near_outlet_row}, near_centre_col={near_centre_col}")
    record("3. Known-geometry V-valley test", ok, detail)
    return d8_length if ok else None


# ============================================================================
# Test 4 — Length consistency test (pure geometry, no GRASS)
# ============================================================================
def test_4_length_consistency():
    tmp = tempfile.mkdtemp(prefix='lfp_t4_')
    n = 60
    gt = (0, 1, 0, n, 0, -1)
    # Simple uniform-slope plane draining to one corner, with enough jitter
    # across rows/cols that simplify(2m) will actually change vertex count.
    rng = np.random.RandomState(7)
    elev = np.zeros((n, n), dtype=np.float32)
    for r in range(n):
        for c in range(n):
            elev[r, c] = (n - r) * 1.0 + (n - c) * 1.0 + rng.uniform(-0.05, 0.05)
    dem_path = os.path.join(tmp, "dem.tif")
    write_raster(dem_path, elev, gdal.GDT_Float32, gt)
    drain_path, accum_path, _ = run_watershed(dem_path, n, n, gt, threshold=50, tmp=tmp)
    drain_ds = gdal.Open(drain_path)
    accum_ds = gdal.Open(accum_path)
    drain_nodata = drain_ds.GetRasterBand(1).GetNoDataValue()
    accum_nodata = accum_ds.GetRasterBand(1).GetNoDataValue()

    poly = QgsGeometry.fromPolygonXY([[
        QgsPointXY(0, 0), QgsPointXY(n, 0), QgsPointXY(n, n), QgsPointXY(0, n), QgsPointXY(0, 0),
    ]])
    algo = Algo()

    line0, d8_0, _ = algo._trace(poly, drain_ds, accum_ds, gt, n, n, 1.0, 1.0, 0.0,
                                  drain_nodata=drain_nodata, accum_nodata=accum_nodata)
    line2, d8_2, _ = algo._trace(poly, drain_ds, accum_ds, gt, n, n, 1.0, 1.0, 2.0,
                                  drain_nodata=drain_nodata, accum_nodata=accum_nodata)

    ok = line0 is not None and line2 is not None
    detail = ""
    if ok:
        geom0_len = line0.length()
        d8_unchanged = abs(d8_0 - d8_2) < 1e-9
        geom0_matches_d8 = abs(geom0_len - d8_0) <= 0.01
        ok = d8_unchanged and geom0_matches_d8
        detail = (f"simplify=0: D8_LENGTH_M={d8_0:.3f} geom.length()={geom0_len:.3f} "
                  f"(within 0.01m: {geom0_matches_d8}); simplify=2m: D8_LENGTH_M={d8_2:.3f} "
                  f"(unchanged: {d8_unchanged})")
    record("4. Length consistency test", ok, detail)


# ============================================================================
# Test 5 — "Real-data" check
# ============================================================================
def test_5_real_data_check(valley_d8_length):
    """
    NOTE: no real past-project Auckland subcatchment dataset was found on
    this machine (checked AucklandCatchmentHydrology/, Catchment-Assessment/
    Codes and Codes_SS, and SubCatchmentHydrology/Codes - none contained a
    DEM/subcatchment set). Substituting a synthetic multi-basin catchment
    (reusing Test 2's terrain) and comparing the OLD (V01, pre-fix) D8 table
    against the NEW (V02) table on the same inputs, which is the actual
    comparison the user's V01->V02 change makes meaningful. If a real
    dataset is available, re-run this specific test against it instead.
    """
    tmp = tempfile.mkdtemp(prefix='lfp_t5_')
    rng = np.random.RandomState(42)
    n = 200
    gt = (0, 1, 0, n, 0, -1)
    yy, xx = np.mgrid[0:n, 0:n]
    elev = (n - yy) * 0.05 + xx * 0.01
    for _ in range(12):
        cx, cy = rng.uniform(0, n), rng.uniform(0, n)
        amp = rng.uniform(1.0, 4.0)
        sigma = rng.uniform(15, 35)
        elev += amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2)))
    elev = elev.astype(np.float32)
    dem_path = os.path.join(tmp, "dem.tif")
    write_raster(dem_path, elev, gdal.GDT_Float32, gt)
    drain_path, accum_path, basin_path = run_watershed(dem_path, n, n, gt, threshold=1500, tmp=tmp)
    drain_ds = gdal.Open(drain_path)
    accum_ds = gdal.Open(accum_path)
    basin_ds = gdal.Open(basin_path)
    basin_arr = basin_ds.GetRasterBand(1).ReadAsArray()
    drain_nodata = drain_ds.GetRasterBand(1).GetNoDataValue()
    accum_nodata = accum_ds.GetRasterBand(1).GetNoDataValue()

    # OLD (V01, buggy) D8 table, rotated one position from the real encoding.
    OLD_D8 = {1: (0, 1), 2: (-1, 1), 3: (-1, 0), 4: (-1, -1),
              5: (0, -1), 6: (1, -1), 7: (1, 0), 8: (1, 1)}

    algo = Algo()
    basin_ids, counts = np.unique(basin_arr, return_counts=True)
    rows = []
    for bid, cell_count in zip(basin_ids.tolist(), counts.tolist()):
        if bid <= 0 or cell_count <= 1000:
            continue
        rows_idx, cols_idx = np.where(basin_arr == bid)
        if rows_idx.min() == 0 or rows_idx.max() == n - 1 or \
           cols_idx.min() == 0 or cols_idx.max() == n - 1:
            continue  # edge-touching basin - see note in test_2_accumulation
        mask = (basin_arr == bid).astype(np.uint8)
        mem_drv = gdal.GetDriverByName('MEM')
        mask_ds = mem_drv.Create('', n, n, 1, gdal.GDT_Byte)
        mask_ds.SetGeoTransform(gt)
        mask_ds.GetRasterBand(1).WriteArray(mask)
        ogr_ds = ogr.GetDriverByName('Memory').CreateDataSource('')
        ogr_lyr = ogr_ds.CreateLayer('poly')
        ogr_lyr.CreateField(ogr.FieldDefn('val', ogr.OFTInteger))
        gdal.Polygonize(mask_ds.GetRasterBand(1), mask_ds.GetRasterBand(1), ogr_lyr, 0)
        best_feat, best_area = None, 0.0
        for f in ogr_lyr:
            if f.GetField('val') != 1:
                continue
            a = f.GetGeometryRef().GetArea()
            if a > best_area:
                best_area, best_feat = a, f
        if best_feat is None:
            continue
        poly_geom = QgsGeometry.fromWkt(best_feat.GetGeometryRef().ExportToWkt())

        new_line, new_len, _ = algo._trace(
            poly_geom, drain_ds, accum_ds, gt, n, n, 1.0, 1.0, 0.0,
            drain_nodata=drain_nodata, accum_nodata=accum_nodata,
        )

        old_mod_D8 = mod._D8
        mod._D8 = OLD_D8
        try:
            old_line, old_len, _ = algo._trace(
                poly_geom, drain_ds, accum_ds, gt, n, n, 1.0, 1.0, 0.0,
                drain_nodata=drain_nodata, accum_nodata=accum_nodata,
            )
        finally:
            mod._D8 = old_mod_D8

        if new_line is None and old_line is None:
            continue
        old_len_v = old_len if old_line is not None else 0.0
        new_len_v = new_len if new_line is not None else 0.0
        pct_change = ((new_len_v - old_len_v) / old_len_v * 100.0) if old_len_v else float('nan')
        rows.append((int(bid), round(old_len_v, 2), round(new_len_v, 2), round(pct_change, 1)))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, "before_after_v01_v02.csv")
    with open(out_csv, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['SUBCAT_FID', 'old_V01_length_m', 'new_V02_length_m', 'pct_change'])
        writer.writerows(rows)

    record("5. 'Real-data' check (synthetic substitute - see note above)",
           len(rows) > 0, f"{len(rows)} basin(s); CSV written to {out_csv}")
    return out_csv, rows


# ============================================================================
if __name__ == '__main__':
    t_start = time.time()
    test_1_direction()
    test_2_accumulation()
    valley_len = test_3_known_geometry()
    test_4_length_consistency()
    csv_path, csv_rows = test_5_real_data_check(valley_len)

    print(f"\nTotal elapsed: {time.time() - t_start:.1f}s")
    print("\n=== SUMMARY ===")
    for name, passed, detail in RESULTS:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    all_passed = all(r[1] for r in RESULTS)
    print("\nALL TESTS PASSED:" if all_passed else "\nSOME TESTS FAILED:", all_passed)
    print("\nBefore/after CSV:", csv_path)
    for row in csv_rows:
        print("  ", row)
