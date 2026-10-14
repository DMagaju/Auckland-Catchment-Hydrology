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
import os

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterField,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterVectorDestination,
    QgsProcessingMultiStepFeedback,
    QgsProcessingException,
    QgsProcessingOutputLayerDefinition,
    QgsCoordinateTransform,
    QgsProject,
    QgsSpatialIndex,
    QgsPointXY,
    QgsGeometry,
    QgsField,
    QgsVectorLayer,
    QgsProcessingUtils,
)
import processing

from .Subcatch_Utils import _QuietFeedback, _grass_prefix, _safe_filename


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
    SPLIT_OUTPUTS              = 'SPLIT_OUTPUTS'
    SEPARATE_OUTPUTS           = 'SEPARATE_OUTPUTS'
    OUTLET_ID_FIELD            = 'OUTLET_ID_FIELD'
    OUTPUT_FOLDER              = 'OUTPUT_FOLDER'
    OUTPUT_CATCHMENTS          = 'OUTPUT_CATCHMENTS'
    OUTPUT_CATCHMENTS_UNMERGED = 'OUTPUT_CATCHMENTS_UNMERGED'
    OUTPUT_CATCHMENTS_SPLIT    = 'OUTPUT_CATCHMENTS_SPLIT'
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
            "Outputs (default — all outlets combined):\n"
            "  - Outlet Catchments: merged polygon per outlet (geometry only)\n"
            "  - Outlet Sub-Catchments: individual sub-catchment polygons within each\n"
            "    contributing area, with SubcatchID field only (requires sub-catchments input)\n\n"
            "Separate output mode (tick 'Save each outlet as separate layers'):\n"
            "  - Instead of one combined set of layers, writes Catchment/SubCatchment/\n"
            "    Stream/Outlet shapefiles PER OUTLET into the chosen output folder,\n"
            "    named from the outlet's own ID field (e.g. US1_Catchment.shp).\n"
            "    Requires: Outlet ID field and Output folder.\n\n"
            "Split catchments (tick 'Split catchments between outlets'):\n"
            "  - By default, outlet catchments are nested/overlapping — a downstream\n"
            "    outlet's catchment fully contains any upstream outlet's catchment.\n"
            "  - When ticked, an additional non-overlapping version is produced, with\n"
            "    each outlet's catchment reduced to exclude the area already captured\n"
            "    by its upstream outlet(s). Works independently of (and together with)\n"
            "    the separate output mode above — unticked, nothing changes from the\n"
            "    default nested/overlapping behaviour."
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

        # ── Split (non-overlapping) catchments ─────────────────────────────
        # Independent of SEPARATE_OUTPUTS below — unticked (default), nothing
        # changes from today's nested/overlapping catchments. Ticked, an
        # additional non-overlapping version is produced alongside the normal
        # one, in whichever output mode (combined or separate-layers) is
        # already in use.
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SPLIT_OUTPUTS,
                self.tr('Split catchments between outlets (remove upstream overlap)'),
                defaultValue=False, optional=True,
            )
        )

        # ── Per-outlet separate output mode ───────────────────────────────
        # Instead of merging every outlet's result into one combined set of
        # layers (the default below), write each outlet's own Catchment/
        # SubCatchment/Stream/Outlet as its own file, named from the
        # outlet's own ID field. OUTLET_ID_FIELD/OUTPUT_FOLDER are only
        # actually required when SEPARATE_OUTPUTS is ticked — validated at
        # run time in processAlgorithm (QGIS Processing has no clean way to
        # make a parameter's requiredness depend on another parameter's
        # value in the standard algorithm dialog), rather than left silently
        # optional with no real error if left blank.
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SEPARATE_OUTPUTS,
                self.tr('Save each outlet as separate layers (instead of one combined layer)'),
                defaultValue=False, optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.OUTLET_ID_FIELD,
                self.tr(
                    'Outlet ID field (optional — copied into an "OutletID" attribute on '
                    'every catchment polygon so you know which outlet it belongs to; also '
                    'names each outlet\'s own output files — required if the above is ticked)'
                ),
                parentLayerParameterName=self.INPUT_OUTLETS,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                self.tr('Output folder for separate per-outlet layers (required if the above is ticked)'),
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_CATCHMENTS,
                self.tr('Outlet catchment polygons (not used in separate-layers mode)'),
                optional=True,
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
                self.OUTPUT_CATCHMENTS_SPLIT,
                self.tr('Outlet catchments split (non-overlapping — only used if "Split catchments" is ticked)'),
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
        split_outputs = self.parameterAsBool(parameters, self.SPLIT_OUTPUTS, context)

        separate_outputs = self.parameterAsBool(parameters, self.SEPARATE_OUTPUTS, context)
        id_field          = self.parameterAsString(parameters, self.OUTLET_ID_FIELD, context)
        out_folder        = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        if separate_outputs:
            if not id_field:
                raise QgsProcessingException(self.tr(
                    "'Save each outlet as separate layers' is ticked but no Outlet ID field "
                    "was chosen — pick the field in the outlet layer that holds each outlet's "
                    "own ID (e.g. 'US1', 'DS1')."
                ))
            if outlets_lyr.fields().indexOf(id_field) < 0:
                raise QgsProcessingException(self.tr(
                    f"Outlet ID field '{id_field}' was not found on the outlet points layer."
                ))
            if not out_folder:
                raise QgsProcessingException(self.tr(
                    "'Save each outlet as separate layers' is ticked but no output folder "
                    "was chosen."
                ))
            os.makedirs(out_folder, exist_ok=True)
        else:
            out_def_check = parameters.get(self.OUTPUT_CATCHMENTS)
            if not out_def_check:
                raise QgsProcessingException(self.tr(
                    "Outlet catchment polygons output is required unless "
                    "'Save each outlet as separate layers' is ticked."
                ))

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
        if separate_outputs:
            # Per outlet: r.water.outlet + polygonise/filter/dissolve/assign
            # (2, same as combined mode) + save catchment (2) + save outlet
            # point (1) + optional sub-catchment save (3) + optional stream
            # save (1) — no final merge steps, each outlet is written as it
            # goes.
            n_steps = n_outlets * (2 + 2 + 1
                                    + (3 if subcatch_lyr is not None else 0)
                                    + (1 if streams_lyr is not None else 0))
        else:
            # 2 per outlet (watershed + polygonise) + 2 final (merge + fix+area)
            # + 3 if sub-catchments provided (intersect + fix + area)
            # + 1 if streams provided (clip to contributing area)
            n_steps = (n_outlets * 2 + 2
                       + (3 if subcatch_lyr is not None else 0)
                       + (1 if streams_lyr is not None else 0))
        if split_outputs:
            # Split-catchment phase runs after the main loop and its cost
            # depends on how many outlets actually have upstream overlap
            # (unknown up front) — reserve a generous buffer per outlet
            # (union + dissolve + difference + fix [+ write, separate mode])
            # rather than trying to predict it exactly; unused steps just
            # mean the progress bar doesn't tick during part of that phase.
            n_steps += n_outlets * (5 if separate_outputs else 4)
        mfb  = QgsProcessingMultiStepFeedback(n_steps, feedback)
        step = 0

        catchment_layers = []   # combined mode only — temp layers merged into one output below
        saved_ids        = []   # separate-layers mode only — outlet IDs successfully written
        full_catchments  = []   # every outlet's own full (nested) catchment — used by the
                                 # split-catchment phase below regardless of output mode

        def _tag_outlet_id(output, value):
            """Stamps an 'OutletID' string attribute (one value, every
            feature) onto a catchment output, in place, so the polygon can
            be traced back to the outlet point it was delineated from —
            independent of output mode or file naming."""
            lyr = QgsVectorLayer(output, '_tmp_tag', 'ogr') if isinstance(output, str) else output
            dp = lyr.dataProvider()
            if lyr.fields().indexOf('OutletID') < 0:
                dp.addAttributes([QgsField('OutletID', QVariant.String, '', 100)])
                lyr.updateFields()
            idx = lyr.fields().indexOf('OutletID')
            dp.changeAttributeValues({f.id(): {idx: value} for f in lyr.getFeatures()})
            return lyr

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

            if id_field:
                raw_id_val = feat[id_field]
                outlet_id_value = '' if raw_id_val is None else str(raw_id_val)
                safe_id = _safe_filename(raw_id_val)
            else:
                outlet_id_value = f'Outlet_{i + 1}'
                safe_id = outlet_id_value
            assigned['OUTPUT'] = _tag_outlet_id(assigned['OUTPUT'], outlet_id_value)

            if split_outputs:
                full_catchments.append({
                    'safe_id': safe_id,
                    'point'  : QgsPointXY(x, y),
                    'output' : assigned['OUTPUT'],
                })

            if not separate_outputs:
                catchment_layers.append(assigned['OUTPUT'])
                feedback.pushInfo(f"  Outlet {i + 1}: catchment delineated.\n")
                continue

            # ── Separate-layers mode: write this outlet's own files now,
            # rather than collecting it for a later cross-outlet merge. ──
            feedback.pushInfo(f"  Outlet {i + 1} ('{safe_id}'): saving separate layers...")

            # Catchment — fix geometry, drop the leftover 'dn'/'fid' field
            # polygonize left behind, write straight to its own file (same
            # intent as the deletecolumn step the combined path does once,
            # after merging — done per outlet here instead).
            mfb.setCurrentStep(step); step += 1
            cat_fixed = processing.run(
                'native:fixgeometries',
                {'INPUT': assigned['OUTPUT'], 'METHOD': 1, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )
            mfb.setCurrentStep(step); step += 1
            cat_path = os.path.join(out_folder, f'{safe_id}_Catchment.shp')
            cat_fixed_out = cat_fixed['OUTPUT']
            cat_fixed_lyr = (QgsVectorLayer(cat_fixed_out, '_tmp_cat', 'ogr')
                              if isinstance(cat_fixed_out, str) else cat_fixed_out)
            cols_to_drop = [f.name() for f in cat_fixed_lyr.fields() if f.name().lower() in ('dn', 'fid')]
            if cols_to_drop:
                processing.run(
                    'native:deletecolumn',
                    {'INPUT': cat_fixed_out, 'COLUMN': cols_to_drop, 'OUTPUT': cat_path},
                    context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                )
            else:
                processing.run(
                    'native:fixgeometries',
                    {'INPUT': cat_fixed_out, 'METHOD': 1, 'OUTPUT': cat_path},
                    context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                )
            feedback.pushInfo(f"    Catchment    -> {cat_path}")

            # Outlet point — just this one feature, re-saved on its own
            # (nothing analogous exists in the combined-output mode today).
            mfb.setCurrentStep(step); step += 1
            outlet_path = os.path.join(out_folder, f'{safe_id}_Outlet.shp')
            processing.run(
                'native:extractbyexpression',
                {'INPUT': outlets_lyr, 'EXPRESSION': f'$id = {feat.id()}', 'OUTPUT': outlet_path},
                context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
            )
            feedback.pushInfo(f"    Outlet       -> {outlet_path}")

            # Sub-catchments within THIS outlet's own contributing area —
            # same intersection the combined mode does once across all
            # outlets at once, done per outlet here instead.
            if subcatch_lyr is not None:
                mfb.setCurrentStep(step); step += 1
                sub_intersect = processing.run(
                    'native:intersection',
                    {
                        'INPUT': subcatch_lyr, 'OVERLAY': assigned['OUTPUT'],
                        'INPUT_FIELDS': [], 'OVERLAY_FIELDS': [], 'OVERLAY_FIELDS_PREFIX': '',
                        'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
                    },
                    context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                )
                mfb.setCurrentStep(step); step += 1
                sub_fixed = processing.run(
                    'native:fixgeometries',
                    {'INPUT': sub_intersect['OUTPUT'], 'METHOD': 1, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                    context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                )
                mfb.setCurrentStep(step); step += 1
                sub_path = os.path.join(out_folder, f'{safe_id}_SubCatchment.shp')
                if subcatch_lyr.fields().indexOf('SubcatchID') >= 0:
                    processing.run(
                        'native:retainfields',
                        {'INPUT': sub_fixed['OUTPUT'], 'FIELDS': ['SubcatchID'], 'OUTPUT': sub_path},
                        context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                    )
                else:
                    processing.run(
                        'native:fixgeometries',
                        {'INPUT': sub_fixed['OUTPUT'], 'METHOD': 1, 'OUTPUT': sub_path},
                        context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                    )
                feedback.pushInfo(f"    SubCatchment -> {sub_path}")

            # Stream network clipped to THIS outlet's own contributing area.
            if streams_lyr is not None:
                mfb.setCurrentStep(step); step += 1
                stream_path = os.path.join(out_folder, f'{safe_id}_Stream.shp')
                processing.run(
                    'native:clip',
                    {'INPUT': streams_lyr, 'OVERLAY': assigned['OUTPUT'], 'OUTPUT': stream_path},
                    context=context, feedback=_QuietFeedback(mfb), is_child_algorithm=True,
                )
                feedback.pushInfo(f"    Stream       -> {stream_path}")

            saved_ids.append(safe_id)
            feedback.pushInfo(f"  Outlet {i + 1} ('{safe_id}'): done.\n")

        def _compute_split_catchments():
            """Returns [{'safe_id', 'output'}, ...] — each outlet's own full
            catchment with any upstream outlet(s)' catchment(s) subtracted
            out, so results no longer overlap. An outlet with nothing nested
            inside its catchment is returned unchanged."""
            feedback.pushInfo("Computing split (non-overlapping) catchments...")

            def _combined_geom(output):
                lyr = QgsVectorLayer(output, '_tmp', 'ogr') if isinstance(output, str) else output
                geom = None
                for f in lyr.getFeatures():
                    geom = f.geometry() if geom is None else geom.combine(f.geometry())
                return geom

            geoms = [_combined_geom(item['output']) for item in full_catchments]
            # Raster-to-vector polygon boundaries (gdal:polygonize) can sit up to
            # half a pixel away from an outlet's exact continuous coordinate, so
            # testing the bare point against the polygon can miss genuine nesting
            # by a hair. Buffer the point by a full pixel width to absorb that —
            # still far smaller than the gap between genuinely separate outlets.
            point_tolerance = pixel_x
            results = []
            for i, item in enumerate(full_catchments):
                own_geom = geoms[i]
                own_area = own_geom.area() if own_geom is not None else 0.0
                upstream = [
                    j for j, other in enumerate(full_catchments)
                    if j != i and own_geom is not None
                    and own_geom.intersects(QgsGeometry.fromPointXY(other['point']).buffer(point_tolerance, 4))
                ]
                if not upstream:
                    feedback.pushInfo(
                        f"  {item['safe_id']}: no upstream outlet detected (area "
                        f"{own_area:.0f} m²) — keeping full catchment."
                    )
                    results.append({'safe_id': item['safe_id'], 'output': item['output']})
                    continue

                feedback.pushInfo(
                    f"  {item['safe_id']}: removing overlap from "
                    f"{len(upstream)} upstream outlet(s) "
                    f"({', '.join(full_catchments[k]['safe_id'] for k in upstream)})..."
                )
                if len(upstream) == 1:
                    union_output = full_catchments[upstream[0]]['output']
                else:
                    layers = [full_catchments[k]['output'] for k in upstream]
                    merged_up = processing.run(
                        'native:mergevectorlayers',
                        {'LAYERS': layers, 'CRS': crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                        context=context, feedback=_QuietFeedback(feedback), is_child_algorithm=True,
                    )
                    dissolved_up = processing.run(
                        'native:dissolve',
                        {'INPUT': merged_up['OUTPUT'], 'FIELD': [], 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                        context=context, feedback=_QuietFeedback(feedback), is_child_algorithm=True,
                    )
                    union_output = dissolved_up['OUTPUT']

                diff = processing.run(
                    'native:difference',
                    {'INPUT': item['output'], 'OVERLAY': union_output, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                    context=context, feedback=_QuietFeedback(feedback), is_child_algorithm=True,
                )
                fixed = processing.run(
                    'native:fixgeometries',
                    {'INPUT': diff['OUTPUT'], 'METHOD': 1, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                    context=context, feedback=_QuietFeedback(feedback), is_child_algorithm=True,
                )
                fixed_out = fixed['OUTPUT']
                fixed_for_area = QgsVectorLayer(fixed_out, '_tmp', 'ogr') if isinstance(fixed_out, str) else fixed_out
                split_area = sum(f.geometry().area() for f in fixed_for_area.getFeatures())
                feedback.pushInfo(
                    f"    {item['safe_id']}: area {own_area:.0f} m² -> {split_area:.0f} m² after split."
                )
                results.append({'safe_id': item['safe_id'], 'output': fixed_out})

            return results

        split_results = _compute_split_catchments() if split_outputs else None

        if separate_outputs:
            if not saved_ids:
                raise QgsProcessingException(
                    "No catchments were successfully delineated. "
                    "Check that outlet points fall within the drainage raster extent "
                    "and on valid flow cells."
                )
            if split_results:
                for r in split_results:
                    split_path = os.path.join(out_folder, f"{r['safe_id']}_Catchment_Split.shp")
                    processing.run(
                        'native:fixgeometries',
                        {'INPUT': r['output'], 'METHOD': 1, 'OUTPUT': split_path},
                        context=context, feedback=_QuietFeedback(feedback), is_child_algorithm=True,
                    )
                    feedback.pushInfo(f"    Catchment (split) -> {split_path}")
            feedback.pushInfo(
                f"--- Outlet Catchment Generator complete — "
                f"{len(saved_ids)} outlet(s) written to:\n  {out_folder} ---"
            )
            return {}

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

        if split_results:
            mfb.setCurrentStep(step); step += 1
            feedback.pushInfo(f"Merging {len(split_results)} split outlet catchment(s)...")
            split_layers = [r['output'] for r in split_results]
            merged_split = processing.run(
                'native:mergevectorlayers',
                {'LAYERS': split_layers, 'CRS': crs, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=mfb, is_child_algorithm=True,
            )
            fixed_split = processing.run(
                'native:fixgeometries',
                {'INPUT': merged_split['OUTPUT'], 'METHOD': 1, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                context=context, feedback=mfb, is_child_algorithm=True,
            )

            out_split_dest = parameters.get(self.OUTPUT_CATCHMENTS_SPLIT)
            if out_split_dest is None:
                out_split_dest = QgsProcessing.TEMPORARY_OUTPUT
            if not isinstance(out_split_dest, QgsProcessingOutputLayerDefinition):
                out_split_dest = QgsProcessingOutputLayerDefinition(str(out_split_dest))
            out_split_dest.destinationName = 'Outlet Catchments (Split)'

            fixed_split_out = fixed_split['OUTPUT']
            fixed_split_lyr = (QgsVectorLayer(fixed_split_out, '_tmp_split', 'ogr')
                                if isinstance(fixed_split_out, str) else fixed_split_out)
            cols_to_drop = [
                f.name() for f in fixed_split_lyr.fields()
                if f.name().lower() in ('dn', 'layer', 'path', 'fid')
            ]
            if cols_to_drop:
                cleaned_split = processing.run(
                    'native:deletecolumn',
                    {'INPUT': fixed_split_out, 'COLUMN': cols_to_drop, 'OUTPUT': out_split_dest},
                    context=context, feedback=mfb, is_child_algorithm=True,
                )
                result[self.OUTPUT_CATCHMENTS_SPLIT] = cleaned_split['OUTPUT']
            else:
                passthrough_split = processing.run(
                    'native:fixgeometries',
                    {'INPUT': fixed_split_out, 'METHOD': 1, 'OUTPUT': out_split_dest},
                    context=context, feedback=mfb, is_child_algorithm=True,
                )
                result[self.OUTPUT_CATCHMENTS_SPLIT] = passthrough_split['OUTPUT']

        feedback.pushInfo("--- Outlet Catchment Generator complete ---")
        return result
