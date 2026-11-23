# -*- coding: utf-8 -*-
"""
Subcatchment Slope Calculation — equal-area method (Auckland TP108).

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V02"
__status__     = "Stable"
_LINKED_PROMPT = "SCH_SubcatchSlopeCalculationPrompt_GM.txt"

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingException,
    QgsVectorDataProvider,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsSpatialIndex,
    QgsFeatureRequest,
)
import math
import os

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class CalculateSlopeForToC(QgsProcessingAlgorithm):

    INPUT_CATCHMENTS   = 'INPUT_CATCHMENTS'
    CATCHMENT_ID_FIELD = 'CATCHMENT_ID_FIELD'
    INPUT_FLOWPATHS    = 'INPUT_FLOWPATHS'
    FLOWPATH_ID_FIELD  = 'FLOWPATH_ID_FIELD'
    INPUT_DEM          = 'INPUT_DEM'
    SAMPLING_METHOD    = 'SAMPLING_METHOD'
    DENSIFY_INTERVAL   = 'DENSIFY_INTERVAL'
    OUTPUT_MODE        = 'OUTPUT_MODE'
    OUTPUT_CATCHMENTS  = 'OUTPUT_CATCHMENTS'
    OUTPUT_PLOT_FOLDER = 'OUTPUT_PLOT_FOLDER'

    SAMPLING_OPTIONS    = ['User-specified interval', 'At flow path vertices only']
    OUTPUT_MODE_OPTIONS = ['New output file', 'Edit input catchment layer in-place']

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculateSlopeForToC()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'sch_slope_calculation_v02'

    def displayName(self):
        return self.tr('Subcatchment Slope Calculation V02')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Calculates catchment slope and area using the TP108 equal-area method.\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Inputs:\n"
            " • Subcatchment layer (polygon) with unique ID field\n"
            " • Longest flow path layer (line) with unique ID field\n"
            " • DEM raster for elevation sampling\n"
            " • DEM Sampling Method: 'User-specified interval' or 'At flow path vertices only'\n"
            " • Sampling interval (m) — used only with 'User-specified interval'\n"
            " • Output Mode: 'New output file' or 'Edit input catchment layer in-place'\n\n"
            "Outputs:\n"
            " • Catchment polygon layer with Sc_TP108 (slope) and Catch_L_m (flow path length)\n"
            " • Optional elevation profile PNG plots\n\n"
            "Pre-run validation:\n"
            " • CRS alignment across all input layers is checked\n"
            " • Subcatchment geometries are validated\n"
            " • Subcatchment layer is checked for overlapping polygons\n\n"
            "Important: All input layers must share the same CRS. "
            "DEM must fully cover all flow paths.\n\n"
            "Length used: Catch_L_m — and therefore Sc_TP108 and the Time of "
            "Concentration tool that reads Catch_L_m downstream — is taken from the flow "
            "path's D8_LENGTH_M field (the hydrological D8 length) when present, not the "
            "output line's own drawn length, so a simplified or stream-extended flow path "
            "geometry doesn't change the slope/ToC result. Older flow path layers without "
            "D8_LENGTH_M fall back to the geometry length, as before (V01 behaviour)."
        )

    def help(self):
        return self.tr(
            "<html><body>"
            "<h2>Slope Calculation for Time of Concentration (TP108)</h2>"
            "<p><b>Author:</b> Dipendra Magaju | <b>Tested On:</b> QGIS 3.44.2</p>"
            "<h3>Purpose</h3>"
            "<p>Calculates the equal-area slope (Sc) for catchments based on TP108 guideline "
            "(Auckland) for computing Time of Concentration (ToC).</p>"
            "<h3>Important Notes</h3>"
            "<ul>"
            "<li>All input layers must have the same CRS.</li>"
            "<li>The DEM must fully cover all flow paths.</li>"
            "<li>Subcatchment geometries must be valid — invalid features halt the tool.</li>"
            "<li>Subcatchment polygons must not overlap.</li>"
            "<li>In-place edits cannot be undone — back up data first.</li>"
            "<li>Optional plot generation requires Matplotlib.</li>"
            "</ul>"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_FLOWPATHS, self.tr('Longest Flow Path Layer'),
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(QgsProcessingParameterField(
            self.FLOWPATH_ID_FIELD, self.tr('Flow Path Unique ID Field'),
            parentLayerParameterName=self.INPUT_FLOWPATHS,
            type=QgsProcessingParameterField.Any
        ))
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_CATCHMENTS, self.tr('Subcatchment Layer'),
                types=[QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(QgsProcessingParameterField(
            self.CATCHMENT_ID_FIELD, self.tr('Subcatchment Unique ID Field'),
            parentLayerParameterName=self.INPUT_CATCHMENTS,
            type=QgsProcessingParameterField.Any
        ))
        self.addParameter(
            QgsProcessingParameterRasterLayer(self.INPUT_DEM, self.tr('DEM Layer'))
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SAMPLING_METHOD, self.tr('DEM Sampling Method'),
                options=self.SAMPLING_OPTIONS, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DENSIFY_INTERVAL,
                self.tr('DEM Sampling Interval along Flow Path (meters)'),
                type=QgsProcessingParameterNumber.Double, defaultValue=10.0,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_MODE, self.tr('Output Mode'),
                options=self.OUTPUT_MODE_OPTIONS, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_CATCHMENTS, self.tr('Output Catchments with Slope'),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_PLOT_FOLDER, self.tr('Folder for Output Plots (Optional)'),
                optional=True
            )
        )

    def _check_catchment_validity(self, layer, id_field, feedback):
        feedback.pushInfo("Checking subcatchment geometry validity...")
        invalid_ids = []
        for feat in layer.getFeatures():
            geom = feat.geometry()
            fid = str(feat[id_field]) if id_field else str(feat.id())
            if geom is None or geom.isEmpty() or not geom.isGeosValid():
                invalid_ids.append(fid)
        if invalid_ids:
            raise QgsProcessingException(
                f"Invalid or empty geometries found in the subcatchment layer for "
                f"{len(invalid_ids)} feature(s):\n"
                f"  IDs: {', '.join(invalid_ids)}\n"
                f"Please fix these geometries before running the tool."
            )
        feedback.pushInfo("Geometry validity check passed.")

    def _check_layer_overlaps(self, layer, id_field, feedback):
        feedback.pushInfo("Checking subcatchment layer for overlapping polygons...")
        index    = QgsSpatialIndex(layer.getFeatures())
        features = {f.id(): f for f in layer.getFeatures()}
        overlap_pairs = []

        for fid, feat in features.items():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            for cid in index.intersects(geom.boundingBox()):
                if cid <= fid:
                    continue
                candidate = features[cid]
                cgeom = candidate.geometry()
                if cgeom is None or cgeom.isEmpty():
                    continue
                intersection = geom.intersection(cgeom)
                if intersection and not intersection.isEmpty():
                    poly_part = intersection.convertToType(QgsWkbTypes.PolygonGeometry, False)
                    if poly_part and not poly_part.isEmpty() and poly_part.area() > 0.001:
                        id_a = str(feat[id_field])
                        id_b = str(candidate[id_field])
                        overlap_pairs.append((id_a, id_b, poly_part.area()))

        if overlap_pairs:
            msg = (f"Overlapping polygons detected in the subcatchment layer "
                   f"({len(overlap_pairs)} pair(s)):\n")
            for id_a, id_b, area in overlap_pairs:
                msg += f"  - '{id_a}' overlaps '{id_b}' (overlap area ≈ {area:.4f} m²)\n"
            msg += "Please resolve overlaps before running the tool."
            raise QgsProcessingException(msg)
        feedback.pushInfo("Overlap check passed.")

    def processAlgorithm(self, parameters, context, feedback):
        catchments         = self.parameterAsVectorLayer(parameters, self.INPUT_CATCHMENTS, context)
        catchment_id_field = self.parameterAsString(parameters, self.CATCHMENT_ID_FIELD, context)
        flowpaths          = self.parameterAsSource(parameters, self.INPUT_FLOWPATHS, context)
        flowpath_id_field  = self.parameterAsString(parameters, self.FLOWPATH_ID_FIELD, context)
        dem                = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        sampling_method    = self.parameterAsEnum(parameters, self.SAMPLING_METHOD, context)
        interval           = self.parameterAsDouble(parameters, self.DENSIFY_INTERVAL, context)
        output_mode        = self.parameterAsInt(parameters, self.OUTPUT_MODE, context)
        plot_folder        = self.parameterAsString(parameters, self.OUTPUT_PLOT_FOLDER, context)

        if sampling_method == 0 and interval <= 0:
            raise QgsProcessingException(
                "DEM Sampling Interval must be greater than 0 when using 'User-specified interval' mode."
            )

        if plot_folder and not MATPLOTLIB_AVAILABLE:
            raise QgsProcessingException(
                "Matplotlib is required for plotting but is not installed."
            )

        if output_mode == 1:
            existing_fields = [f.name() for f in catchments.fields()]
            conflicts = [n for n in ('Sc_TP108', 'Catch_L_m') if n in existing_fields]
            if conflicts:
                raise QgsProcessingException(
                    f"Field(s) {conflicts} already exist in the input catchment layer. "
                    f"Remove or rename them before running in-place mode."
                )
            provider = catchments.dataProvider()
            if not (provider.capabilities() & QgsVectorDataProvider.AddAttributes):
                raise QgsProcessingException(
                    "The input catchment layer's data provider does not support adding attributes in-place."
                )

        feedback.pushInfo("Validating Coordinate Reference Systems (CRS)...")
        crs_catchment = catchments.crs()
        crs_flowpath  = flowpaths.sourceCrs()
        crs_dem       = dem.crs()
        if not (crs_catchment == crs_flowpath == crs_dem):
            raise QgsProcessingException(
                f"CRS Mismatch! All layers must have the same CRS.\n"
                f" - Catchments: {crs_catchment.authid()}\n"
                f" - Flow Paths: {crs_flowpath.authid()}\n"
                f" - DEM: {crs_dem.authid()}"
            )

        self._check_catchment_validity(catchments, catchment_id_field, feedback)
        self._check_layer_overlaps(catchments, catchment_id_field, feedback)

        feedback.pushInfo("Indexing catchment layer...")
        catchment_index = QgsSpatialIndex(catchments.getFeatures())

        if output_mode == 0:
            dest_fields = QgsFields()
            dest_fields.append(QgsField(catchment_id_field, QVariant.String, len=100))
            dest_fields.append(QgsField(flowpath_id_field,  QVariant.String, len=100))
            dest_fields.append(QgsField('Catch_L_m', QVariant.Double, 'double', 10, 3))
            dest_fields.append(QgsField('Sc_TP108',  QVariant.Double, 'double', 10, 6))

            (sink, dest_id) = self.parameterAsSink(
                parameters, self.OUTPUT_CATCHMENTS, context,
                dest_fields, catchments.wkbType(), catchments.crs()
            )
            if sink is None:
                raise QgsProcessingException(
                    'Could not create output layer. '
                    'Specify an output file path or switch Output Mode to "Edit input catchment layer in-place".'
                )
        else:
            sink      = None
            dest_id   = None
            collected = {}

        # Tolerance for the boundary-point fallback: half a DEM cell diagonal.
        dem_cell_w = dem.rasterUnitsPerPixelX()
        dem_cell_h = dem.rasterUnitsPerPixelY()
        cell_tol   = math.hypot(dem_cell_w, dem_cell_h) / 2.0

        processed_catchment_fids = set()
        total        = 100.0 / flowpaths.featureCount() if flowpaths.featureCount() > 0 else 0
        dem_provider = dem.dataProvider()

        for current, flowpath_feat in enumerate(flowpaths.getFeatures()):
            if feedback.isCanceled():
                break

            flowpath_id   = str(flowpath_feat[flowpath_id_field])
            flowpath_geom = flowpath_feat.geometry()

            if not flowpath_geom.isGeosValid():
                feedback.pushWarning(f"Invalid geometry for flow path ID '{flowpath_id}'. Skipping.")
                continue

            verts    = list(flowpath_geom.vertices())
            first_pt = QgsPointXY(verts[0])
            last_pt  = QgsPointXY(verts[-1])

            def _catchment_for_point(pt):
                pt_geom = QgsGeometry.fromPointXY(pt)
                for fid in catchment_index.intersects(pt_geom.boundingBox()):
                    c_feat = next(catchments.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
                    if c_feat and c_feat.geometry().contains(pt_geom):
                        return c_feat
                # Fallback: point lies exactly on a boundary (common with raster cell centres).
                buf = pt_geom.buffer(cell_tol, 5)
                for fid in catchment_index.intersects(buf.boundingBox()):
                    c_feat = next(catchments.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
                    if c_feat and c_feat.geometry().intersects(buf):
                        return c_feat
                return None

            cat_first = _catchment_for_point(first_pt)
            cat_last  = _catchment_for_point(last_pt)

            parent_catchment = None
            if cat_first is not None and cat_last is not None:
                if cat_first.id() != cat_last.id():
                    feedback.pushWarning(
                        f"Flow path '{flowpath_id}': outlet and upstream endpoints belong to "
                        f"different catchments — using outlet-end catchment "
                        f"('{cat_first[catchment_id_field]}')."
                    )
                parent_catchment = cat_first
            elif cat_first is not None:
                parent_catchment = cat_first
            elif cat_last is not None:
                parent_catchment = cat_last
                feedback.pushWarning(
                    f"Flow path '{flowpath_id}': outlet point not found in any catchment; "
                    f"using upstream-end catchment as fallback."
                )

            if parent_catchment is None:
                feedback.pushWarning(
                    f"No valid containing catchment found for flow path ID '{flowpath_id}'. Skipping."
                )
                continue

            catchment_id = str(parent_catchment[catchment_id_field])

            # Prefer the flow path's hydrological length (D8_LENGTH_M, the
            # summed D8 raster step length) over the output geometry's own
            # length, which can be shorter when a simplification tolerance
            # was applied in Longest Flow Path. Falls back to the geometry
            # length for flow-path layers from before that field existed.
            geom_length  = flowpath_geom.length()
            total_length = geom_length
            try:
                field_names = [f.name() for f in flowpath_feat.fields()]
                if 'D8_LENGTH_M' in field_names:
                    d8_len = float(flowpath_feat['D8_LENGTH_M'])
                    if d8_len > 0:
                        total_length = d8_len
            except (TypeError, ValueError):
                pass
            # Scales a distance measured on the hydrological-length basis
            # (0..total_length) to the equivalent distance along the actual
            # (possibly shorter) output geometry, so elevations are still
            # sampled from real points on that geometry.
            to_geom_dist = (geom_length / total_length) if total_length > 0 else 1.0

            profile = []
            if sampling_method == 0:
                for distance in range(0, int(total_length) + 1, int(interval)):
                    point = flowpath_geom.interpolate(distance * to_geom_dist).asPoint()
                    elevation, res = dem_provider.sample(point, 1)
                    if res:
                        profile.append({'dist': float(distance), 'elev': float(elevation)})
                last_point = flowpath_geom.interpolate(geom_length).asPoint()
                last_elev, res = dem_provider.sample(last_point, 1)
                if res and (not profile or profile[-1]['dist'] < total_length):
                    profile.append({'dist': total_length, 'elev': float(last_elev)})
            else:
                cum_dist = 0.0
                prev_pt  = None
                for vtx in flowpath_geom.vertices():
                    if prev_pt is not None:
                        cum_dist += prev_pt.distance(vtx)
                    elevation, res = dem_provider.sample(QgsPointXY(vtx), 1)
                    if res:
                        profile.append({'dist': cum_dist / to_geom_dist, 'elev': float(elevation)})
                    prev_pt = vtx

            # The TP108 equal-area formula expects the profile to run
            # outlet→headwater (ascending: profile[0] is the lowest point).
            # The Longest Flow Path tool traces headwater→outlet (descending),
            # so reverse the profile when the first sampled elevation is higher
            # than the last.
            if len(profile) >= 2 and profile[0]['elev'] > profile[-1]['elev']:
                max_d   = profile[-1]['dist']
                profile = [{'dist': max_d - p['dist'], 'elev': p['elev']}
                           for p in reversed(profile)]

            raw_profile    = list(profile)
            filled_profile = self._fill_profile_sinks(profile) if len(profile) >= 3 else list(profile)
            raw_slope      = self._compute_slope(raw_profile,    total_length)
            filled_slope   = self._compute_slope(filled_profile, total_length)

            if raw_slope is not None and raw_slope >= 0:
                out_slope            = raw_slope
                sink_correction_used = False
            elif filled_slope is not None and filled_slope >= 0:
                out_slope            = filled_slope
                sink_correction_used = True
                feedback.pushWarning(
                    f"Flow path '{flowpath_id}' (catchment '{catchment_id}'): raw slope negative "
                    f"— sink-filled slope used instead (Sc_TP108 = {filled_slope:.4f})."
                )
            else:
                feedback.pushWarning(
                    f"Flow path '{flowpath_id}' (catchment '{catchment_id}'): both slopes invalid. Skipping."
                )
                feedback.setProgress(int((current + 1) * total))
                continue

            if plot_folder:
                self.generate_plot(catchment_id, filled_profile, raw_profile,
                                   total_length, filled_slope, raw_slope,
                                   plot_folder, feedback, sink_correction_used)

            if output_mode == 0:
                new_feat = QgsFeature()
                new_feat.setGeometry(parent_catchment.geometry())
                new_feat.setAttributes([catchment_id, flowpath_id, total_length, out_slope])
                sink.addFeature(new_feat, QgsFeatureSink.FastInsert)
                processed_catchment_fids.add(parent_catchment.id())
            else:
                collected[parent_catchment.id()] = (total_length, out_slope)
                processed_catchment_fids.add(parent_catchment.id())

            feedback.setProgress(int((current + 1) * total))

        unmatched_count = 0
        for cat_feat in catchments.getFeatures():
            if cat_feat.id() not in processed_catchment_fids:
                cat_id = str(cat_feat[catchment_id_field])
                if output_mode == 0:
                    null_feat = QgsFeature()
                    null_feat.setGeometry(cat_feat.geometry())
                    null_feat.setAttributes([cat_id, None, None, None])
                    sink.addFeature(null_feat, QgsFeatureSink.FastInsert)
                unmatched_count += 1

        if unmatched_count:
            feedback.pushInfo(
                f"{unmatched_count} subcatchment(s) had no matching flow path — "
                f"Catch_L_m and Sc_TP108 recorded as NULL."
            )

        if output_mode == 1:
            provider = catchments.dataProvider()
            provider.addAttributes([
                QgsField('Catch_L_m', QVariant.Double, 'double', 10, 3),
                QgsField('Sc_TP108',  QVariant.Double, 'double', 10, 6),
            ])
            catchments.updateFields()

            idx_len   = catchments.fields().indexFromName('Catch_L_m')
            idx_slope = catchments.fields().indexFromName('Sc_TP108')

            attr_map = {
                feat_id: {idx_len: length, idx_slope: slope}
                for feat_id, (length, slope) in collected.items()
            }
            provider.changeAttributeValues(attr_map)
            catchments.updateFields()

            feedback.pushInfo(
                f"Done. Sc_TP108 and Catch_L_m added in-place to '{catchments.name()}' "
                f"for {len(collected)} catchment(s)."
            )
            return {}

        return {self.OUTPUT_CATCHMENTS: dest_id}

    def generate_plot(self, c_id, filled_profile, raw_profile, length,
                      filled_slope, raw_slope, folder, feedback,
                      sink_correction_used=False):
        try:
            outlet_elev  = raw_profile[0]['elev']
            raw_dists    = [p['dist'] for p in raw_profile]
            raw_elevs    = [p['elev'] for p in raw_profile]
            filled_dists = [p['dist'] for p in filled_profile]
            filled_elevs = [p['elev'] for p in filled_profile]

            sinks_present = any(
                abs(r['elev'] - f['elev']) > 0.001
                for r, f in zip(raw_profile, filled_profile)
            )
            both_negative = (
                (raw_slope is None or raw_slope < 0) and
                (filled_slope is None or filled_slope < 0)
            )

            plt.figure(figsize=(10, 6))
            raw_color = 'lightgrey' if sinks_present else 'black'
            raw_label = 'Raw DEM profile (unfilled)' if sinks_present else 'Elevation profile'
            plt.plot(raw_dists, raw_elevs, color=raw_color, linewidth=1.5,
                     linestyle='--', label=raw_label)

            if sinks_present:
                plt.plot(filled_dists, filled_elevs, color='black', linewidth=2,
                         label='Elevation profile (sink-filled)')

            if raw_slope is not None:
                saved_tag   = '' if sink_correction_used else '  ← saved'
                raw_slope_y = [outlet_elev + d * raw_slope for d in raw_dists]
                plt.plot(raw_dists, raw_slope_y, color='steelblue', linewidth=1.5,
                         linestyle=':', label=f'Raw slope  Sc = {raw_slope:.4f}{saved_tag}')

            if filled_slope is not None:
                saved_tag      = '  ← saved' if sink_correction_used else ''
                filled_slope_y = [outlet_elev + d * filled_slope for d in filled_dists]
                plt.plot(filled_dists, filled_slope_y, color='red', linewidth=1.5,
                         linestyle='--', label=f'Filled slope  Sc = {filled_slope:.4f}{saved_tag}')

            title = f'Elevation Profile — Catchment: {c_id}'
            if both_negative:
                title += '\n⚠  Both slopes negative — feature excluded from output'
            plt.title(title)
            plt.xlabel('Distance along Flow Path (m)')
            plt.ylabel('Elevation (m)')
            plt.grid(True)
            plt.legend()
            plt.xlim(0, length)

            plot_path = os.path.join(folder, f'slope_profile_{c_id}.png')
            plt.savefig(plot_path, dpi=150, bbox_inches='tight')
            plt.close()
        except Exception as e:
            feedback.pushWarning(f"Could not generate plot for catchment '{c_id}'. Error: {e}")

    def _compute_slope(self, profile, total_length):
        if len(profile) < 2 or total_length <= 0:
            return None
        outlet_elevation = profile[0]['elev']
        area = 0.0
        for i in range(1, len(profile)):
            p1, p2 = profile[i - 1], profile[i]
            h1 = p1['elev'] - outlet_elevation
            h2 = p2['elev'] - outlet_elevation
            area += (h1 + h2) / 2.0 * (p2['dist'] - p1['dist'])
        return (2.0 * area) / (total_length ** 2)

    def _fill_profile_sinks(self, profile):
        n     = len(profile)
        elevs = [p['elev'] for p in profile]

        left_max = [elevs[0]] * n
        for i in range(1, n):
            left_max[i] = max(left_max[i - 1], elevs[i])

        right_max = [elevs[-1]] * n
        for i in range(n - 2, -1, -1):
            right_max[i] = max(right_max[i + 1], elevs[i])

        filled_elevs = [
            max(e, min(left_max[i], right_max[i]))
            for i, e in enumerate(elevs)
        ]
        return [{'dist': p['dist'], 'elev': e} for p, e in zip(profile, filled_elevs)]
