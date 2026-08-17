# -*- coding: utf-8 -*-
"""
Subcatchment Time of Concentration — Auckland TP108 formula.

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V01"
__status__     = "Stable"
_LINKED_PROMPT = "SCH_SubCatchTimeOfConcentrationPrompt_GM.txt"

import os
import math
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterEnum,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsFeatureSink,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsSpatialIndex,
    QgsWkbTypes,
)
import processing


class SubcatchmentToC(QgsProcessingAlgorithm):

    INPUT_CATCHMENT = "INPUT_CATCHMENT"
    FIELD_LENGTH    = "FIELD_LENGTH"
    FIELD_CN        = "FIELD_CN"
    DEFAULT_CN      = "DEFAULT_CN"
    FIELD_SLOPE     = "FIELD_SLOPE"
    CHANNEL_FACTOR  = "CHANNEL_FACTOR"
    OUTPUT_MODE     = "OUTPUT_MODE"
    OUTPUT_LAYER    = "OUTPUT_LAYER"
    SAVE_LOG        = "SAVE_LOG"
    LOG_FILE_PATH   = "LOG_FILE_PATH"
    CHECK_OVERLAPS  = "CHECK_OVERLAPS"

    OUTPUT_MODE_OPTIONS = ["Separate output layer", "Update input layer"]

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def name(self):
        return "sch_time_of_concentration_v01"

    def displayName(self):
        return self.tr("Subcatchment Time Of Concentration V01")

    def group(self):
        return self.tr("SubCatchment Assessment")

    def groupId(self):
        return "subcatchment_assessment"

    def createInstance(self):
        return SubcatchmentToC()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return self.tr(
            "Subcatchment Time of Concentration\n\n"
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Computes Time of Concentration (ToC) for subcatchments "
            "using the Auckland TP108 formula and outputs ToC in minutes.\n\n"
            "Formula: t_c = 0.14 × C × L^0.66 × (CN/(200-CN))^-0.55 × Sc^-0.30\n\n"
            "Inputs:\n"
            "  • Subcatchment polygon layer\n"
            "  • Catchment Length field (metres)\n"
            "  • Curve Number (CN) field\n"
            "  • Slope field (m/m)\n"
            "  • Channelisation Factor C (default: 0.6)\n\n"
            "Outputs:\n"
            "  • Polygon layer (or in-place update) with ToC_min field (minutes)\n\n"
            "Important:\n"
            "  • CRS must be projected and use metres\n"
            "  • CN must be between 0 and 100 (exclusive)\n"
            "  • Minimum ToC enforced at 10 minutes\n"
            "  • Zero or negative slope is clamped to 0.0001 m/m"
        )

    def help(self):
        return self.tr(
            "<html><body>"
            "<h2>Subcatchment Time of Concentration</h2>"
            "<p><b>Author:</b> Dipendra Magaju | <b>Tested On:</b> QGIS 3.44.2</p>"
            "<h3>Formula</h3>"
            "<pre>t_c = 0.14 × C × L^0.66 × (CN/(200-CN))^-0.55 × Sc^-0.30</pre>"
            "<h3>Important Notes</h3>"
            "<ul>"
            "<li>CRS must be projected in metres.</li>"
            "<li>CN must be strictly between 0 and 100.</li>"
            "<li>Minimum ToC is 10 minutes.</li>"
            "<li>Zero or negative slope is clamped to 0.0001 m/m.</li>"
            "<li>In-place update directly modifies the source layer — back up data first.</li>"
            "</ul>"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_CATCHMENT,
                self.tr("Input Subcatchment Layer"),
                types=[QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_LENGTH,
                self.tr("Catchment Length Field (m)"),
                parentLayerParameterName=self.INPUT_CATCHMENT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="Catch_L_m"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_CN,
                self.tr("Curve Number (CN) Field"),
                parentLayerParameterName=self.INPUT_CATCHMENT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="Catch_CN",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DEFAULT_CN,
                self.tr("Default CN value (used when no field is selected, or field value is NULL)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=75.0,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_SLOPE,
                self.tr("Slope Field (m/m)"),
                parentLayerParameterName=self.INPUT_CATCHMENT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="Sc_TP108"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CHANNEL_FACTOR,
                self.tr("Channelisation Factor C"),
                type=QgsProcessingParameterNumber.Double,
                minValue=0.01,
                defaultValue=0.6
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_MODE,
                self.tr("Output Mode"),
                options=self.OUTPUT_MODE_OPTIONS,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr("Output Layer"),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CHECK_OVERLAPS,
                self.tr("Check for overlapping polygons (slow)"),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SAVE_LOG,
                self.tr("Save log file"),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.LOG_FILE_PATH,
                self.tr("Log File"),
                fileFilter="Text files (*.txt)",
                optional=True
            )
        )

    def _check_layer_overlaps(self, layer, label):
        feats   = {f.id(): f for f in layer.getFeatures()}
        index   = QgsSpatialIndex(feats.values())
        overlap = 0
        checked = set()

        for fid, f in feats.items():
            geom = f.geometry()
            for cid in index.intersects(geom.boundingBox()):
                if fid == cid:
                    continue
                pair = tuple(sorted((fid, cid)))
                if pair in checked:
                    continue
                checked.add(pair)
                other = feats[cid].geometry()
                if geom.intersects(other):
                    intersection = geom.intersection(other)
                    if intersection and not intersection.isEmpty():
                        overlap += intersection.area()

        if overlap > 0.01:
            raise QgsProcessingException(
                f"Overlapping polygons detected in '{label}'. Total overlap: {overlap:.3f} m²"
            )

    def processAlgorithm(self, parameters, context, feedback):
        layer       = self.parameterAsSource(parameters, self.INPUT_CATCHMENT, context)
        field_L     = self.parameterAsString(parameters, self.FIELD_LENGTH, context)
        field_CN    = self.parameterAsString(parameters, self.FIELD_CN, context)
        default_cn  = self.parameterAsDouble(parameters, self.DEFAULT_CN, context)
        field_S     = self.parameterAsString(parameters, self.FIELD_SLOPE, context)
        C_factor    = self.parameterAsDouble(parameters, self.CHANNEL_FACTOR, context)
        output_mode = self.parameterAsEnum(parameters, self.OUTPUT_MODE, context)
        check_overlaps = self.parameterAsBool(parameters, self.CHECK_OVERLAPS, context)
        save_log    = self.parameterAsBool(parameters, self.SAVE_LOG, context)
        log_path    = self.parameterAsFile(parameters, self.LOG_FILE_PATH, context)

        if C_factor <= 0:
            raise QgsProcessingException("Channelisation factor must be greater than zero.")

        # Validate CN source: need either a field or a valid default value.
        has_cn_field   = bool(field_CN)
        has_default_cn = (0 < default_cn < 100)
        if not has_cn_field and not has_default_cn:
            raise QgsProcessingException(
                "Curve Number: provide either a CN field or a default CN value (0 < CN < 100)."
            )

        if check_overlaps:
            self._check_layer_overlaps(layer, "Subcatchments")

        fields = QgsFields(layer.fields())
        fields.append(QgsField("ToC_min", QVariant.Double, len=12, prec=2))

        if output_mode == 0:
            sink, dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_LAYER, context,
                fields, layer.wkbType(), layer.sourceCrs()
            )
        else:
            actual_layer = self.parameterAsVectorLayer(parameters, self.INPUT_CATCHMENT, context)
            if not actual_layer.isEditable():
                actual_layer.startEditing()
            if actual_layer.fields().lookupField("ToC_min") == -1:
                actual_layer.addAttribute(QgsField("ToC_min", QVariant.Double, len=12, prec=2))
            actual_layer.updateFields()

        log_file = None
        if save_log and log_path:
            try:
                log_file = open(log_path, "w", encoding="utf-8")
                log_file.write("--- Subcatchment Time of Concentration Log ---\n\n")
                log_file.write(f"  Length field:    {field_L}\n")
                if has_cn_field:
                    log_file.write(f"  CN field:        {field_CN}\n")
                if has_default_cn:
                    log_file.write(f"  Default CN:      {default_cn}\n")
                log_file.write(f"  Slope field:     {field_S}\n")
                log_file.write(f"  Channel Factor C:{C_factor}\n")
                log_file.write(f"  Output Mode:     {self.OUTPUT_MODE_OPTIONS[output_mode]}\n\n")
            except Exception as e:
                feedback.reportError(f"Could not open log file: {e}")
                log_file = None

        def log(msg):
            if log_file:
                log_file.write(msg + "\n")

        for f in layer.getFeatures():
            fid     = f.id()
            geom    = f.geometry()
            area_m2 = geom.area()
            L_raw   = f[field_L]
            CN      = f[field_CN] if has_cn_field else None
            S_raw   = f[field_S]
            ToC_min = None

            # Fall back to default CN when the field value is absent or out of range.
            if CN is None or CN <= 0 or CN >= 100:
                if has_default_cn:
                    CN = default_cn
                else:
                    CN = None

            if (L_raw is None or S_raw is None) and area_m2 < 30000:
                ToC_min = 10
                log(f"Feature {fid}: Missing L/S AND area < 3 ha → ToC = 10 min")
            else:
                if CN is None or CN <= 0 or CN >= 100:
                    log(f"Feature {fid}: Invalid CN → ToC = NULL")
                else:
                    L_km = (L_raw if L_raw else 0) / 1000.0
                    S    = S_raw if S_raw else 0.0001
                    if S <= 0:
                        S = 0.0001
                    try:
                        ratio      = CN / (200.0 - CN)
                        tc_hr      = 0.14 * C_factor * (L_km ** 0.66) * (ratio ** -0.55) * (S ** -0.30)
                        ToC_min    = max(10.0, tc_hr * 60.0)
                    except Exception as e:
                        log(f"Feature {fid}: ERROR computing ToC: {e}")
                        ToC_min = None

            if output_mode == 0:
                new_f = QgsFeature()
                new_f.setGeometry(geom)
                new_f.setFields(fields)
                new_f.setAttributes(f.attributes() + [ToC_min])
                sink.addFeature(new_f)
            else:
                idx = actual_layer.fields().lookupField("ToC_min")
                actual_layer.changeAttributeValue(fid, idx, ToC_min)

        if log_file:
            log_file.write("\n--- End of Log ---\n")
            log_file.close()

        if output_mode == 1:
            if not actual_layer.commitChanges():
                raise QgsProcessingException(
                    "Failed to commit ToC_min field to the input catchment layer."
                )
            feedback.pushInfo("ToC_min saved to the input catchment layer.")
            return {}

        return {self.OUTPUT_LAYER: dest_id}
