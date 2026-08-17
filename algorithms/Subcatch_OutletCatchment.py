# -*- coding: utf-8 -*-
"""
Outlet-based catchment delineation.

Uses the drainage direction raster produced by StreamCatchmentGenerator
(r.watershed) and one or more outlet points to delineate the contributing
catchment area above each outlet via r.water.outlet.  r.watershed does NOT
run again — only the fast r.water.outlet step runs per outlet.
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"
__status__  = "Stable"

import math

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorDestination,
    QgsProcessingMultiStepFeedback,
    QgsProcessingException,
    QgsProcessingOutputLayerDefinition,
    QgsCoordinateTransform,
    QgsProject,
    QgsSpatialIndex,
    QgsPointXY,
    QgsVectorLayer,
    QgsProcessingUtils,
)
import processing

from .Subcatch_Utils import _QuietFeedback, _grass_prefix


class OutletCatchmentGenerator(QgsProcessingAlgorithm):
    """
    Delineates the contributing catchment above each outlet point using the
    drainage direction raster from the Stream and Catchment Generator.
    r.watershed does not re-run — only the fast r.water.outlet step executes
    per outlet.
    """

    INPUT_DRAINAGE             = 'INPUT_DRAINAGE'
    INPUT_OUTLETS              = 'INPUT_OUTLETS'
    INPUT_STREAMS              = 'INPUT_STREAMS'
    INPUT_SUBCATCHMENTS        = 'INPUT_SUBCATCHMENTS'
    SNAP_TOLERANCE             = 'SNAP_TOLERANCE'
    OUTPUT_CATCHMENTS          = 'OUTPUT_CATCHMENTS'
    OUTPUT_CATCHMENTS_UNMERGED = 'OUTPUT_CATCHMENTS_UNMERGED'
    OUTPUT_STREAMS             = 'OUTPUT_STREAMS'

    def tr(self, s):
        return QCoreApplication.translate('Processing', s)

    def createInstance(self):
        return OutletCatchmentGenerator()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'outlet_catchment_generator'

    def displayName(self):
        return self.tr('Outlet Catchment Generator')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Delineates the contributing catchment area above each outlet point "
            "using the drainage direction raster from the Stream and Catchment Generator.\n\n"
            "Run 'Stream and Catchment Generator' first to produce the drainage direction "
            "raster, then use this tool for outlet-specific catchments.\n\n"
            "Inputs:\n"
            "  - Drainage direction raster (OUTPUT_DRAINAGE from Stream and Catchment Generator)\n"
            "  - Outlet points (vector point layer — one catchment per point)\n"
            "  - Stream network (optional, for snapping outlets to stream centreline)\n"
            "  - Snap tolerance (m) — 0 = no snapping\n\n"
            "Inputs:\n"
            "  - Sub-catchment polygons (optional, from Stream and Catchment Generator)\n"
            "    When provided, a second output with the individual sub-catchments\n"
            "    within each contributing area is produced.\n\n"
            "Outputs:\n"
            "  - Outlet Catchments: merged polygon per outlet (geometry only)\n"
            "  - Outlet Sub-Catchments: individual sub-catchment polygons within each\n"
            "    contributing area, with SubcatchID field only (requires sub-catchments input)"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DRAINAGE, self.tr('Drainage direction raster'),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_OUTLETS, self.tr('Outlet points'),
                types=[QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_STREAMS,
                self.tr('Stream network (optional — used for outlet snapping)'),
                types=[QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_SUBCATCHMENTS,
                self.tr('Sub-catchment polygons (optional — for individual sub-catchment output)'),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SNAP_TOLERANCE,
                self.tr('Snap tolerance (m) — 0 = no snapping'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0, minValue=0.0, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_CATCHMENTS, self.tr('Outlet catchment polygons'),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_CATCHMENTS_UNMERGED,
                self.tr('Outlet sub-catchments (individual polygons within contributing area)'),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_STREAMS,
                self.tr('Stream network within contributing area(s)'),
                optional=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        grass = _grass_prefix()
        if grass is None:
            raise QgsProcessingException(self.tr(
                "GRASS GIS provider not found. Install GRASS GIS and enable it "
                "under Processing - Options - Providers - GRASS GIS, then restart QGIS."
            ))

        drainage = self.parameterAsRasterLayer(parameters, self.INPUT_DRAINAGE, context)
        if drainage is None:
            raise QgsProcessingException(self.tr("Invalid or missing drainage direction raster."))

        outlets_lyr = self.parameterAsVectorLayer(parameters, self.INPUT_OUTLETS, context)
        if outlets_lyr is None or outlets_lyr.featureCount() == 0:
            raise QgsProcessingException(self.tr("Outlet points layer is empty or invalid."))

        streams_lyr  = self.parameterAsVectorLayer(parameters, self.INPUT_STREAMS, context)
        subcatch_lyr = self.parameterAsVectorLayer(parameters, self.INPUT_SUBCATCHMENTS, context)
        snap_tol     = self.parameterAsDouble(parameters, self.SNAP_TOLERANCE, context)

        crs     = drainage.crs()
        pixel_x = abs(drainage.rasterUnitsPerPixelX())
        extent  = drainage.extent()

        # CRS transform: outlets → drainage CRS
        xform = None
        if outlets_lyr.crs() != crs:
            xform = QgsCoordinateTransform(outlets_lyr.crs(), crs, QgsProject.instance())

        # Spatial index for stream snapping
        snap_index = None
        snap_feats = {}
        if streams_lyr is not None and snap_tol > 0:
            feedback.pushInfo(
                f"Building stream index for snapping (tolerance {snap_tol:.1f} m)..."
            )
            snap_index = QgsSpatialIndex()
            for f in streams_lyr.getFeatures():
                snap_index.insertFeature(f)
                snap_feats[f.id()] = f

        features  = list(outlets_lyr.getFeatures())
        n_outlets = len(features)
        # 2 per outlet (watershed + polygonise) + 2 final (merge + fix+area)
        # + 3 if sub-catchments provided (intersect + fix + area)
        # + 1 if streams provided (clip to contributing area)
        n_steps = (n_outlets * 2 + 2
                   + (3 if subcatch_lyr is not None else 0)
                   + (1 if streams_lyr is not None else 0))
        mfb  = QgsProcessingMultiStepFeedback(n_steps, feedback)
        step = 0

        catchment_layers = []

        for i, feat in enumerate(features):
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            if geom.isEmpty():
                feedback.pushWarning(f"  Outlet {i + 1}: empty geometry — skipped.")
                step += 2
                continue

            pt   = geom.asPoint()
            x, y = pt.x(), pt.y()

            if xform:
                tp   = xform.transform(QgsPointXY(x, y))
                x, y = tp.x(), tp.y()

            # Snap to nearest stream
            if snap_index is not None:
                nearest = snap_index.nearestNeighbor(QgsPointXY(x, y), 1)
                if nearest:
                    sq_dist, closest, _, _ = (
                        snap_feats[nearest[0]].geometry()
                        .closestSegmentWithContext(QgsPointXY(x, y))
                    )
                    dist = math.sqrt(sq_dist)
                    if dist <= snap_tol:
                        x, y = closest.x(), closest.y()
                        feedback.pushInfo(
                            f"  Outlet {i + 1}: snapped {dist:.1f} m to stream."
                        )
                    else:
                        feedback.pushWarning(
                            f"  Outlet {i + 1}: nearest stream {dist:.1f} m away "
                            f"(> tolerance {snap_tol:.1f} m) — using original location."
                        )

            # ── r.water.outlet ────────────────────────────────────────────────
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo(
                f"Outlet {i + 1}/{n_outlets}: r.water.outlet at ({x:.1f}, {y:.1f})..."
            )
            try:
                wout = processing.run(
                    f'{grass}:r.water.outlet',
                    {
                        'input'      : drainage,
                        'coordinates': f'{x},{y}',
                        'output'     : QgsProcessing.TEMPORARY_OUTPUT,
                        'GRASS_REGION_PARAMETER'         : extent,
                        'GRASS_REGION_CELLSIZE_PARAMETER': pixel_x,
                        'GRASS_RASTER_FORMAT_OPT' : '',
                        'GRASS_RASTER_FORMAT_META': '',
                    },
                    context=context,
                    feedback=_QuietFeedback(mfb),
                    is_child_algorithm=True,
                )
            except Exception as exc:
                feedback.reportError(
                    f"  Outlet {i + 1}: r.water.outlet failed — {exc}. "
                    "Ensure the point falls within the DEM extent and on a valid flow cell.",
                    fatal=False,
                )
                step += 1
                continue

            # ── Polygonise → filter → dissolve → tag ──────────────────────────
            mfb.setCurrentStep(step); step += 1

            poly = processing.run(
                'gdal:polygonize',
                {
                    'INPUT': wout['output'], 'BAND': 1,
                    'FIELD': 'dn', 'EIGHT_CONNECTEDNESS': False, 'EXTRA': '',
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )

            filt = processing.run(
                'native:extractbyexpression',
                {
                    'INPUT': poly['OUTPUT'],
                    'EXPRESSION': '"dn" > 0',
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )

            dissolved = processing.run(
                'native:dissolve',
                {
                    'INPUT': filt['OUTPUT'], 'FIELD': [],
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )

            assigned = processing.run(
                'native:assignprojection',
                {'INPUT': dissolved['OUTPUT'], 'CRS': crs,
                 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )
            catchment_layers.append(assigned['OUTPUT'])
            feedback.pushInfo(f"  Outlet {i + 1}: catchment delineated.\n")

        if not catchment_layers:
            raise QgsProcessingException(
                "No catchments were successfully delineated. "
                "Check that outlet points fall within the drainage raster extent "
                "and on valid flow cells."
            )

        # ── Merge all outlet catchments ───────────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo(f"Merging {len(catchment_layers)} outlet catchment(s)...")
        merged = processing.run(
            'native:mergevectorlayers',
            {'LAYERS': catchment_layers, 'CRS': crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
            context=context, feedback=mfb, is_child_algorithm=True,
        )

        # ── Fix geometries and write output ──────────────────────────────────
        mfb.setCurrentStep(step); step += 1
        feedback.pushInfo("Fixing geometries and writing output...")

        # Keep fixed as a temp layer so it can be used cleanly as OVERLAY for
        # the intersection step below.
        fixed = processing.run(
            'native:fixgeometries',
            {'INPUT': merged['OUTPUT'], 'METHOD': 1,
             'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
            context=context, feedback=mfb, is_child_algorithm=True,
        )

        out_def = parameters[self.OUTPUT_CATCHMENTS]
        if not isinstance(out_def, QgsProcessingOutputLayerDefinition):
            out_def = QgsProcessingOutputLayerDefinition(out_def)
        out_def.destinationName = 'Outlet Catchments'

        # Strip internal fields added by polygonize (dn) and mergevectorlayers
        # (layer, path).  native:retainfields with FIELDS=[] is rejected by QGIS,
        # so we use deletecolumn on the known field names instead.
        # fixed['OUTPUT'] may be a file-path string rather than a QgsVectorLayer,
        # so resolve it to a layer object before inspecting fields.
        fixed_out = fixed['OUTPUT']
        if isinstance(fixed_out, str):
            fixed_lyr = QgsVectorLayer(fixed_out, '_tmp_fixed', 'ogr')
        else:
            fixed_lyr = fixed_out
        internal_fields = {'dn', 'layer', 'path', 'fid'}
        cols_to_drop = [
            f.name() for f in fixed_lyr.fields()
            if f.name().lower() in internal_fields
        ]
        if cols_to_drop:
            cleaned = processing.run(
                'native:deletecolumn',
                {'INPUT': fixed_out, 'COLUMN': cols_to_drop, 'OUTPUT': out_def},
                context=context, feedback=mfb, is_child_algorithm=True,
            )
            result = {self.OUTPUT_CATCHMENTS: cleaned['OUTPUT']}
        else:
            passthrough = processing.run(
                'native:fixgeometries',
                {'INPUT': fixed_out, 'METHOD': 1, 'OUTPUT': out_def},
                context=context, feedback=mfb, is_child_algorithm=True,
            )
            result = {self.OUTPUT_CATCHMENTS: passthrough['OUTPUT']}

        if subcatch_lyr is not None:
            # Intersect sub-catchments with contributing areas → individual polygons per outlet
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo("Intersecting sub-catchments with contributing areas...")
            intersect = processing.run(
                'native:intersection',
                {
                    'INPUT'                : subcatch_lyr,
                    'OVERLAY'              : fixed_out,
                    'INPUT_FIELDS'         : [],
                    'OVERLAY_FIELDS'       : [],
                    'OVERLAY_FIELDS_PREFIX': '',
                    'OUTPUT'               : QgsProcessing.TEMPORARY_OUTPUT,
                },
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )

            mfb.setCurrentStep(step); step += 1
            fix_sub = processing.run(
                'native:fixgeometries',
                {'INPUT': intersect['OUTPUT'], 'METHOD': 1,
                 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )

            mfb.setCurrentStep(step); step += 1
            out_sub_dest = parameters.get(self.OUTPUT_CATCHMENTS_UNMERGED)
            if out_sub_dest is None:
                out_sub_dest = QgsProcessing.TEMPORARY_OUTPUT
            if not isinstance(out_sub_dest, QgsProcessingOutputLayerDefinition):
                out_sub_dest = QgsProcessingOutputLayerDefinition(str(out_sub_dest))
            out_sub_dest.destinationName = 'Outlet Sub-Catchments'

            # Keep only SubcatchID — drop any fields introduced by the intersection.
            # native:retainfields requires a non-empty FIELDS list, so fall back
            # to fixgeometries (keeps all fields) when SubcatchID is absent.
            if subcatch_lyr.fields().indexOf('SubcatchID') >= 0:
                out_sub = processing.run(
                    'native:retainfields',
                    {'INPUT': fix_sub['OUTPUT'], 'FIELDS': ['SubcatchID'],
                     'OUTPUT': out_sub_dest},
                    context=context, feedback=mfb, is_child_algorithm=True,
                )
            else:
                out_sub = processing.run(
                    'native:fixgeometries',
                    {'INPUT': fix_sub['OUTPUT'], 'METHOD': 1, 'OUTPUT': out_sub_dest},
                    context=context, feedback=mfb, is_child_algorithm=True,
                )
            result[self.OUTPUT_CATCHMENTS_UNMERGED] = out_sub['OUTPUT']
            feedback.pushInfo(
                f"  {intersect['OUTPUT'].featureCount() if hasattr(intersect['OUTPUT'], 'featureCount') else '?'} "
                "sub-catchment polygon(s) within contributing area(s)."
            )

        if streams_lyr is not None:
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo("Clipping stream network to contributing area(s)...")

            out_streams_dest = parameters.get(self.OUTPUT_STREAMS)
            if out_streams_dest is None:
                out_streams_dest = QgsProcessing.TEMPORARY_OUTPUT
            if not isinstance(out_streams_dest, QgsProcessingOutputLayerDefinition):
                out_streams_dest = QgsProcessingOutputLayerDefinition(str(out_streams_dest))
            out_streams_dest.destinationName = 'Outlet Streams'

            clipped_streams = processing.run(
                'native:clip',
                {
                    'INPUT'  : streams_lyr,
                    'OVERLAY': fixed_out,
                    'OUTPUT' : out_streams_dest,
                },
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )
            result[self.OUTPUT_STREAMS] = clipped_streams['OUTPUT']

        feedback.pushInfo("--- Outlet Catchment Generator complete ---")
        return result
