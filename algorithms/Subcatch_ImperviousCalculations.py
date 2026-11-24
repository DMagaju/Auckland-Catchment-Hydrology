# -*- coding: utf-8 -*-

"""
QGIS Processing script to calculate impervious area and soil breakdown for subcatchments.

This script intersects a subcatchment layer with a land use layer, calculates the impervious
area percentage for each subcatchment using an impervious percentage field from the land use
layer, and optionally breaks down the pervious area by soil type.

---

Author: Dipendra Magaju

Tested On: QGIS 3.44.2

Short description: Calculates impervious area and optionally breaks down pervious area by soil type for each subcatchment.

Inputs / Outputs:
  Inputs — Vector polygon (subcatchments), Vector polygon (land use with impervious % field), Vector polygon (soil, optional)
  Outputs — Vector polygon with impervious area percentages and soil breakdown, optional log file

Long description / Help:
  This tool intersects the catchment and land use polygons, and uses the impervious percentage
  field from the land use layer to calculate the impervious area percentage for each subcatchment.
  It computes area-weighted statistics and provides hydrological parameters. Optionally, it breaks
  down the pervious area by soil type, providing percentages of each soil group relative to the
  total catchment area. Includes comprehensive validation and optional logging.
  
  Important prerequisites:
  - All input layers must share the same Coordinate Reference System (CRS)
  - Land use layer must have an impervious percentage field (numeric, 0-100)
  - Input geometries must be valid (no self-intersections or gaps)

To use this script:
1. Save it as a .py file in your QGIS processing scripts directory.
   (e.g., C:/Users/YourUser/AppData/Roaming/QGIS/QGIS3/profiles/default/processing/scripts)
2. In QGIS, open the Processing Toolbox.
3. Right-click on 'Scripts' and select 'Refresh scripts'.
4. The tool will appear under the 'SubCatchment Assessment' group.
"""

__author__  = "Dipendra Magaju"
__version__ = "V01"
__status__  = "Beta"


# Imports
import collections
import os
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingException,
    QgsFields,
    QgsField,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsSpatialIndex,
    QgsWkbTypes,
    QgsProcessingUtils
)
import processing

class CalculateImperviousAreas(QgsProcessingAlgorithm):
    """
    This algorithm intersects a catchment layer with a landuse layer, calculates
    the impervious area for each catchment based on a CSV lookup file, and outputs
    a new layer with the results. It can also optionally break down the pervious area by soil type.
    """
    # --- Constants for Input and Output names ---
    INPUT_CATCHMENT = 'INPUT_CATCHMENT'
    INPUT_LANDUSE = 'INPUT_LANDUSE'
    INPUT_SOIL = 'INPUT_SOIL'
    INPUT_CATCHMENT_ID_FIELD = 'INPUT_CATCHMENT_ID_FIELD'
    INPUT_IMPERVIOUS_FIELD = 'INPUT_IMPERVIOUS_FIELD'
    INPUT_LANDUSE_TYPE_FIELD = 'INPUT_LANDUSE_TYPE_FIELD'
    INPUT_SOIL_GROUP_FIELD = 'INPUT_SOIL_GROUP_FIELD'
    SAVE_LOG = 'SAVE_LOG'
    LOG_FILE_PATH = 'LOG_FILE_PATH'
    PERFORM_SOIL_ASSESSMENT = 'PERFORM_SOIL_ASSESSMENT'
    CHECK_OVERLAPS = 'CHECK_OVERLAPS'

    USE_EXISTING_IMP = 'USE_EXISTING_IMP'
    INPUT_EXISTING_IMP = 'INPUT_EXISTING_IMP'

    PERFORM_CN_CALCULATION = 'PERFORM_CN_CALCULATION'
    CN_IMP             = 'CN_IMP'
    CN_PERV_METHOD     = 'CN_PERV_METHOD'
    CN_PERV_USER_VALUE  = 'CN_PERV_USER_VALUE'
    CN_SOIL_FIELD       = 'CN_SOIL_FIELD'
    CN_DEFAULT_VALUE    = 'CN_DEFAULT_VALUE'

    CN_PERV_OPTIONS = ['User-supplied value',
                       'Calculate from Soil/Geology layer (weighted)']


    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculateImperviousAreas()

    def flags(self):
        # Disable background threading — sub-algorithm calls and QgsSpatialIndex
        # operations must run on the main thread to avoid GIL/access-violation crashes.
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'calculate_impervious_area'

    def displayName(self):
        return self.tr('Subcatchment Impervious Calculations')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Calculates impervious area and soil breakdown for subcatchments.\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Short description:\n"
            "Intersects subcatchment and land use layers, uses impervious percentage field from land use layer, "
            "and optionally breaks down pervious area by soil type.\n\n"
            "Inputs:\n"
            " • Subcatchment polygon layer (with unique ID field)\n"
            " • Land use polygon layer (with impervious percentage field)\n"
            " • Impervious Percentage field from land use layer (numeric, 0-100%)\n"
            " • Optional: Soil group polygon layer (with soil group field)\n\n"
            "Outputs:\n"
            " • New catchment polygon layer with:"
            "   - Subcatchment ID, Total Impervious Area (%), Impervious Area (ha)\n"
            "   - Soil breakdown (if enabled): Soil Group and % relative to total area\n"
            " • Optional: Detailed log file with calculation results\n\n"
            "Important: All input layers must share the same CRS. Land use layer must have impervious % field."
        )

    def help(self):
        """Provide detailed help text and examples for the tool."""
        return self.tr(
            "<html><body>\n"
            "<h2>Calculate Impervious Area and Soil Breakdown</h2>\n"
            "<p><b>Author:</b> Dipendra Magaju</p>\n"
            "<p><b>Tested On:</b> QGIS 3.44.2</p>\n\n"
            "<h3>Purpose</h3>\n"
            "<p>This tool calculates the impervious area percentage for subcatchments based on land use classification. "
            "It uses the impervious percentage field from the land use layer to compute area-weighted statistics, and "
            "optionally provides a breakdown of pervious areas by soil type. This is essential for hydrological modeling "
            "and stormwater management design.</p>\n\n"
            "<h3>Methodology</h3>\n"
            "<p>The tool:</p>\n"
            "<ol>\n"
            "<li>Validates all input layers share the same Coordinate Reference System (CRS)</li>\n"
            "<li>Checks that the impervious percentage field is numeric and contains valid values</li>\n"
            "<li>Intersects subcatchment and land use polygons</li>\n"
            "<li>Uses impervious percentages directly from the land use layer field</li>\n"
            "<li>Calculates area-weighted impervious percentage per subcatchment</li>\n"
            "<li>Computes total impervious area in hectares</li>\n"
            "<li>Optionally breaks down pervious area by soil type with percentages</li>\n"
            "<li>Generates detailed log file with calculation results and statistics</li>\n"
            "</ol>\n\n"
            "<h3>Input Requirements</h3>\n"
            "<ul>\n"
            "<li><b>Subcatchment Layer:</b> Polygon features with a unique ID field</li>\n"
            "<li><b>AUP Land Use Layer:</b> Polygon features with numeric impervious percentage field (0-100)</li>\n"
            "<li><b>Impervious Percentage Field:</b> Numeric field in land use layer containing impervious % values</li>\n"
            "<li><b>Soil Group Layer (Optional):</b> Polygon features with soil group identifier field</li>\n"
            "</ul>\n\n"
            "<h3>Output</h3>\n"
            "<ul>\n"
            "<li><b>Subcatchment_ID:</b> Unique identifier for each subcatchment</li>\n"
            "<li><b>Impervious_Pct:</b> Total impervious area as percentage of subcatchment area</li>\n"
            "<li><b>Impervious_Area_ha:</b> Total impervious area in hectares</li>\n"
            "<li><b>Pervious_Area_ha:</b> Total pervious area in hectares</li>\n"
            "<li><b>Total_Area_ha:</b> Total subcatchment area in hectares</li>\n"
            "<li><b>Soil_Group:</b> (if soil assessment enabled) Soil group classification</li>\n"
            "<li><b>Soil_Group_Pct:</b> (if soil assessment enabled) Percentage of soil group relative to total area</li>\n"
            "</ul>\n\n"
            "<h3>Important Notes and Restrictions</h3>\n"
            "<ul>\n"
            "<li>All input layers must have the same CRS—no automatic reprojection is performed</li>\n"
            "<li>The impervious percentage field must be numeric with values between 0 and 100</li>\n"
            "<li>Input geometries must be valid (no self-intersections, gaps, or invalid topologies)</li>\n"
            "<li>Soil assessment is optional; check the 'Perform Soil Breakdown' option to enable it</li>\n"
            "<li>Log file output is optional; uncheck 'Save Detailed Log File' to disable it</li>\n"
            "<li>The tool automatically detects CRS mismatches, geometry issues, and field validity before processing</li>\n"
            "</ul>\n"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        """
        Defines the input and output parameters for the algorithm.
        """
        # --- Input Layers ---
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_CATCHMENT,
                self.tr('Input Subcatchment Layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LANDUSE,
                self.tr('Input AUP Land Use Layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )

        # --- Input Fields ---
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_CATCHMENT_ID_FIELD,
                self.tr('Subcatchment Unique ID Field'),
                parentLayerParameterName=self.INPUT_CATCHMENT,
                type=QgsProcessingParameterField.Any
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_IMPERVIOUS_FIELD,
                self.tr('Impervious Percentage Field (%)'),
                parentLayerParameterName=self.INPUT_LANDUSE,
                type=QgsProcessingParameterField.Numeric,
                defaultValue='MPDIMPERV'
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_LANDUSE_TYPE_FIELD,
                self.tr('Land Use Classification Field (optional)'),
                parentLayerParameterName=self.INPUT_LANDUSE,
                type=QgsProcessingParameterField.Any,
                optional=True,
                defaultValue='ZONE_DESCR'
            )
        )

        # --- Optional Soil Assessment ---
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PERFORM_SOIL_ASSESSMENT,
                self.tr('Perform Soil Breakdown'),
                defaultValue=True
            )
        )
        param_soil_layer = QgsProcessingParameterFeatureSource(
            self.INPUT_SOIL,
            self.tr('Input Soil Group Layer'),
            [QgsProcessing.TypeVectorPolygon],
            optional=True
        )
        param_soil_layer.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_SOIL_ASSESSMENT}})
        self.addParameter(param_soil_layer)
        
        param_soil_field = QgsProcessingParameterField(
            self.INPUT_SOIL_GROUP_FIELD,
            self.tr('Soil Group Field'),
            parentLayerParameterName=self.INPUT_SOIL,
            type=QgsProcessingParameterField.String,
            optional=True,
            defaultValue='id'
        )
        param_soil_field.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_SOIL_ASSESSMENT}})
        self.addParameter(param_soil_field)


        # --- Optional Curve Number Calculation ---
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PERFORM_CN_CALCULATION,
                self.tr('Calculate Weighted Curve Number (CN)'),
                defaultValue=True
            )
        )
        param_cn_imp = QgsProcessingParameterNumber(
            self.CN_IMP,
            self.tr('Impervious Area CN'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=98,
            minValue=0,
            maxValue=100
        )
        param_cn_imp.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_CN_CALCULATION}})
        self.addParameter(param_cn_imp)

        param_cn_method = QgsProcessingParameterEnum(
            self.CN_PERV_METHOD,
            self.tr('Pervious Area CN Method'),
            options=self.CN_PERV_OPTIONS,
            defaultValue=0
        )
        param_cn_method.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_CN_CALCULATION}})
        self.addParameter(param_cn_method)

        param_cn_user = QgsProcessingParameterNumber(
            self.CN_PERV_USER_VALUE,
            self.tr('Pervious Area CN (user-supplied)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=74,
            minValue=0,
            maxValue=100,
            optional=True
        )
        param_cn_user.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_CN_CALCULATION}})
        self.addParameter(param_cn_user)

        param_cn_default = QgsProcessingParameterNumber(
            self.CN_DEFAULT_VALUE,
            self.tr('Default CN for areas with no soil data'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=74,
            minValue=0,
            maxValue=100,
            optional=True
        )
        param_cn_default.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_CN_CALCULATION}})
        self.addParameter(param_cn_default)

        param_cn_field = QgsProcessingParameterField(
            self.CN_SOIL_FIELD,
            self.tr('CN Attribute Field (from Soil/Geology layer)'),
            parentLayerParameterName=self.INPUT_SOIL,
            type=QgsProcessingParameterField.Numeric,
            optional=True,
            defaultValue='CN'
        )
        param_cn_field.setMetadata({'widget_wrapper': {'enabled_property': self.PERFORM_CN_CALCULATION}})
        self.addParameter(param_cn_field)

        # --- Optional Existing Impervious Extent ---
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_EXISTING_IMP,
                self.tr('Compare with Existing Impervious Extent Layer'),
                defaultValue=False
            )
        )
        param_existing_imp = QgsProcessingParameterFeatureSource(
            self.INPUT_EXISTING_IMP,
            self.tr('Existing Impervious Extent Layer'),
            [QgsProcessing.TypeVectorPolygon],
            optional=True
        )
        param_existing_imp.setMetadata({'widget_wrapper': {'enabled_property': self.USE_EXISTING_IMP}})
        self.addParameter(param_existing_imp)

        # --- Overlap Check (optional, slow for large layers) ---
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CHECK_OVERLAPS,
                self.tr('Check input layers for overlapping polygons (slow for large layers)'),
                defaultValue=False
            )
        )

        # --- Log File Options ---
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SAVE_LOG,
                self.tr('Save Results to Log File'),
                defaultValue=True
            )
        )
        param_log_file = QgsProcessingParameterFileDestination(
            self.LOG_FILE_PATH,
            self.tr('Output Log File'),
            fileFilter='Text files (*.txt)',
            optional=True
        )
        param_log_file.setMetadata({'widget_wrapper': {'enabled_property': self.SAVE_LOG}})
        self.addParameter(param_log_file)


    def _check_layer_overlaps(self, layer, layer_label, context, feedback):
        """
        Raises QgsProcessingException if the layer contains overlapping polygons.
        Uses the same spatial index + intersection approach as CheckOverlapTool
        so that both tools always agree on whether overlaps exist.
        """
        feedback.pushInfo(f"  Checking '{layer_label}' for overlapping polygons...")

        features = {
            f.id(): f for f in layer.getFeatures()
            if f.hasGeometry() and not f.geometry().isEmpty()
        }
        if not features:
            feedback.pushInfo(f"    '{layer_label}' — no features found.")
            return

        index = QgsSpatialIndex()
        for f in features.values():
            index.insertFeature(f)

        processed_pairs = set()
        total_overlap_area = 0.0

        for fid, feat in features.items():
            geom = feat.geometry()
            for cid in index.intersects(geom.boundingBox()):
                if cid == fid:
                    continue
                pair = (min(fid, cid), max(fid, cid))
                if pair in processed_pairs:
                    continue
                processed_pairs.add(pair)

                other_geom = features[cid].geometry()
                if not geom.intersects(other_geom):
                    continue

                intersection = geom.intersection(other_geom)
                if intersection.isEmpty():
                    continue

                poly_geom = intersection.convertToType(
                    QgsWkbTypes.PolygonGeometry, destMultipart=True
                )
                if poly_geom and not poly_geom.isEmpty():
                    total_overlap_area += poly_geom.area()

        if total_overlap_area >= 0.01:
            raise QgsProcessingException(
                f"Overlapping polygons detected in layer '{layer_label}'.\n"
                f"  Total overlap area: {total_overlap_area:.4f} m²\n"
                f"  Run the 'Check Layer Overlaps' tool on this layer to map the exact locations.\n"
                f"Please fix the overlapping polygons before running this tool."
            )
        feedback.pushInfo(f"    '{layer_label}' — no overlaps detected.")

    def processAlgorithm(self, parameters, context, feedback):
        """
        The main execution logic of the algorithm.
        """
        # --- 1. Retrieve inputs ---
        catchment_layer = self.parameterAsSource(parameters, self.INPUT_CATCHMENT, context)
        landuse_layer = self.parameterAsSource(parameters, self.INPUT_LANDUSE, context)
        subcatchment_id_field = self.parameterAsString(parameters, self.INPUT_CATCHMENT_ID_FIELD, context)
        impervious_field = self.parameterAsString(parameters, self.INPUT_IMPERVIOUS_FIELD, context)
        landuse_type_field = self.parameterAsString(parameters, self.INPUT_LANDUSE_TYPE_FIELD, context)
        save_log_file = self.parameterAsBool(parameters, self.SAVE_LOG, context)
        log_file_path = self.parameterAsFileOutput(parameters, self.LOG_FILE_PATH, context)
        perform_soil_assessment = self.parameterAsBool(parameters, self.PERFORM_SOIL_ASSESSMENT, context)
        check_overlaps = self.parameterAsBool(parameters, self.CHECK_OVERLAPS, context)

        # --- CN parameters ---
        perform_cn_calculation = self.parameterAsBool(parameters, self.PERFORM_CN_CALCULATION, context)
        cn_imp = self.parameterAsInt(parameters, self.CN_IMP, context) if perform_cn_calculation else 98
        cn_perv_method = self.parameterAsEnum(parameters, self.CN_PERV_METHOD, context) if perform_cn_calculation else 0
        cn_perv_user_value = self.parameterAsInt(parameters, self.CN_PERV_USER_VALUE, context) if perform_cn_calculation else 74
        cn_soil_field = self.parameterAsString(parameters, self.CN_SOIL_FIELD, context) if perform_cn_calculation else ''
        cn_default_value = self.parameterAsInt(parameters, self.CN_DEFAULT_VALUE, context) if perform_cn_calculation else 74

        # Determine if the soil layer is needed at all
        need_soil_layer = perform_soil_assessment or (perform_cn_calculation and cn_perv_method == 1)

        soil_layer = None
        soil_group_field = ''
        if need_soil_layer:
            soil_layer = self.parameterAsSource(parameters, self.INPUT_SOIL, context)
            if perform_soil_assessment:
                soil_group_field = self.parameterAsString(parameters, self.INPUT_SOIL_GROUP_FIELD, context)
                if not soil_layer or not soil_group_field:
                    raise QgsProcessingException("Soil breakdown was requested, but soil layer or field was not provided.")
            if perform_cn_calculation and cn_perv_method == 1:
                if not soil_layer:
                    raise QgsProcessingException("CN from soil layer method requires a soil layer (Input Soil Group Layer).")
                if not cn_soil_field:
                    raise QgsProcessingException("CN from soil layer method requires a CN attribute field to be selected.")


        # --- Existing impervious extent parameters ---
        use_existing_imp = self.parameterAsBool(parameters, self.USE_EXISTING_IMP, context)
        existing_imp_layer = None
        if use_existing_imp:
            existing_imp_layer = self.parameterAsSource(parameters, self.INPUT_EXISTING_IMP, context)
            if existing_imp_layer is None:
                raise QgsProcessingException("'Compare with Existing Impervious Extent' is enabled but no layer was provided.")

        if not all([catchment_layer, landuse_layer]):
            raise QgsProcessingException("Catchment or Land Use input layer is invalid.")

        # --- 2. Validate impervious field exists and is numeric ---
        feedback.pushInfo("Validating impervious percentage field...")
        impervious_field_idx = landuse_layer.fields().lookupField(impervious_field)
        if impervious_field_idx == -1:
            raise QgsProcessingException(f"Impervious percentage field '{impervious_field}' not found in land use layer.")
        
        field_def = landuse_layer.fields().at(impervious_field_idx)
        if not field_def.isNumeric():
            raise QgsProcessingException(f"Field '{impervious_field}' must be numeric (contains {field_def.typeName()}).")

        # --- 2b. Check input layers for overlapping polygons (optional) ---
        if check_overlaps:
            feedback.pushInfo("\nValidating input layers for polygon overlaps...")
            self._check_layer_overlaps(catchment_layer, 'Input Subcatchment Layer', context, feedback)
            self._check_layer_overlaps(landuse_layer,   'Input AUP Land Use Layer', context, feedback)
            if need_soil_layer and soil_layer:
                self._check_layer_overlaps(soil_layer,  'Input Soil Group Layer',   context, feedback)
            if use_existing_imp and existing_imp_layer:
                self._check_layer_overlaps(existing_imp_layer, 'Existing Impervious Extent Layer', context, feedback)
            feedback.pushInfo("  All layers passed overlap check.\n")
        else:
            feedback.pushInfo("Overlap check skipped (enable 'Check input layers for overlapping polygons' to run it).")

        # --- 3. Intersect land use and identify all unique types ---
        feedback.pushInfo("Performing land use intersection...")
        params_lu = {'INPUT': parameters[self.INPUT_CATCHMENT], 'OVERLAY': parameters[self.INPUT_LANDUSE], 'OUTPUT': 'memory:'}
        lu_result = processing.run("native:intersection", params_lu, context=context, feedback=feedback, is_child_algorithm=True)
        lu_intersection = QgsProcessingUtils.mapLayerFromString(lu_result['OUTPUT'], context)
        
        # Ensure the impervious field exists in the intersected layer
        try:
            impervious_field_idx_intersect = lu_intersection.fields().lookupField(impervious_field)
            if impervious_field_idx_intersect == -1:
                raise QgsProcessingException(f"Impervious field '{impervious_field}' not found in the intersected layer.")
        except Exception as e:
            raise QgsProcessingException(f"Could not find impervious field '{impervious_field}'. Error: {e}")
        
        # --- 4. Calculate Impervious Areas ---
        feedback.pushInfo("Calculating impervious areas...")
        impervious_details = collections.defaultdict(float)  # subcatchment_id -> impervious_sum (m²)
        # lu_breakdown: subcatchment_id -> {(lu_type, imp_pct): {'area_sqm': ..., 'imp_area_sqm': ...}}
        lu_breakdown = collections.defaultdict(lambda: collections.defaultdict(lambda: {'area_sqm': 0.0, 'imp_area_sqm': 0.0}))
        lu_intersection_total_area = collections.defaultdict(float)  # total LU-covered area per subcatchment (for gap reporting)
        for feature in lu_intersection.getFeatures():
            subcatchment_id = feature[subcatchment_id_field]
            area_sqm = feature.geometry().area()

            try:
                impervious_percentage = float(feature[impervious_field]) if feature[impervious_field] is not None else 0.0
                if impervious_percentage < 0 or impervious_percentage > 100:
                    feedback.pushWarning(f"Warning: Impervious percentage {impervious_percentage}% is outside 0-100 range, clamping to valid range.")
                    impervious_percentage = max(0, min(100, impervious_percentage))
            except (ValueError, TypeError):
                feedback.pushWarning(f"Warning: Could not read impervious percentage value, using 0%.")
                impervious_percentage = 0.0

            imp_area = area_sqm * impervious_percentage / 100
            impervious_details[subcatchment_id] += imp_area
            lu_type = str(feature[landuse_type_field]) if landuse_type_field and feature[landuse_type_field] is not None else 'N/A'
            lu_breakdown[subcatchment_id][(lu_type, impervious_percentage)]['area_sqm'] += area_sqm
            lu_breakdown[subcatchment_id][(lu_type, impervious_percentage)]['imp_area_sqm'] += imp_area
            lu_intersection_total_area[subcatchment_id] += area_sqm

        # --- 5. Existing Impervious Extent Intersection ---
        existing_imp_area_by_catch = {}  # subcatchment_id -> total impervious area (m²)
        if use_existing_imp and existing_imp_layer:
            feedback.pushInfo("Performing existing impervious extent intersection...")
            params_ei = {'INPUT': parameters[self.INPUT_CATCHMENT], 'OVERLAY': parameters[self.INPUT_EXISTING_IMP], 'OUTPUT': 'memory:'}
            ei_result = processing.run("native:intersection", params_ei, context=context, feedback=feedback, is_child_algorithm=True)
            ei_intersection = QgsProcessingUtils.mapLayerFromString(ei_result['OUTPUT'], context)
            for feat in ei_intersection.getFeatures():
                sid = feat[subcatchment_id_field]
                existing_imp_area_by_catch[sid] = existing_imp_area_by_catch.get(sid, 0.0) + feat.geometry().area()
            feedback.pushInfo(f"  Existing impervious extent processed for {len(existing_imp_area_by_catch)} subcatchment(s).")

        # --- 6. Soil / Geology Intersection (shared for soil assessment and CN) ---
        soil_details = collections.defaultdict(lambda: {'total_soil_area': 0.0, 'groups': collections.defaultdict(float)})
        all_soil_groups = set()
        cn_soil_data = collections.defaultdict(lambda: {
            'weighted_cn_sum': 0.0,
            'total_area': 0.0,
            'groups': collections.defaultdict(lambda: {'area': 0.0, 'weighted_cn': 0.0})
        })

        if need_soil_layer:
            feedback.pushInfo("Performing soil/geology intersection...")
            params_soil = {'INPUT': parameters[self.INPUT_CATCHMENT], 'OVERLAY': parameters[self.INPUT_SOIL], 'OUTPUT': 'memory:'}
            soil_result = processing.run("native:intersection", params_soil, context=context, feedback=feedback, is_child_algorithm=True)
            soil_intersection = QgsProcessingUtils.mapLayerFromString(soil_result['OUTPUT'], context)

            feedback.pushInfo("Processing soil data...")
            for feature in soil_intersection.getFeatures():
                subcatchment_id = feature[subcatchment_id_field]
                area_sqm = feature.geometry().area()

                # Soil breakdown collection
                if perform_soil_assessment and soil_group_field:
                    soil_group = feature[soil_group_field]
                    if soil_group:
                        all_soil_groups.add(soil_group)
                        soil_details[subcatchment_id]['groups'][soil_group] += area_sqm
                        soil_details[subcatchment_id]['total_soil_area'] += area_sqm

                # CN weighted sum collection
                if perform_cn_calculation and cn_perv_method == 1 and cn_soil_field:
                    try:
                        cn_value = float(feature[cn_soil_field]) if feature[cn_soil_field] is not None else None
                        if cn_value is not None:
                            cn_soil_data[subcatchment_id]['weighted_cn_sum'] += area_sqm * cn_value
                            cn_soil_data[subcatchment_id]['total_area'] += area_sqm
                            if perform_soil_assessment and soil_group_field:
                                grp = feature[soil_group_field]
                                if grp:
                                    cn_soil_data[subcatchment_id]['groups'][grp]['area'] += area_sqm
                                    cn_soil_data[subcatchment_id]['groups'][grp]['weighted_cn'] += area_sqm * cn_value
                    except (ValueError, TypeError):
                        feedback.pushWarning(f"Could not read CN value from field '{cn_soil_field}' for a feature, skipping.")

        if feedback.isCanceled(): return {}

        # --- 7. Prepare Output Fields ---
        dest_fields = QgsFields(catchment_layer.fields())
        dest_fields.append(QgsField("Imperv[ha]", QVariant.Double, len=15, prec=4))
        dest_fields.append(QgsField("Perv[ha]",   QVariant.Double, len=15, prec=4))
        dest_fields.append(QgsField("TArea[ha]",  QVariant.Double, len=15, prec=4))
        dest_fields.append(QgsField("Imperv[%]",  QVariant.Double, len=10, prec=2))
        dest_fields.append(QgsField("Perv[%]",    QVariant.Double, len=10, prec=2))
        
        sorted_soil_groups = []
        if perform_soil_assessment:
            sorted_soil_groups = sorted(list(all_soil_groups))
            for group in sorted_soil_groups:
                # Truncate group name to 6 chars so the full name stays within
                # the 10-character shapefile field name limit (6 + len('[ha]') = 10).
                g = str(group)[:6]
                dest_fields.append(QgsField(f"{g}[ha]", QVariant.Double, len=15, prec=4))
                dest_fields.append(QgsField(f"{g}[%]",  QVariant.Double, len=10, prec=2))
            # 'NULL[ha]' = 8 chars, 'NULL[%]' = 7 chars — safe for shapefiles
            dest_fields.append(QgsField("NULL[ha]", QVariant.Double, len=15, prec=4))
            dest_fields.append(QgsField("NULL[%]",  QVariant.Double, len=10, prec=2))

        if use_existing_imp:
            dest_fields.append(QgsField("LU_Imp[%]", QVariant.Double, len=10, prec=2))
            dest_fields.append(QgsField("EI_Imp[%]", QVariant.Double, len=10, prec=2))
            dest_fields.append(QgsField("Imp_Src",   QVariant.String, len=2))

        if perform_cn_calculation:
            dest_fields.append(QgsField("PervCN",   QVariant.Double, len=10, prec=2))
            dest_fields.append(QgsField("Catch_CN", QVariant.Double, len=10, prec=2))


        # --- 8. Final Calculations, Logging, and Feature Writing ---
        n_orig_fields = catchment_layer.fields().count()

        # Always write back to the input catchment layer
        actual_layer = self.parameterAsVectorLayer(parameters, self.INPUT_CATCHMENT, context)
        if actual_layer is None:
            raise QgsProcessingException(
                'Could not access the input catchment layer for in-place modification. '
                'Ensure the layer is loaded in the QGIS project (not provided as a bare file path).'
            )
        if not actual_layer.isEditable():
            actual_layer.startEditing()
        new_field_defs = [dest_fields.at(i) for i in range(n_orig_fields, dest_fields.count())]
        for field in new_field_defs:
            if actual_layer.fields().lookupField(field.name()) == -1:
                actual_layer.addAttribute(field)
        actual_layer.updateFields()

        # Open log file for direct streaming (avoids accumulating a huge list in memory)
        log_file_handle = None
        if save_log_file and log_file_path:
            log_dir = os.path.dirname(log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            txt_path = os.path.splitext(log_file_path)[0] + '.txt'
            try:
                log_file_handle = open(txt_path, 'w', encoding='utf-8')
                log_file_handle.write("--- DETAILED CALCULATION LOG ---\n")
            except Exception as e:
                feedback.reportError(f"Could not open log file '{txt_path}'. Error: {e}", False)
                log_file_handle = None

        def log(line):
            if log_file_handle:
                log_file_handle.write(line + '\n')

        feedback.pushInfo("Writing results to output layer...")
        feature_count = catchment_layer.featureCount()
        total = 100.0 / feature_count if feature_count else 0
        last_progress = -1

        for current, feature in enumerate(catchment_layer.getFeatures()):
            if feedback.isCanceled(): break

            subcatchment_id = feature[subcatchment_id_field]
            true_total_area_sqm = feature.geometry().area()
            
            # Land-use derived impervious
            lu_impervious_area_sqm = impervious_details.get(subcatchment_id, 0.0)
            lu_percent_impervious = (lu_impervious_area_sqm / true_total_area_sqm) * 100 if true_total_area_sqm > 0 else 0

            # Compare with existing impervious extent (if enabled)
            imp_source = 'LU'
            ei_percent_impervious = None
            if use_existing_imp:
                ei_area_sqm = existing_imp_area_by_catch.get(subcatchment_id, 0.0)
                ei_percent_impervious = (ei_area_sqm / true_total_area_sqm) * 100 if true_total_area_sqm > 0 else 0.0
                if ei_percent_impervious > lu_percent_impervious:
                    imp_source = 'EI'

            # Resolve final impervious values
            if imp_source == 'EI':
                impervious_area_sqm = ei_area_sqm
                percent_impervious  = ei_percent_impervious
            else:
                impervious_area_sqm = lu_impervious_area_sqm
                percent_impervious  = lu_percent_impervious
            pervious_area_sqm = true_total_area_sqm - impervious_area_sqm
            percent_pervious  = 100.0 - percent_impervious

            feedback.pushInfo(f"  Performing Calculation on SubCatchment ID: {subcatchment_id}")
            if log_file_handle:
                log(f"\n--- Subcatchment ID: {subcatchment_id} ---")
                log(f"  Catchment Total Area:  {true_total_area_sqm:.4f} m²  ({true_total_area_sqm / 10000.0:.4f} ha)")

                # Land use impervious breakdown
                log(f"\n  --- Land Use Impervious Calculation Breakdown ---")
                subcatch_lu = lu_breakdown.get(subcatchment_id, {})
                lu_inter_area = lu_intersection_total_area.get(subcatchment_id, 0.0)
                lu_gap = true_total_area_sqm - lu_inter_area
                col_w = 45
                log(f"  {'Land Use Type':<{col_w}} {'Imp%':>7} {'Segment Area (m²)':>19} {'Imp. Area (m²)':>16}")
                log(f"  {'-'*(col_w + 7 + 19 + 16 + 3)}")
                for (lu_type, imp_pct), vals in sorted(subcatch_lu.items()):
                    log(f"  {lu_type:<{col_w}} {imp_pct:>7.2f} {vals['area_sqm']:>19.4f} {vals['imp_area_sqm']:>16.4f}")
                log(f"  {'-'*(col_w + 7 + 19 + 16 + 3)}")
                log(f"  {'LU intersection subtotal':<{col_w}} {'':>7} {lu_inter_area:>19.4f} {lu_impervious_area_sqm:>16.4f}")
                if lu_gap > 0.001:
                    log(f"  {'Gap (catchment area not in LU layer)':<{col_w}} {'0.00':>7} {lu_gap:>19.4f} {'0.0000':>16}")
                log(f"  {'Catchment total':<{col_w}} {'':>7} {true_total_area_sqm:>19.4f}")
                log(f"\n  AUP MPD — Impervious Area (from LU): {lu_impervious_area_sqm:.4f} m²  ({lu_percent_impervious:.2f}%)")
                if use_existing_imp:
                    log(f"  Existing Impervious Extent — Impervious Area: {ei_area_sqm:.4f} m²  ({ei_percent_impervious:.2f}%)")
                    log(f"  → Source selected: {'Existing Impervious Extent' if imp_source == 'EI' else 'Land Use'} (higher value used)")
                log(f"  FINAL Impervious Area: {impervious_area_sqm:.4f} m²  ({percent_impervious:.2f}%)")
                log(f"  FINAL Pervious Area:   {pervious_area_sqm:.4f} m²  ({percent_pervious:.2f}%)")

            # Prepare new attribute values only (strip any null slots added by updateFields before iterating)
            attributes = list(feature.attributes())[:n_orig_fields]
            attributes.extend([
                impervious_area_sqm / 10000.0,
                pervious_area_sqm / 10000.0,
                true_total_area_sqm / 10000.0,
                percent_impervious,
                percent_pervious
            ])

            null_area_sqm = 0.0  # Set by soil assessment block; used by CN block
            if perform_soil_assessment:
                pervious_soil_breakdown = {}
                pervious_soil_percentages = {}
                subcatch_soil_data = soil_details[subcatchment_id]
                total_soil_area_in_catch = subcatch_soil_data['total_soil_area']
                null_area_sqm = max(0.0, true_total_area_sqm - total_soil_area_in_catch)
                if 0 < null_area_sqm < 0.001:
                    log(f"    Note: Null area ({null_area_sqm:.2e} m²) is insignificantly small (< 0.001 m²) — likely floating-point noise. Recorded as 0.")
                    null_area_sqm = 0.0

                log("\n  --- Pervious Area Breakdown by Soil ---")

                if total_soil_area_in_catch > 0:
                    log(f"    Total area with soil data: {total_soil_area_in_catch:.4f} m²")
                    log(f"    Area with no soil data (ID_NULL): {null_area_sqm:.4f} m²")

                    for group, area in subcatch_soil_data['groups'].items():
                        proportion = area / total_soil_area_in_catch
                        proportional_pervious_area = pervious_area_sqm * proportion
                        pervious_soil_breakdown[group] = proportional_pervious_area
                        percent_vs_total = (proportional_pervious_area / true_total_area_sqm) * 100 if true_total_area_sqm > 0 else 0
                        percent_vs_pervious = (proportional_pervious_area / pervious_area_sqm) * 100 if pervious_area_sqm > 0 else 0
                        pervious_soil_percentages[group] = percent_vs_total
                        log(f"    - Group '{group}': Soil coverage proportion: {proportion:.2%} → Pervious area attributed: {proportional_pervious_area:.4f} m² ({percent_vs_pervious:.2f}% of pervious | {percent_vs_total:.2f}% of total catchment)")
                else:
                    log("    No soil data found for this subcatchment.")

                null_percent = (null_area_sqm / true_total_area_sqm) * 100 if true_total_area_sqm > 0 else 0.0
                null_percent_of_pervious = (null_area_sqm / pervious_area_sqm) * 100 if pervious_area_sqm > 0 else 0.0
                log(f"    - Group 'ID_NULL': {null_area_sqm:.4f} m² ({null_percent_of_pervious:.2f}% of pervious | {null_percent:.2f}% of total catchment)")

                for group in sorted_soil_groups:
                    attributes.append(pervious_soil_breakdown.get(group, 0.0) / 10000.0)
                    attributes.append(pervious_soil_percentages.get(group, 0.0))
                attributes.append(null_area_sqm / 10000.0)
                attributes.append(null_percent)


            # Existing impervious extent comparison fields
            if use_existing_imp:
                attributes.extend([
                    lu_percent_impervious,
                    ei_percent_impervious if ei_percent_impervious is not None else None,
                    imp_source
                ])

            # CN calculation
            if perform_cn_calculation:
                if cn_perv_method == 0:  # User-supplied
                    cn_perv = float(cn_perv_user_value)
                    cn_perv_log = [f"    CN_perv = {cn_perv:.2f} (user-supplied)"]
                else:  # From soil layer
                    d = cn_soil_data[subcatchment_id]
                    use_pergroup = (perform_soil_assessment
                                    and bool(d['groups'])
                                    and bool(pervious_soil_breakdown)
                                    and pervious_area_sqm > 0)

                    if use_pergroup:
                        # Compute CN_perv using pervious area per group — same formula style as Catch_CN
                        weighted_sum = 0.0
                        for grp, gd in d['groups'].items():
                            if gd['area'] > 0:
                                grp_cn = gd['weighted_cn'] / gd['area']
                                weighted_sum += pervious_soil_breakdown.get(grp, 0.0) * grp_cn
                        null_contrib = null_area_sqm * cn_default_value
                        cn_perv = (weighted_sum + null_contrib) / pervious_area_sqm

                        cn_perv_log = [f"    CN_perv (area-weighted from soil layer):"]
                        formula_parts = []
                        for grp in sorted(d['groups'].keys()):
                            gd = d['groups'][grp]
                            if gd['area'] > 0:
                                grp_cn = gd['weighted_cn'] / gd['area']
                                perv_area = pervious_soil_breakdown.get(grp, 0.0)
                                cn_perv_log.append(
                                    f"      Group '{grp}': {perv_area:.4f} m² × CN({grp_cn:.2f}) = {perv_area * grp_cn:.4f} m²·CN"
                                )
                                formula_parts.append(f"{perv_area:.4f}×{grp_cn:.2f}")
                        if null_area_sqm > 0:
                            cn_perv_log.append(
                                f"      ID_NULL: {null_area_sqm:.4f} m² × CN_default({cn_default_value}) = {null_contrib:.4f} m²·CN"
                            )
                            formula_parts.append(f"{null_area_sqm:.4f}×{cn_default_value}")
                        else:
                            cn_perv_log.append(f"      ID_NULL: 0.0000 m² (full soil coverage)")
                        cn_perv_log.append(
                            f"      CN_perv = ({' + '.join(formula_parts)}) / {pervious_area_sqm:.4f} = {cn_perv:.2f}"
                        )
                    else:
                        # Aggregate fallback (soil assessment disabled)
                        soil_covered_area = d['total_area']
                        null_cn_area = max(0.0, pervious_area_sqm - soil_covered_area)
                        if null_cn_area < 0.001:
                            null_cn_area = 0.0
                        total_cn_area = soil_covered_area + null_cn_area
                        if total_cn_area > 0:
                            cn_perv = (d['weighted_cn_sum'] + null_cn_area * cn_default_value) / total_cn_area
                        else:
                            cn_perv = float(cn_default_value)
                            feedback.pushWarning(f"No CN soil data for '{subcatchment_id}', PervCN set to default ({cn_default_value}).")
                        cn_perv_log = [
                            f"    CN_perv (area-weighted from soil layer):",
                            f"      Soil-covered: {soil_covered_area:.4f} m², weighted CN sum: {d['weighted_cn_sum']:.4f} m²·CN",
                            f"      Null area   : {null_cn_area:.4f} m² × CN_default({cn_default_value}) = {null_cn_area * cn_default_value:.4f} m²·CN",
                            f"      CN_perv = ({d['weighted_cn_sum']:.4f} + {null_cn_area * cn_default_value:.4f}) / {total_cn_area:.4f} = {cn_perv:.2f}",
                        ]

                if true_total_area_sqm > 0:
                    cn_catch = (impervious_area_sqm * cn_imp + pervious_area_sqm * cn_perv) / true_total_area_sqm
                else:
                    cn_catch = 0.0

                log(f"\n  --- Curve Number ---")
                for line in cn_perv_log:
                    log(line)
                log(f"    CN_imp={cn_imp}, CN_perv={cn_perv:.2f}")
                log(f"    Catch_CN = ({impervious_area_sqm:.4f}×{cn_imp} + {pervious_area_sqm:.4f}×{cn_perv:.2f}) / {true_total_area_sqm:.4f} = {cn_catch:.2f}")
                attributes.extend([cn_perv, cn_catch])

            new_attr_values = attributes[n_orig_fields:]
            new_field_names = [dest_fields.at(i) for i in range(n_orig_fields, dest_fields.count())]
            for field, value in zip(new_field_names, new_attr_values):
                field_idx = actual_layer.fields().lookupField(field.name())
                if field_idx >= 0:
                    actual_layer.changeAttributeValue(feature.id(), field_idx, value)

            # Throttle progress updates — only fire when the integer % changes
            new_progress = int(current * total)
            if new_progress != last_progress:
                feedback.setProgress(new_progress)
                last_progress = new_progress

        # --- 9. Close Log File ---
        if log_file_handle:
            try:
                log_file_handle.write("\n--- END OF DETAILED LOG ---\n")
                log_file_handle.close()
                feedback.pushInfo(f"Log saved to: {txt_path}")
            except Exception as e:
                feedback.reportError(f"Could not finalise log file. Error: {e}", False)

        # --- 10. Finalize ---
        if not actual_layer.commitChanges():
            errors = actual_layer.commitErrors()
            actual_layer.rollBack()
            detail = '\n  '.join(errors) if errors else 'No detail from provider.'
            raise QgsProcessingException(
                f'Failed to commit changes to the input catchment layer.\n\n'
                f'Provider errors:\n  {detail}\n\n'
                f'Common causes:\n'
                f'  • Shapefile field name exceeds 10 characters\n'
                f'  • Layer file is read-only or locked by another application\n'
                f'  • Provider does not support in-place field addition\n'
                f'  • Run the tool on a GeoPackage layer to avoid shapefile name limits'
            )
        feedback.pushInfo("Fields added and saved to the input catchment layer.")

        feedback.pushInfo("Processing complete.")
        return {}

