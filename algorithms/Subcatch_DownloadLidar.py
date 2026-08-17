# -*- coding: utf-8 -*-

"""
Download LINZ DEM – Download the highest-resolution LINZ DEM available for a
user-drawn map canvas extent.

Library (Group): Flood Pre-Modelling
Tool (Algorithm): Download LINZ DEM V01
File: TM_Download_LiDAR_V01.py

Author: Dipendra Magaju
Tested On: QGIS 3.44.2

Short description: Downloads all LINZ 1m LiDAR DEM surveys intersecting a
user-drawn extent as separate GeoTIFFs with metadata sidecars; falls back to
the LINZ 8m DEM (2012) via WCS if no LiDAR coverage exists.

Inputs / Outputs:
  Inputs  — Extent (drawn on canvas), output resolution (m),
             output CRS (default EPSG:2193 NZTM2000),
             LINZ API key (optional — required only for 8m fallback),
             output folder
  Outputs — Per survey: GeoTIFF + _metadata.json + _metadata.txt;
             download_summary.txt with metadata and raster statistics

Long description / Help:
  The tool queries the LINZ 1m LiDAR STAC catalog hosted on AWS Open Data.
  All surveys that intersect the drawn extent are identified; tile URLs are
  pre-filtered in parallel (16 workers) to only those overlapping the extent,
  minimising network traffic. Each survey is downloaded as a separate GeoTIFF
  to preserve datum integrity — surveys from different years may use different
  vertical datums and should not be merged without verification.

  If no 1m LiDAR coverage is found, the tool falls back to the LINZ 8m DEM
  (2012) downloaded via WCS, which requires a free LINZ API key.

  Source selection (automatic):
    1. NZ 1m LiDAR DEM (preferred) — AWS open-data STAC catalog. No API key
       required. One file per survey, newest first.
    2. NZ 8m DEM (2012) (fallback) — LINZ WCS. Full national coverage.
       Requires a free LINZ API key (data.linz.govt.nz -> API Keys).

  Output per survey:
    - DEM_LiDAR_1m_<year>.tif — clipped raster at the requested resolution
    - DEM_LiDAR_1m_<year>_metadata.json — full STAC collection JSON
    - DEM_LiDAR_1m_<year>_metadata.txt — LINZ-style metadata report
    - download_summary.txt — aggregate summary for all files in the run

  Prerequisites:
    - GDAL Python bindings (osgeo) — bundled with QGIS.
    - Internet access to AWS Open Data (STAC) and optionally LINZ WCS.
    - LINZ API key required only if 8m fallback is needed.
"""

__author__ = "Dipendra Magaju"
__copyright__ = "2025"
__license__ = "GPL"
__version__ = "V01"
__email__ = ""
__status__ = "Stable"
_LINKED_PROMPT = 'TM_Download_LiDAR_V01_GM.txt'

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRasterLayer,
    QgsProcessingContext,
)
import os
import json
import math
import re
import datetime
import urllib.request
import urllib.parse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

# AWS open-data STAC root for NZ elevation
STAC_BASE = 'https://nz-elevation.s3.ap-southeast-2.amazonaws.com'
STAC_ROOT = f'{STAC_BASE}/catalog.json'

# Approximate NZTM2000 bounding boxes for NZ regions (used to filter STAC traversal)
_REGION_BOUNDS = {
    'northland':       (1620000, 1720000, 6040000, 6200000),
    'auckland':        (1700000, 1820000, 5870000, 5990000),
    'waikato':         (1700000, 1850000, 5740000, 5880000),
    'bay-of-plenty':   (1780000, 1940000, 5760000, 5870000),
    'gisborne':        (1870000, 2000000, 5620000, 5760000),
    'hawkes-bay':      (1820000, 1990000, 5490000, 5650000),
    'taranaki':        (1640000, 1740000, 5620000, 5740000),
    'manawatu-whanganui': (1700000, 1860000, 5440000, 5640000),
    'wellington':      (1730000, 1810000, 5380000, 5470000),
    'nelson-marlborough': (1530000, 1700000, 5340000, 5480000),
    'west-coast':      (1330000, 1570000, 5100000, 5380000),
    'canterbury':      (1430000, 1650000, 5060000, 5320000),
    'otago':           (1230000, 1490000, 4900000, 5150000),
    'southland':       (1060000, 1320000, 4760000, 5010000),
}


class TmDownloadLiDAR(QgsProcessingAlgorithm):
    """
    Downloads all LINZ 1m LiDAR surveys that intersect the drawn extent,
    saving each as a separate GeoTIFF with metadata sidecars. Falls back to
    the LINZ 8m DEM if no LiDAR surveys exist for the area.
    """

    EXTENT        = 'EXTENT'
    RESOLUTION    = 'RESOLUTION'
    LATEST_ONLY   = 'LATEST_ONLY'
    API_KEY       = 'API_KEY'
    OUTPUT_CRS    = 'OUTPUT_CRS'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER'

    LAYER_ID_8M  = '51768'   # LINZ WCS layer ID for the NZ 8m DEM (2012)
    WARN_EXTENT  = 20000     # metres — warn if extent exceeds this in either direction
    NZTM         = 'EPSG:2193'
    WGS84        = 'EPSG:4326'

    # -------------------------------------------------------------------------

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return TmDownloadLiDAR()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'tm_download_lidar_v01'

    def displayName(self):
        return self.tr('Download LINZ DEM V01')

    def group(self):
        return self.tr('Flood Pre-Modelling')

    def groupId(self):
        return 'flood_pre_modelling'

    def shortHelpString(self):
        return self.tr(
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Short description: Downloads all LINZ 1m LiDAR DEM surveys intersecting "
            "a user-drawn extent as separate GeoTIFFs with metadata sidecars. Falls "
            "back to the LINZ 8m DEM (2012) via WCS if no LiDAR coverage exists.\n\n"
            "HOW TO USE:\n"
            " 1. Click 'Draw on Canvas' and drag a rectangle over your area.\n"
            " 2. Set output resolution (metres). Default 1 m matches LiDAR source.\n"
            " 3. Optionally enter your LINZ API key (enables 8m fallback).\n"
            " 4. Choose an output folder and click Run.\n\n"
            "Inputs:\n"
            " * Extent — bounding box drawn on the map canvas\n"
            " * Output resolution (m) — default 1.0 m\n"
            " * Output CRS — default EPSG:2193 NZTM2000\n"
            " * LINZ API key (optional) — required only for 8m DEM fallback;\n"
            "   get a free key at data.linz.govt.nz\n"
            " * Output folder — a timestamped subfolder is created inside\n\n"
            "Outputs:\n"
            " * DEM_LiDAR_1m_<year>.tif — clipped LiDAR raster per survey\n"
            " * DEM_LiDAR_1m_<year>_metadata.json — full STAC collection JSON\n"
            " * DEM_LiDAR_1m_<year>_metadata.txt — LINZ-style metadata report\n"
            " * download_summary.txt — aggregate download log for all files\n\n"
            "Source selection is automatic:\n"
            " 1. NZ 1m LiDAR DEM (preferred) — AWS open-data STAC, no API key needed.\n"
            "    One file per survey, newest first.\n"
            " 2. NZ 8m DEM 2012 (fallback) — LINZ WCS, requires API key.\n\n"
            "Important: surveys from different years may use different vertical datums.\n"
            "Do not merge files without verifying datum consistency.\n\n"
            "NETWORK & DOWNLOAD METHOD\n"
            "Tiles are downloaded using two methods, tried in order:\n"
            " 1. GDAL /vsicurl/ streaming — fast, no local storage. Preferred where\n"
            "    GDAL's network layer can reach AWS directly.\n"
            " 2. Direct HTTP download (automatic fallback) — if /vsicurl/ fails, the\n"
            "    tool retries using Python's urllib, which honours OS/system proxy\n"
            "    settings. This works in most corporate office environments where a\n"
            "    firewall or proxy blocks GDAL's own curl layer.\n\n"
            "If you see 'Failed to build virtual mosaic from tile URLs', the fallback\n"
            "will activate automatically. If both methods fail, check that the tile\n"
            "URL shown in the log opens in a browser — if not, ask IT to whitelist\n"
            "nz-elevation.s3.ap-southeast-2.amazonaws.com."
        )

    def help(self):
        return self.tr(
            "<html><body>\n"
            "<h2>Download LINZ DEM V01</h2>\n"
            "<p><b>Author:</b> Dipendra Magaju | <b>Tested On:</b> QGIS 3.44.2</p>\n"
            "<h3>Purpose</h3>\n"
            "<p>Downloads the highest-resolution LINZ DEM available for a user-drawn "
            "extent. The tool queries the LINZ 1m LiDAR STAC catalog on AWS Open Data, "
            "identifies all surveys intersecting the extent, and downloads each as a "
            "separate GeoTIFF to preserve vertical datum integrity. If no 1m LiDAR is "
            "available for the area, the tool falls back to the LINZ 8m DEM (2012) via "
            "WCS. Full metadata sidecars are written alongside every downloaded file.</p>\n"
            "<h3>Methodology</h3>\n"
            "<ol>\n"
            "<li>The drawn extent is reprojected to WGS84 for STAC queries and NZTM "
            "for tile clipping and WCS requests.</li>\n"
            "<li>The AWS STAC root catalog is traversed. Known NZTM region bounding "
            "boxes are used as hints to narrow the search before fetching collection "
            "JSON files.</li>\n"
            "<li>For each candidate collection, individual STAC item JSONs are fetched "
            "in parallel (16 workers) and filtered to only tiles that overlap the "
            "drawn extent.</li>\n"
            "<li>Per survey, an in-memory virtual raster (VRT) is built from the "
            "selected tile /vsicurl/ URLs and warped directly to the output extent and "
            "CRS using GDAL Warp — no intermediate files are created on disk.</li>\n"
            "<li>If the warped output contains no valid pixels (all nodata), the file "
            "is discarded automatically and a warning is logged.</li>\n"
            "<li>For each valid output, a metadata JSON sidecar and a LINZ-style "
            "metadata TXT report are written alongside the GeoTIFF.</li>\n"
            "<li>If no 1m LiDAR surveys are found, a WCS GetCoverage request is sent "
            "to LINZ for the 8m DEM using the provided API key.</li>\n"
            "</ol>\n"
            "<h3>Input Requirements</h3>\n"
            "<ul>\n"
            "<li><b>Extent:</b> Bounding box drawn on the map canvas. Automatically "
            "reprojected to WGS84 and NZTM internally — any CRS is accepted.</li>\n"
            "<li><b>Output resolution (m):</b> Pixel size of the output GeoTIFF. "
            "Default 1 m matches the LiDAR source resolution.</li>\n"
            "<li><b>Output CRS:</b> Target CRS for the output raster. Default "
            "EPSG:2193 (NZTM2000). Any EPSG code is accepted.</li>\n"
            "<li><b>LINZ API key (optional):</b> Required only when no 1m LiDAR "
            "exists for the area and the 8m fallback is needed. Get a free key at "
            "<a href='https://data.linz.govt.nz'>data.linz.govt.nz</a>.</li>\n"
            "<li><b>Output folder:</b> A timestamped subfolder (LINZ_YYYYMMDD_HHMMSS) "
            "is created inside the chosen folder for each run.</li>\n"
            "</ul>\n"
            "<h3>Output</h3>\n"
            "<ul>\n"
            "<li><b>DEM_LiDAR_1m_&lt;year&gt;.tif</b> — Float32 GeoTIFF at the "
            "requested resolution and CRS, clipped to the drawn extent. One file per "
            "intersecting survey, sorted newest first.</li>\n"
            "<li><b>DEM_LiDAR_1m_&lt;year&gt;_metadata.json</b> — Full STAC "
            "collection JSON including all LINZ-specific metadata fields.</li>\n"
            "<li><b>DEM_LiDAR_1m_&lt;year&gt;_metadata.txt</b> — LINZ-style metadata "
            "report including survey dates, vertical datum, spatial stats, and license "
            "information.</li>\n"
            "<li><b>download_summary.txt</b> — Aggregate download log with raster "
            "statistics (min, max, mean, std dev) and full metadata for every file "
            "produced in the run.</li>\n"
            "</ul>\n"
            "<h3>Important Notes and Restrictions</h3>\n"
            "<ul>\n"
            "<li>Surveys from different years may use different vertical datums "
            "(e.g. NZVD2016 vs NZVD2009). Do not merge rasters without verifying "
            "datum consistency first.</li>\n"
            "<li>GDAL Python bindings (osgeo) are required and are bundled with QGIS. "
            "The tool will raise an error if they are unavailable.</li>\n"
            "<li>Internet access to AWS Open Data is required for STAC catalog "
            "traversal and tile streaming. LINZ WCS access is required for the 8m "
            "fallback only.</li>\n"
            "<li>Large extents (&gt;20 km in either direction) will produce a warning. "
            "Consider drawing a smaller extent if only a specific reach is needed.</li>\n"
            "<li>If a survey's tiles do not cover the exact drawn extent after warping, "
            "the output file is discarded and a warning is logged — this is normal "
            "behaviour at survey edges.</li>\n"
            "</ul>\n"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT,
                self.tr('Extent (draw on canvas)'),
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RESOLUTION,
                self.tr('Output Resolution (metres)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.5,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LATEST_ONLY,
                self.tr('Download latest survey only (uncheck to download all intersecting surveys)'),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.API_KEY,
                self.tr('LINZ API Key (optional — enables 8m DEM fallback if no LiDAR found)'),
                optional=True,
                defaultValue='',
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr('Output CRS'),
                defaultValue='EPSG:2193',
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                self.tr("Output folder (a 'LINZ_YYYYMMDD_HHMMSS' subfolder is created inside)"),
                defaultValue=os.path.join(os.path.expanduser('~'), 'Downloads'),
            )
        )

    # =========================================================================

    def processAlgorithm(self, parameters, context, feedback):
        extent        = self.parameterAsExtent(parameters, self.EXTENT, context)
        extent_crs    = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        resolution    = self.parameterAsDouble(parameters, self.RESOLUTION, context)
        latest_only   = self.parameterAsBool(parameters, self.LATEST_ONLY, context)
        api_key       = self.parameterAsString(parameters, self.API_KEY, context).strip()
        output_crs    = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        output_folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)

        if not output_folder:
            raise QgsProcessingException('Please specify an output folder.')
        timestamp    = datetime.datetime.now().strftime('LINZ_%Y%m%d_%H%M%S')
        output_folder = os.path.join(output_folder, timestamp)
        os.makedirs(output_folder, exist_ok=True)
        feedback.pushInfo(f'Output folder: {output_folder}')

        output_crs_authid = output_crs.authid()
        feedback.pushInfo(f'Output CRS : {output_crs_authid}')

        # Reproject extent to NZTM2000 (source CRS of tiles) and WGS84 (STAC bbox).
        nztm    = QgsCoordinateReferenceSystem(self.NZTM)
        wgs84   = QgsCoordinateReferenceSystem(self.WGS84)
        project = QgsProject.instance()

        to_nztm  = QgsCoordinateTransform(extent_crs, nztm,  project)
        to_wgs84 = QgsCoordinateTransform(extent_crs, wgs84, project)

        ext_nztm  = to_nztm.transformBoundingBox(extent)
        ext_wgs84 = to_wgs84.transformBoundingBox(extent)

        xmin, ymin = ext_nztm.xMinimum(), ext_nztm.yMinimum()
        xmax, ymax = ext_nztm.xMaximum(), ext_nztm.yMaximum()
        w = ext_wgs84.xMinimum(); s = ext_wgs84.yMinimum()
        e = ext_wgs84.xMaximum(); n = ext_wgs84.yMaximum()

        feedback.pushInfo(
            f'Extent (NZTM) : {xmin:.0f}, {ymin:.0f}, {xmax:.0f}, {ymax:.0f}'
        )
        feedback.pushInfo(f'Extent size   : {xmax-xmin:.0f}m × {ymax-ymin:.0f}m')
        feedback.pushInfo(f'Extent (WGS84): {w:.5f}, {s:.5f}, {e:.5f}, {n:.5f}')

        downloaded_info = []

        # ── Step 1: Try 1m LiDAR (AWS STAC) — one file per survey ────────────
        feedback.pushInfo('Checking LINZ 1m LiDAR catalog (AWS)...')
        surveys = self._find_stac_surveys(w, s, e, n, xmin, ymin, xmax, ymax, feedback)

        if surveys:
            if latest_only and len(surveys) > 1:
                feedback.pushInfo(
                    f'{len(surveys)} survey(s) intersect the extent. '
                    f'Keeping only the latest: "{surveys[0]["title"]}" ({surveys[0]["year"]}). '
                    f'Uncheck "Download latest survey only" to download all.'
                )
                surveys = surveys[:1]
            else:
                feedback.pushInfo(
                    f'{len(surveys)} survey(s) intersect the extent — '
                    f'downloading each as a separate file.'
                )
            used_filenames = set()
            for idx, survey in enumerate(surveys):
                if feedback.isCanceled():
                    break

                year = survey['year']
                fname = f'DEM_LiDAR_1m_{year}.tif'
                if fname in used_filenames:
                    fname = f'DEM_LiDAR_1m_{year}_{idx + 1}.tif'
                used_filenames.add(fname)
                stem = os.path.splitext(fname)[0]
                output_path = os.path.join(output_folder, fname)

                feedback.pushInfo(
                    f'\n[{idx+1}/{len(surveys)}] {survey["title"]} '
                    f'({len(survey["tile_urls"])} tiles in extent)...'
                )
                self._download_1m_aws(
                    survey['tile_urls'], ext_nztm, resolution,
                    output_crs_authid, output_path, feedback
                )

                # Skip surveys whose tiles don't actually cover the drawn extent.
                if not self._has_valid_data(output_path):
                    feedback.pushInfo(
                        '  No LiDAR data within the drawn extent for this survey — skipped.'
                    )
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    continue

                # ── Save metadata JSON sidecar ─────────────────────────────
                meta_fname = f'{stem}_metadata.json'
                meta_path  = os.path.join(output_folder, meta_fname)
                try:
                    with open(meta_path, 'w', encoding='utf-8') as mf:
                        json.dump(survey['raw_collection'], mf, indent=2, ensure_ascii=False)
                    feedback.pushInfo(f'  Metadata : {meta_fname}')
                except Exception as exc:
                    feedback.pushWarning(f'  Could not save metadata JSON: {exc}')
                    meta_fname = None

                ca_fname = None  # capture area not downloaded

                # ── Write per-survey LINZ-style metadata text ──────────────
                meta_txt_fname = f'{stem}_metadata.txt'
                meta_txt_path  = os.path.join(output_folder, meta_txt_fname)
                self._write_metadata_txt(
                    meta_txt_path, survey, fname, output_path,
                    output_crs_authid, meta_fname, ca_fname, feedback
                )

                layer_label = stem
                raster = QgsRasterLayer(output_path, layer_label)
                if raster.isValid():
                    context.addLayerToLoadOnCompletion(
                        output_path,
                        QgsProcessingContext.LayerDetails(layer_label, context.project()),
                    )
                    downloaded_info.append({
                        'filename':              fname,
                        'path':                  output_path,
                        'source':                'LINZ 1m LiDAR (AWS STAC)',
                        'meta_filename':         meta_fname,
                        'meta_txt_filename':     meta_txt_fname,
                        'capture_area_filename': ca_fname,
                        **survey,
                    })
                    feedback.pushInfo(f'  Saved: {output_path}')
                else:
                    feedback.pushWarning(
                        f'  Output raster invalid (no LiDAR data in this survey '
                        f'for the exact extent): {output_path}'
                    )

            if not downloaded_info:
                raise QgsProcessingException(
                    'Surveys were found in the catalog but no valid output rasters '
                    'were produced. The tiles may not overlap the drawn extent precisely.'
                )

        else:
            # ── Step 2: Fall back to 8m DEM ───────────────────────────────────
            feedback.pushInfo('No 1m LiDAR coverage found for this extent.')
            if not api_key:
                raise QgsProcessingException(
                    'No 1m LiDAR DEM is available for this area in the LINZ catalog.\n\n'
                    'To use the NZ 8m DEM (2012) as a fallback, provide a LINZ API key '
                    'in the "LINZ API Key" field.\n'
                    'Get a free key at: https://data.linz.govt.nz'
                )
            feedback.pushInfo('Falling back to LINZ 8m DEM (2012)...')
            fname = 'DEM_8m_2012.tif'
            output_path = os.path.join(output_folder, fname)
            self._download_8m_wcs(
                api_key, self.LAYER_ID_8M, ext_nztm, resolution,
                output_crs_authid, output_path, feedback
            )

            layer_label = 'DEM_8m_2012'
            raster = QgsRasterLayer(output_path, layer_label)
            if not raster.isValid():
                raise QgsProcessingException(
                    'Output raster is not valid. The dataset may not cover this extent.'
                )
            context.addLayerToLoadOnCompletion(
                output_path,
                QgsProcessingContext.LayerDetails(layer_label, context.project()),
            )
            downloaded_info.append({
                'filename':              fname,
                'path':                  output_path,
                'source':                'LINZ 8m DEM 2012 (WCS)',
                'meta_filename':         None,
                'meta_txt_filename':     None,
                'capture_area_filename': None,
                'title':                 'NZ 8m Digital Elevation Model (2012)',
                'year':                  2012,
                'url':                   f'https://data.linz.govt.nz/layer/{self.LAYER_ID_8M}',
                'bbox':                  None,
                'temporal':              ['2012-01-01T00:00:00Z', None],
                'description':           'National DEM at 8m resolution covering all of New Zealand.',
                'tile_urls':             [],
                'id':                    '',
                'license':               'CC-BY-4.0',
                'gsd':                   8,
                'created':               '',
                'updated':               '',
                'providers':             [{'name': 'Toitū Te Whenua Land Information New Zealand',
                                           'roles': ['producer', 'host']}],
                'linz_slug':             'nz-8m-digital-elevation-model-2012',
                'linz_region':           'national',
                'linz_lifecycle':        'completed',
                'linz_category':         'dem',
                'linz_security':         'unclassified',
                'capture_area_href':     None,
                'raw_collection':        {},
            })
            feedback.pushInfo(f'Saved: {output_path}')

        # ── Write summary ─────────────────────────────────────────────────────
        self._write_summary(
            output_folder, downloaded_info,
            xmin, ymin, xmax, ymax, w, s, e, n,
            output_crs_authid, resolution, feedback
        )

        return {self.OUTPUT_FOLDER: output_folder}

    # =========================================================================
    # 1m LiDAR DEM via AWS STAC + GDAL
    # =========================================================================

    def _download_1m_aws(self, tile_urls, ext_nztm, resolution, output_crs_authid, output_path, feedback):
        try:
            from osgeo import gdal
            gdal.UseExceptions()
        except ImportError:
            raise QgsProcessingException(
                'GDAL Python bindings (osgeo) are required for the 1m DEM download. '
                'They are included with QGIS — ensure you are running this tool from within QGIS.'
            )

        # ── GDAL config for fast COG streaming over HTTPS ─────────────────────
        # These settings enable HTTP/2 multiplexing, merge consecutive byte-range
        # requests, add a large VSI read-ahead cache, and allow the Warp step to
        # read tiles from multiple threads simultaneously.
        _gdal_opts = {
            'CPL_VSIL_CURL_CACHE_SIZE':          '200000000',   # 200 MB /vsicurl/ cache
            'VSI_CACHE':                          'TRUE',
            'VSI_CACHE_SIZE':                     '200000000',   # 200 MB VSI block cache
            'GDAL_HTTP_MERGE_CONSECUTIVE_REQUESTS': 'YES',       # coalesce range requests
            'GDAL_HTTP_MULTIPLEX':                'YES',         # HTTP/2 if server supports
            'GDAL_HTTP_MAX_RETRY':                '3',
            'GDAL_HTTP_RETRY_DELAY':              '0.5',
            'GDAL_NUM_THREADS':                   'ALL_CPUS',   # parallel tile I/O
        }
        for k, v in _gdal_opts.items():
            gdal.SetConfigOption(k, v)

        xmin, ymin = ext_nztm.xMinimum(), ext_nztm.yMinimum()
        xmax, ymax = ext_nztm.xMaximum(), ext_nztm.yMaximum()

        feedback.pushInfo(f'  Building virtual mosaic from {len(tile_urls)} tiles...')
        if tile_urls:
            feedback.pushInfo(f'  First tile URL: {tile_urls[0]}')

        # Attempt 1: /vsicurl/ streaming — fast, no local storage, preferred path.
        vrt = gdal.BuildVRT('', [f'/vsicurl/{u}' for u in tile_urls])
        tmp_dir = None

        if not vrt:
            # Attempt 2: direct urllib download to a temp folder.
            # urllib uses the system/OS proxy settings, so this works in corporate
            # environments where GDAL's own curl layer is blocked by a firewall or proxy.
            feedback.pushWarning(
                '  GDAL /vsicurl/ streaming unavailable — '
                'falling back to direct HTTP download.\n'
                '  Tiles will be saved to a temporary folder before processing. '
                'This is slower but works through most corporate proxies/firewalls.'
            )
            import tempfile, shutil as _shutil
            tmp_dir = tempfile.mkdtemp(prefix='linz_tiles_')
            try:
                vrt = self._fetch_tiles_direct(tile_urls, tmp_dir, gdal, feedback)
            except Exception as exc:
                _shutil.rmtree(tmp_dir, ignore_errors=True)
                raise QgsProcessingException(
                    f'Download failed:\n{exc}\n\n'
                    'Both GDAL /vsicurl/ streaming and direct HTTP download failed.\n'
                    'Check internet access to nz-elevation.s3.ap-southeast-2.amazonaws.com.'
                )
            if not vrt:
                _shutil.rmtree(tmp_dir, ignore_errors=True)
                raise QgsProcessingException(
                    'No tiles could be downloaded — check internet access to AWS\n'
                    '(nz-elevation.s3.ap-southeast-2.amazonaws.com).'
                )

        # Output pixel dimensions at the requested resolution — no cap, no resampling.
        width_px  = max(1, int(math.ceil((xmax - xmin) / resolution)))
        height_px = max(1, int(math.ceil((ymax - ymin) / resolution)))
        feedback.pushInfo(
            f'  Output size: {width_px} × {height_px} pixels  '
            f'({width_px * height_px / 1_000_000:.1f} MP, '
            f'{(xmax-xmin):.0f} m × {(ymax-ymin):.0f} m at {resolution} m/px)'
        )

        ext_w = xmax - xmin
        ext_h = ymax - ymin
        if ext_w > self.WARN_EXTENT or ext_h > self.WARN_EXTENT:
            feedback.pushWarning(
                f'  Extent is large ({ext_w:.0f} m × {ext_h:.0f} m). '
                f'The output file may be several hundred MB. '
                f'Draw a smaller extent if only a specific reach is needed.'
            )

        if output_crs_authid != self.NZTM:
            feedback.pushInfo(
                f'  Reprojecting NZTM → {output_crs_authid}: pixel values will be '
                f'interpolated (unavoidable when changing CRS).'
            )

        # NearestNeighbour preserves original source pixel values exactly.
        # multithread + NUM_THREADS let GDAL read tiles from multiple COGs in parallel.
        try:
            result = gdal.Warp(
                output_path,
                vrt,
                format='GTiff',
                outputBounds=(xmin, ymin, xmax, ymax),
                outputBoundsSRS=self.NZTM,
                dstSRS=output_crs_authid,
                width=width_px,
                height=height_px,
                resampleAlg=gdal.GRA_NearestNeighbour,
                creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_NEEDED'],
                multithread=True,
                warpOptions=['NUM_THREADS=ALL_CPUS'],
            )
            vrt = None
            result = None
        finally:
            if tmp_dir:
                import shutil as _shutil2
                _shutil2.rmtree(tmp_dir, ignore_errors=True)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise QgsProcessingException(
                'Clip produced no output. The tiles may not overlap the extent, '
                'or network access to AWS may be blocked.'
            )

    def _find_stac_surveys(self, w, s, e, n, xmin, ymin, xmax, ymax, feedback):
        """Finds all 1m LiDAR survey collections that intersect the extent.

        Returns a list of dicts (newest-first), one per collection.
        Tile URLs are pre-filtered to only those intersecting the drawn
        extent using parallel item JSON fetching, so BuildVRT only opens
        the tiles that are actually needed.
        """
        region_hints = set()
        for region, (rex_min, rex_max, rn_min, rn_max) in _REGION_BOUNDS.items():
            if xmax >= rex_min and xmin <= rex_max and ymax >= rn_min and ymin <= rn_max:
                region_hints.add(region)

        if region_hints:
            feedback.pushInfo(f'Region hint(s): {", ".join(sorted(region_hints))}')
        else:
            feedback.pushInfo('No region hint — scanning all catalog entries (may be slow).')

        root_url = STAC_ROOT
        root = self._fetch_json(root_url)

        collections = []
        for lnk in root.get('links', []):
            if lnk.get('rel') != 'child':
                continue
            href = lnk.get('href', '')
            if 'dem_1m' not in href.lower():
                continue
            collections.append({**lnk, 'href': urljoin(root_url, href)})

        if region_hints:
            filtered = [l for l in collections
                        if any(h in l['href'].lower() for h in region_hints)]
            if filtered:
                collections = filtered

        def _max_year(lnk):
            years = re.findall(r'_(\d{4})', lnk['href'])
            return max((int(y) for y in years), default=0)
        collections = sorted(collections, key=_max_year, reverse=True)

        feedback.pushInfo(f'Found {len(collections)} DEM survey collection(s) to check.')

        # Pre-fetch all collection JSONs in parallel so we don't pay sequential
        # HTTP latency for each one (each fetch is ~200 ms; with 16 workers a
        # set of 32 collections takes ~400 ms instead of ~6 s).
        def _fetch_coll_safe(href):
            try:
                return self._fetch_json(href)
            except Exception:
                return None

        feedback.pushInfo('Fetching collection metadata in parallel...')
        _n_workers = min(16, max(1, len(collections)))
        with ThreadPoolExecutor(max_workers=_n_workers) as _pool:
            _coll_jsons = list(_pool.map(_fetch_coll_safe, [l['href'] for l in collections]))
        feedback.pushInfo(f'Collection metadata fetched. Checking extent overlap...')

        surveys = []
        n_colls = len(collections)
        for i, (lnk, coll) in enumerate(zip(collections, _coll_jsons)):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(i / n_colls * 50))
            coll_url = lnk['href']

            if coll is None:
                feedback.pushWarning(f'  Could not fetch collection: {coll_url}')
                continue

            coll_bbox = (coll.get('extent', {})
                         .get('spatial', {})
                         .get('bbox', [[None]])[0])
            coll_title = coll.get('title', coll_url)[:80]

            if coll_bbox and not self._intersects_wgs84(coll_bbox, w, s, e, n):
                feedback.pushInfo(f'  "{coll_title}" — outside extent, skipped.')
                continue

            temporal = (coll.get('extent', {})
                        .get('temporal', {})
                        .get('interval', [[None, None]])[0])

            description = coll.get('description', '')

            url_years = re.findall(r'_(\d{4})', coll_url)
            coll_year = max((int(y) for y in url_years), default=0)

            # Filter tiles to only those intersecting the drawn extent (parallel).
            item_links = [l for l in coll.get('links', []) if l.get('rel') == 'item']
            if not item_links:
                feedback.pushInfo(f'  "{coll_title}" — no tiles, skipped.')
                continue

            feedback.pushInfo(
                f'  "{coll_title}" — {len(item_links)} tiles total, '
                f'filtering to extent...'
            )
            tile_urls = self._filter_tiles_by_extent(coll_url, item_links, w, s, e, n)
            if not tile_urls:
                feedback.pushInfo(
                    f'    0/{len(item_links)} tiles intersect the drawn extent — skipped.'
                )
                continue

            capture_area_href = None
            assets = coll.get('assets', {})
            if 'capture_area' in assets:
                raw_href = assets['capture_area'].get('href', '')
                if raw_href:
                    capture_area_href = urljoin(coll_url, raw_href)

            feedback.pushInfo(
                f'  "{coll_title}" — {len(tile_urls)}/{len(item_links)} tiles '
                f'intersect extent. ✓'
            )
            surveys.append({
                'title':       coll_title,
                'year':        coll_year,
                'url':         coll_url,
                'id':          coll.get('id', ''),
                'bbox':        coll_bbox,
                'temporal':    temporal,
                'description': description,
                'tile_urls':   tile_urls,
                'license':     coll.get('license', ''),
                'gsd':         coll.get('gsd'),
                'created':     coll.get('created', ''),
                'updated':     coll.get('updated', ''),
                'providers':   coll.get('providers', []),
                'linz_slug':   coll.get('linz:slug', ''),
                'linz_region': coll.get('linz:region', ''),
                'linz_lifecycle':  coll.get('linz:lifecycle', ''),
                'linz_category':   coll.get('linz:geospatial_category', ''),
                'linz_security':   coll.get('linz:security_classification', ''),
                'capture_area_href': capture_area_href,
                'raw_collection':    coll,
            })

        return surveys  # newest-first

    def _filter_tiles_by_extent(self, coll_url, item_links, w, s, e, n):
        """Fetches STAC item JSONs in parallel and returns only tile URLs intersecting the extent.

        Fetching in parallel (16 workers) reduces 843 sequential header probes
        (~170 s) to ~8 s, then BuildVRT only opens the handful of tiles that
        actually cover the drawn area.
        """
        def check_item(il):
            href = il.get('href', '')
            if not href:
                return None
            abs_url = urljoin(coll_url, href)
            try:
                item = self._fetch_json(abs_url)
                bbox = item.get('bbox')  # STAC items: [west, south, east, north]
                if bbox and not self._intersects_wgs84(bbox, w, s, e, n):
                    return None
                # Extract the actual GeoTIFF URL from the STAC item assets.
                # The assets dict typically has a key like 'image' or 'data'
                # pointing to the COG; always prefer this over any URL heuristic.
                assets = item.get('assets', {})
                for key in ('image', 'data', 'visual', 'cog'):
                    if key in assets:
                        asset_href = assets[key].get('href', '')
                        if asset_href:
                            return urljoin(abs_url, asset_href)
                # Fall back: any asset whose href ends in .tif/.tiff
                for asset in assets.values():
                    asset_href = asset.get('href', '')
                    if asset_href and asset_href.lower().endswith(('.tif', '.tiff')):
                        return urljoin(abs_url, asset_href)
            except Exception:
                pass  # conservative: include tile if item fetch fails
            # Last-resort heuristic: replace .json with .tif
            return abs_url.rsplit('.', 1)[0] + '.tif'

        with ThreadPoolExecutor(max_workers=min(32, len(item_links))) as executor:
            results = list(executor.map(check_item, item_links))

        return list(dict.fromkeys(r for r in results if r))

    def _fetch_tiles_direct(self, tile_urls, tmp_dir, gdal, feedback):
        """Download tile files via urllib into tmp_dir and return a GDAL VRT.

        urllib honours the OS/system proxy settings, so this works in corporate
        environments where GDAL's /vsicurl/ curl layer is firewall-blocked.
        Returns None if no tiles could be downloaded.
        """
        local_paths = []
        n = len(tile_urls)
        for i, url in enumerate(tile_urls, 1):
            if not url:
                continue
            fname = os.path.basename(url.split('?')[0]) or f'tile_{i}.tif'
            local_path = os.path.join(tmp_dir, fname)
            try:
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'QGIS-FloodPreModelling/1.0'}
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    with open(local_path, 'wb') as f:
                        f.write(resp.read())
                local_paths.append(local_path)
                feedback.pushInfo(
                    f'  Tile {i}/{n} downloaded: {fname} '
                    f'({os.path.getsize(local_path)/1024/1024:.1f} MB)'
                )
            except Exception as exc:
                feedback.pushWarning(f'  Tile {i}/{n} failed ({fname}): {exc}')

        if not local_paths:
            return None
        feedback.pushInfo(
            f'  {len(local_paths)}/{n} tile(s) downloaded. Building mosaic...'
        )
        return gdal.BuildVRT('', local_paths)

    # =========================================================================
    # 8m DEM via LINZ WCS
    # =========================================================================

    def _download_8m_wcs(self, api_key, layer_id, ext_nztm, resolution, output_crs_authid, output_path, feedback):
        xmin, ymin = ext_nztm.xMinimum(), ext_nztm.yMinimum()
        xmax, ymax = ext_nztm.xMaximum(), ext_nztm.yMaximum()

        width_px  = max(1, int(math.ceil((xmax - xmin) / resolution)))
        height_px = max(1, int(math.ceil((ymax - ymin) / resolution)))
        feedback.pushInfo(f'Output size: {width_px} × {height_px} pixels')
        ext_w = xmax - xmin
        ext_h = ymax - ymin
        if ext_w > self.WARN_EXTENT or ext_h > self.WARN_EXTENT:
            feedback.pushWarning(
                f'Extent is large ({ext_w:.0f} m × {ext_h:.0f} m). '
                f'Draw a smaller extent if only a specific area is needed.'
            )

        wcs_params = {
            'SERVICE':  'WCS',
            'VERSION':  '1.0.0',
            'REQUEST':  'GetCoverage',
            'COVERAGE': f'layer-{layer_id}',
            'CRS':      self.NZTM,
            'BBOX':     f'{xmin},{ymin},{xmax},{ymax}',
            'WIDTH':    str(width_px),
            'HEIGHT':   str(height_px),
            'FORMAT':   'image/GeoTIFF',
        }
        qs = urllib.parse.urlencode(wcs_params)

        url_candidates = [
            f'https://data.linz.govt.nz/services;key={api_key}/wcs?{qs}',
            f'https://data.linz.govt.nz/layer/{layer_id};key={api_key}/wcs?{qs}',
        ]

        last_error = None
        for url in url_candidates:
            feedback.pushInfo('Sending WCS request to LINZ...')
            try:
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'QGIS-FloodPreModelling/1.0'}
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    content_type = resp.headers.get('Content-Type', '')
                    raw = resp.read()

                if 'xml' in content_type.lower() or raw[:5] in (b'<?xml', b'<Serv', b'<Exce'):
                    snippet = raw.decode('utf-8', errors='replace')[:600]
                    last_error = f'LINZ WCS error response:\n{snippet}'
                    continue

                with open(output_path, 'wb') as f:
                    f.write(raw)
                feedback.pushInfo(f'Downloaded {len(raw)/1024:.1f} KB.')

                if output_crs_authid != self.NZTM:
                    feedback.pushInfo(f'Reprojecting to {output_crs_authid}...')
                    from osgeo import gdal
                    tmp = output_path + '._nztm.tif'
                    os.rename(output_path, tmp)
                    try:
                        gdal.Warp(
                            output_path, tmp,
                            dstSRS=output_crs_authid,
                            format='GTiff',
                            resampleAlg=gdal.GRA_Bilinear,
                            creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_NEEDED'],
                        )
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                return

            except Exception as exc:
                last_error = str(exc)
                continue

        raise QgsProcessingException(
            f'Failed to download 8m DEM from LINZ WCS.\n'
            f'Last error: {last_error}\n\n'
            f'Possible causes:\n'
            f'  • Invalid or expired API key\n'
            f'  • Layer {layer_id} may not support WCS — check data.linz.govt.nz\n'
            f'  • Network connectivity issue'
        )

    # =========================================================================
    # Per-survey LINZ-style metadata text file
    # =========================================================================

    def _write_metadata_txt(self, txt_path, survey, fname, raster_path,
                            output_crs_authid, meta_json_fname, ca_fname, feedback):
        """Writes a per-survey metadata text file in LINZ metadata report style."""
        sep  = '=' * 72
        dash = '─' * 72

        temporal = survey.get('temporal') or []
        t_start = temporal[0][:10] if len(temporal) > 0 and temporal[0] else 'Unknown'
        t_end   = temporal[1][:10] if len(temporal) > 1 and temporal[1] else 'Present'

        bbox = survey.get('bbox')
        if bbox and len(bbox) >= 4:
            bw, bs, be, bn = (
                (bbox[0], bbox[1], bbox[3], bbox[4]) if len(bbox) == 6
                else (bbox[0], bbox[1], bbox[2], bbox[3])
            )
            bbox_str = (f'  West  : {bw:.6f}°\n  East  : {be:.6f}°\n'
                        f'  South : {bs:.6f}°\n  North : {bn:.6f}°')
        else:
            bbox_str = '  Not available'

        producers  = []
        licensors  = []
        processors = []
        for p in (survey.get('providers') or []):
            name  = p.get('name', '')
            roles = p.get('roles', [])
            if 'producer'  in roles: producers.append(name)
            if 'licensor'  in roles: licensors.append(name)
            if 'host'      in roles or 'processor' in roles: processors.append(name)

        vert_datum = self._get_vertical_datum(raster_path, survey.get('description', ''))

        raster_lines = []
        try:
            from osgeo import gdal
            ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
            if ds:
                gt        = ds.GetGeoTransform()
                cols      = ds.RasterXSize
                rows      = ds.RasterYSize
                px_size   = abs(gt[1])
                file_mb   = os.path.getsize(raster_path) / (1024 * 1024)
                band      = ds.GetRasterBand(1)
                stats     = band.ComputeStatistics(False)
                raster_lines = [
                    f'  Filename         : {fname}',
                    f'  Output CRS       : {output_crs_authid}',
                    f'  Pixel Size       : {px_size:.4f} m',
                    f'  Dimensions       : {cols} × {rows} pixels',
                    f'  File Size        : {file_mb:.2f} MB',
                    f'  Elevation Min    : {stats[0]:.3f} m',
                    f'  Elevation Max    : {stats[1]:.3f} m',
                    f'  Elevation Mean   : {stats[2]:.3f} m',
                    f'  Elevation Std Dev: {stats[3]:.3f} m',
                    f'  Tiles Used       : {len(survey.get("tile_urls", []))}',
                ]
                ds = None
        except Exception:
            raster_lines = [f'  Filename  : {fname}', '  (raster stats unavailable)']

        companion_lines = []
        if meta_json_fname:
            companion_lines.append(
                f'  {meta_json_fname}\n    Full STAC collection record (machine-readable JSON)'
            )
        if ca_fname:
            companion_lines.append(
                f'  {ca_fname}\n    Exact survey capture boundary polygon (GeoJSON)'
            )
        companion_lines.append(
            '  download_summary.txt\n    Download log for all surveys in this run'
        )

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        sections = [
            sep,
            'LINZ DATASET METADATA',
            sep,
            f'Generated by LINZ DEM Download Tool  |  {now}',
            'Equivalent to the metadata provided when downloading from data.linz.govt.nz',
            '',
            dash,
            'IDENTIFICATION',
            dash,
            f'  Title            : {survey.get("title", "—")}',
            f'  Collection ID    : {survey.get("id", "—")}',
            f'  Slug             : {survey.get("linz_slug", "—")}',
            f'  Category         : {survey.get("linz_category", "dem").upper()}  '
            f'(Digital Elevation Model)',
            f'  Region           : {survey.get("linz_region", "—").title()}',
            f'  Lifecycle Status : {survey.get("linz_lifecycle", "—").title()}',
            f'  Security Class   : {survey.get("linz_security", "—").title()}',
            '',
            dash,
            'DESCRIPTION',
            dash,
            f'  {survey.get("description", "No description available.")}',
            '',
            dash,
            'DATES',
            dash,
            f'  Survey Period    : {t_start}  →  {t_end}',
            f'  Record Created   : {survey.get("created", "—")[:10]}',
            f'  Record Updated   : {survey.get("updated", "—")[:10]}',
            '',
            dash,
            'SPATIAL EXTENT  (WGS84 Geographic / EPSG:4326)',
            dash,
            bbox_str,
            '',
            dash,
            'SPATIAL REFERENCE',
            dash,
            f'  Horizontal CRS   : NZGD2000 / New Zealand Transverse Mercator 2000 '
            f'(EPSG:2193)',
            f'  Vertical Datum   : {vert_datum}',
            f'  Ground Samp Dist : {survey.get("gsd", 1)} m (nominal)',
            '',
            dash,
            'RESPONSIBLE PARTIES',
            dash,
            f'  Producer(s)      : {"; ".join(producers) or "—"}',
            f'  Licensor(s)      : {"; ".join(licensors) or "—"}',
            f'  Host / Processor : {"; ".join(processors) or "—"}',
            '',
            dash,
            'RIGHTS AND CONSTRAINTS',
            dash,
            f'  License          : {survey.get("license", "CC-BY-4.0")}',
            '  License URL      : https://creativecommons.org/licenses/by/4.0/',
            '  Use Limitation   : Attribution required. Cite source as:',
            f'    "{survey.get("title", "LINZ LiDAR DEM")} — '
            'Toitū Te Whenua Land Information New Zealand"',
            '',
            dash,
            'DISTRIBUTION',
            dash,
            '  Format           : Cloud Optimised GeoTIFF (COG)',
            f'  Source Catalog   : {survey.get("url", "—")}',
            '  LINZ Open Data   : https://data.linz.govt.nz',
            '  AWS Open Data    : https://nz-elevation.s3.ap-southeast-2.amazonaws.com',
            '',
            dash,
            'DOWNLOADED FILE INFORMATION',
            dash,
        ] + raster_lines + [
            '',
            dash,
            'COMPANION FILES (same folder)',
            dash,
        ] + companion_lines + [
            '',
            sep,
            'NOTE: Surveys from different years may use different vertical datums.',
            'Do not merge rasters without verifying datum consistency.',
            sep,
        ]

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(sections) + '\n')

    def _get_vertical_datum(self, raster_path, description=''):
        """Extracts vertical datum from the raster CRS; falls back to description parsing."""
        try:
            from osgeo import gdal, osr
            ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
            if ds:
                wkt = ds.GetProjectionRef()
                ds = None
                if wkt:
                    srs = osr.SpatialReference()
                    srs.ImportFromWkt(wkt)
                    if srs.IsCompound():
                        v = srs.GetAttrValue('VERT_CS')
                        if v:
                            return v
        except Exception:
            pass

        desc_upper = (description or '').upper()
        m = re.search(r'NZVD\s*(\d{4})', desc_upper)
        if m:
            yr = m.group(1)
            return f'New Zealand Vertical Datum {yr} (NZVD{yr})'

        return ('New Zealand Vertical Datum 2016 (NZVD2016) — assumed for surveys '
                'post-2016; verify against source if critical')

    # =========================================================================
    # Summary writer
    # =========================================================================

    def _write_summary(self, output_folder, downloads, xmin, ymin, xmax, ymax,
                       w, s, e, n, crs, resolution, feedback):
        """Writes download_summary.txt with metadata and raster stats for every file."""
        txt_path = os.path.join(output_folder, 'download_summary.txt')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        lines = [
            'LINZ LiDAR DEM — Download Summary',
            '=' * 60,
            f'Generated    : {now}',
            f'Output CRS   : {crs}',
            f'Resolution   : {resolution} m (requested)',
            '',
            'Extent',
            f'  NZTM  : {xmin:.0f}, {ymin:.0f}, {xmax:.0f}, {ymax:.0f}',
            f'  WGS84 : {w:.5f}, {s:.5f}, {e:.5f}, {n:.5f}',
            f'  Size  : {xmax-xmin:.0f} m × {ymax-ymin:.0f} m',
            '',
            f'Files Downloaded : {len(downloads)}',
            '',
        ]

        for i, d in enumerate(downloads, 1):
            lines.append(f'{"─" * 60}')
            lines.append(f'File {i} of {len(downloads)}')
            lines.append(f'  Filename      : {d["filename"]}')
            lines.append(f'  Source        : {d.get("source", "—")}')

            if d.get('title'):
                lines.append(f'  Survey Title  : {d["title"]}')
            if d.get('id'):
                lines.append(f'  Collection ID : {d["id"]}')
            if d.get('year'):
                lines.append(f'  Survey Year   : {d["year"]}')

            temporal = d.get('temporal') or []
            t_start = temporal[0] if len(temporal) > 0 and temporal[0] else 'unknown'
            t_end   = temporal[1] if len(temporal) > 1 and temporal[1] else 'present'
            if t_start != 'unknown':
                lines.append(
                    f'  Survey Dates  : {t_start[:10]}  →  '
                    f'{t_end[:10] if t_end != "present" else "present"}'
                )

            if d.get('created'):
                lines.append(f'  Record Created: {d["created"][:10]}')
            if d.get('updated'):
                lines.append(f'  Record Updated: {d["updated"][:10]}')

            bbox = d.get('bbox')
            if bbox and len(bbox) >= 4:
                if len(bbox) == 6:
                    bw, bs, be, bn = bbox[0], bbox[1], bbox[3], bbox[4]
                else:
                    bw, bs, be, bn = bbox[0], bbox[1], bbox[2], bbox[3]
                lines.append(
                    f'  Survey Bbox   : {bw:.4f}, {bs:.4f}, {be:.4f}, {bn:.4f}  (WGS84 W,S,E,N)'
                )

            if d.get('linz_region'):
                lines.append(f'  Region        : {d["linz_region"]}')
            if d.get('linz_slug'):
                lines.append(f'  Slug          : {d["linz_slug"]}')
            if d.get('linz_lifecycle'):
                lines.append(f'  Lifecycle     : {d["linz_lifecycle"]}')
            if d.get('linz_category'):
                lines.append(f'  Category      : {d["linz_category"]}')
            if d.get('linz_security'):
                lines.append(f'  Security      : {d["linz_security"]}')
            if d.get('gsd'):
                lines.append(f'  GSD           : {d["gsd"]} m')
            if d.get('license'):
                lines.append(f'  License       : {d["license"]}')

            providers = d.get('providers') or []
            if providers:
                prov_str = '; '.join(
                    f'{p.get("name", "")} ({", ".join(p.get("roles", []))})'
                    for p in providers
                )
                lines.append(f'  Providers     : {prov_str}')

            if d.get('tile_urls'):
                lines.append(f'  Tiles Used    : {len(d["tile_urls"])}')
            if d.get('url'):
                lines.append(f'  Catalog URL   : {d["url"]}')
            if d.get('description'):
                desc = d['description'].strip()[:300]
                lines.append(f'  Description   : {desc}')

            if d.get('meta_txt_filename'):
                lines.append(f'  Metadata TXT  : {d["meta_txt_filename"]}')
            if d.get('meta_filename'):
                lines.append(f'  Metadata JSON : {d["meta_filename"]}')
            if d.get('capture_area_filename'):
                lines.append(f'  Capture Area  : {d["capture_area_filename"]}')

            path = d.get('path', '')
            if path and os.path.exists(path):
                try:
                    from osgeo import gdal
                    ds = gdal.Open(path, gdal.GA_ReadOnly)
                    if ds:
                        gt   = ds.GetGeoTransform()
                        cols = ds.RasterXSize
                        rows = ds.RasterYSize
                        actual_res = abs(gt[1])
                        file_mb    = os.path.getsize(path) / (1024 * 1024)
                        band       = ds.GetRasterBand(1)
                        stats      = band.ComputeStatistics(False)
                        lines += [
                            f'  Dimensions    : {cols} × {rows} pixels',
                            f'  Pixel Size    : {actual_res:.4f} m',
                            f'  File Size     : {file_mb:.2f} MB',
                            f'  Elev Min      : {stats[0]:.3f} m',
                            f'  Elev Max      : {stats[1]:.3f} m',
                            f'  Elev Mean     : {stats[2]:.3f} m',
                            f'  Elev Std Dev  : {stats[3]:.3f} m',
                        ]
                        ds = None
                except Exception:
                    pass

            lines.append('')

        lines += [
            '=' * 60,
            'Note: Surveys from different years may use different vertical datums.',
            'Do not merge files without verifying datum consistency.',
        ]

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        feedback.pushInfo(f'\nSummary written: {txt_path}')

    # =========================================================================
    # Helpers
    # =========================================================================

    def _has_valid_data(self, raster_path):
        """Returns True if the raster contains at least one non-nodata pixel."""
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            ds = gdal.Open(raster_path, gdal.GA_ReadOnly)
            if not ds:
                return False
            band = ds.GetRasterBand(1)
            try:
                band.ComputeStatistics(True)  # approx_ok — fast; raises if all nodata
                return True
            except Exception:
                return False
            finally:
                ds = None
        except Exception:
            return True  # default: keep the file if the check itself fails

    def _fetch_json(self, url):
        req = urllib.request.Request(
            url, headers={'User-Agent': 'QGIS-FloodPreModelling/1.0'}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _intersects_wgs84(self, bbox, w, s, e, n):
        """Returns True if bbox intersects [w,s,e,n].

        Handles both 4-element [west, south, east, north] and 6-element
        [west, south, min_elev, east, north, max_elev] STAC bboxes.
        """
        if not bbox or len(bbox) < 4:
            return True  # unknown — include it
        if len(bbox) == 6:
            bw, bs, be, bn = bbox[0], bbox[1], bbox[3], bbox[4]
        else:
            bw, bs, be, bn = bbox[0], bbox[1], bbox[2], bbox[3]
        return not (be < w or bw > e or bn < s or bs > n)
