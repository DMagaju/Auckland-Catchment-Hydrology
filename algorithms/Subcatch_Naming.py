# -*- coding: utf-8 -*-
"""
SubCatchmentNaming — Assign spatially ordered unique IDs to subcatchments.

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V01"
__status__     = "Stable"
_LINKED_PROMPT = "SCH_SubcatchmentNamingPrompt_GM.txt"

import random
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterEnum,
    QgsProcessingException,
    QgsVectorDataProvider,
    QgsSpatialIndex,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsFeatureSink,
)


class CalculateSubcatchmentIDs(QgsProcessingAlgorithm):

    INPUT_SUBCATCHMENTS = 'INPUT_SUBCATCHMENTS'
    OUTPUT_MODE         = 'OUTPUT_MODE'
    OUTPUT_LAYER        = 'OUTPUT_LAYER'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculateSubcatchmentIDs()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'sch_naming_v01'

    def displayName(self):
        return self.tr('Subcatchment Naming V01')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Author: Dipendra Magaju\n"
            "Tested On: QGIS 3.44.2\n\n"
            "Assigns a unique spatially ordered ID (SC_0001, SC_0002, …) to each subcatchment\n"
            "by starting from a random polygon and visiting the nearest unvisited neighbour\n"
            "at each step.\n\n"
            "Inputs:\n"
            " • Input Subcatchment Layer: Polygon vector layer (CRS must use metres).\n"
            " • Output Mode: 'New output file' produces a copy; "
            "'Edit input layer in-place' modifies the source directly.\n\n"
            "Outputs:\n"
            " • One new field appended to each feature:\n"
            "   → SubcatchID (String): unique spatially ordered ID, e.g. SC_0001\n\n"
            "Important:\n"
            " • The layer CRS must be projected and use metres.\n"
            " • A field named 'SubcatchID' must not already exist in the input layer.\n"
            " • The start point is random — re-running will produce a different (but valid) sequence.\n"
            " • Supports up to 9 999 subcatchments with the SC_XXXX (4-digit) format.\n"
            " • In-place editing permanently modifies the source file — back up data beforehand."
        )

    def help(self):
        return self.tr(
            "<html><body>\n"
            "<h2>Subcatchment Naming</h2>\n"
            "<p><b>Author:</b> Dipendra Magaju</p>\n"
            "<p><b>Tested On:</b> QGIS 3.44.2</p>\n\n"
            "<h3>Purpose</h3>\n"
            "<p>Assigns a unique sequential ID in the format <code>SC_XXXX</code> to every polygon "
            "in a subcatchment layer using a spatial nearest-neighbour traversal.</p>\n"
            "<h3>Important Notes</h3>\n"
            "<ul>\n"
            "<li>The layer CRS must be projected in metres.</li>\n"
            "<li>A field named <b>SubcatchID</b> must not already exist.</li>\n"
            "<li>The starting subcatchment is chosen randomly.</li>\n"
            "<li>In-place edits cannot be undone via QGIS Undo — back up your data first.</li>\n"
            "</ul>\n"
            "</body></html>"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_SUBCATCHMENTS,
                self.tr('Input Subcatchment Layer'),
                types=[QgsProcessing.TypeVectorPolygon]
            )
        )

        self.output_mode_options = ['New output file', 'Edit input layer in-place']
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_MODE,
                self.tr('Output Mode'),
                options=self.output_mode_options,
                defaultValue=0
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr('Processed Subcatchments'),
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_SUBCATCHMENTS, context)
        if layer is None:
            raise QgsProcessingException(self.tr("Input subcatchment layer not found."))

        output_mode = self.parameterAsInt(parameters, self.OUTPUT_MODE, context)

        if 'SubcatchID' in [f.name() for f in layer.fields()]:
            raise QgsProcessingException(
                self.tr("Field 'SubcatchID' already exists in the input layer. "
                        "Remove or rename it before running this tool.")
            )

        feedback.pushInfo("Loading features and building spatial index...")
        feature_count = layer.featureCount()
        if feature_count == 0:
            raise QgsProcessingException(self.tr("Input layer contains no features."))

        centroids     = {}
        spatial_index = QgsSpatialIndex()

        for current, feat in enumerate(layer.getFeatures()):
            if feedback.isCanceled():
                break
            fid = feat.id()
            centroids[fid] = feat.geometry().centroid().asPoint()
            spatial_index.addFeature(feat)
            feedback.setProgress(int((current + 1) / feature_count * 30))

        all_fids = list(centroids.keys())
        n        = len(all_fids)

        feedback.pushInfo("Computing spatial order (random start, nearest-neighbour traversal)...")
        start_fid  = random.choice(all_fids)
        visited    = {start_fid}
        order      = [start_fid]
        current_pt = centroids[start_fid]

        while len(order) < n:
            if feedback.isCanceled():
                break

            k        = 2
            next_fid = None
            while next_fid is None:
                for c in spatial_index.nearestNeighbor(current_pt, k):
                    if c not in visited:
                        next_fid = c
                        break
                if next_fid is None:
                    if k >= n:
                        next_fid = next(f for f in all_fids if f not in visited)
                        break
                    k = min(k * 2, n)

            visited.add(next_fid)
            order.append(next_fid)
            current_pt = centroids[next_fid]
            feedback.setProgress(30 + int(len(order) / n * 40))

        id_map = {fid: f"SC_{rank:04d}" for rank, fid in enumerate(order, start=1)}

        if output_mode == 1:
            provider = layer.dataProvider()
            if not (provider.capabilities() & QgsVectorDataProvider.AddAttributes):
                raise QgsProcessingException(
                    self.tr("The input layer's data provider does not support adding attributes in-place.")
                )

            provider.addAttributes([QgsField('SubcatchID', QVariant.String, len=10)])
            layer.updateFields()

            idx_cname = layer.fields().indexFromName('SubcatchID')
            provider.changeAttributeValues(
                {fid: {idx_cname: sc_name} for fid, sc_name in id_map.items()}
            )
            layer.updateFields()

            feedback.setProgress(100)
            feedback.pushInfo(f"Done. SubcatchID added in-place to '{layer.name()}'.")
            return {}

        dest_fields = QgsFields(layer.fields())
        dest_fields.append(QgsField('SubcatchID', QVariant.String, len=10))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_LAYER, context,
            dest_fields, layer.wkbType(), layer.sourceCrs()
        )
        if sink is None:
            raise QgsProcessingException(self.tr(
                'Could not create output layer. '
                'Specify an output file path or switch Output Mode to "Edit input layer in-place".'
            ))

        feedback.pushInfo("Writing output features...")
        written = 0
        for feat in layer.getFeatures():
            if feedback.isCanceled():
                break
            new_feat = QgsFeature(dest_fields)
            new_feat.setGeometry(feat.geometry())
            new_feat.setAttributes(list(feat.attributes()) + [id_map[feat.id()]])
            sink.addFeature(new_feat, QgsFeatureSink.FastInsert)
            written += 1
            feedback.setProgress(70 + int(written / n * 30))

        feedback.pushInfo("Processing complete.")
        return {self.OUTPUT_LAYER: dest_id}
