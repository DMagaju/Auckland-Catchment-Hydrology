# -*- coding: utf-8 -*-
"""
Auckland TP108 Subcatchment Flow Calculator.

Author: Dipendra Magaju
Tested On: QGIS 3.44.2
"""

__author__     = "Dipendra Magaju"
__copyright__  = "2026"
__license__    = "GPL"
__version__    = "V03"
__status__     = "Stable"
_LINKED_PROMPT = "SCH_SubcatchFlowCalculationToolDevelopmentPrompt_GM.txt"

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsProcessing,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsFeatureSink,
)
import os


class SubcatchFlowCalculationAlgorithm(QgsProcessingAlgorithm):

    INPUT_LAYER          = 'INPUT_LAYER'
    FIELD_AREA           = 'FIELD_AREA'
    FIELD_TOC            = 'FIELD_TOC'
    FIELD_CN             = 'FIELD_CN'
    FIELD_IMP            = 'FIELD_IMP'
    FIELD_ID             = 'FIELD_ID'
    RAINFALL_DEPTH       = 'RAINFALL_DEPTH'
    AEP_EVENT            = 'AEP_EVENT'
    CC_SCENARIO          = 'CC_SCENARIO'
    STORM_LABEL          = 'STORM_LABEL'
    EVENT_DATE           = 'EVENT_DATE'
    OUTPUT_MODE          = 'OUTPUT_MODE'
    OUTPUT_RAINFALL_CSV  = 'OUTPUT_RAINFALL_CSV'
    OUTPUT_FOLDER        = 'OUTPUT_FOLDER'
    OUTPUT_LAYER         = 'OUTPUT_LAYER'

    AEP_OPTIONS         = ['50%', '20%', '10%', '5%', '2%', '1%']
    CC_OPTIONS          = ['Existing', '2.1°C', '3.8°C']
    OUTPUT_MODE_OPTIONS = [
        'New output layer',
        'Edit input layer in-place',
        'CSV & Plots only (no vector layer)',
        'Rainfall Only (no catchment layer)',
    ]

    # TP108 Table 1 — % increase in 24-hour rainfall depth for climate change.
    _TABLE_1 = {
        '50%': {'2.1°C': 15.1, '3.8°C': 27.4},
        '20%': {'2.1°C': 16.4, '3.8°C': 29.6},
        '10%': {'2.1°C': 17.0, '3.8°C': 30.8},
        '5%':  {'2.1°C': 17.2, '3.8°C': 31.2},
        '2%':  {'2.1°C': 17.6, '3.8°C': 31.9},
        '1%':  {'2.1°C': 18.1, '3.8°C': 32.7},
    }

    # TP108 Table 2 — normalised 24-hour temporal profile.
    # (count, existing_factor, cc2.1_factor, cc3.8_factor): number of
    # 10-min blocks at that intensity multiplier, per climate scenario.
    _PROFILE = [
        (36, 0.34, 0.29, 0.27),
        (18, 0.74, 0.69, 0.66),
        ( 6, 0.96, 0.89, 0.85),
        ( 6, 1.40, 1.40, 1.40),
        ( 3, 2.20, 2.30, 2.36),
        ( 1, 3.80, 4.14, 4.35),
        ( 1, 4.80, 5.23, 5.50),
        ( 1, 8.70, 9.49, 9.97),
        ( 1,16.20,17.66,18.56),   # peak
        ( 1, 5.90, 6.43, 6.76),
        ( 1, 4.20, 4.58, 4.81),
        ( 3, 2.90, 3.08, 3.20),
        ( 6, 1.70, 1.75, 1.77),
        ( 6, 1.20, 1.16, 1.14),
        (18, 0.75, 0.72, 0.70),
        (36, 0.40, 0.36, 0.34),
    ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return SubcatchFlowCalculationAlgorithm()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def name(self):
        return 'sch_flow_calculation_v01'

    def displayName(self):
        return self.tr('Flow Calculation (TP108)')

    def group(self):
        return self.tr('SubCatchment Assessment')

    def groupId(self):
        return 'subcatchment_assessment'

    def shortHelpString(self):
        return self.tr(
            "Flow Calculation (TP108)  —  Auckland Catchment Hydrology\n\n"
            "Author: Dipendra Magaju  |  Tested On: QGIS 3.44.2\n\n"
            "Calculates peak flow and complete flow hydrograph for each subcatchment "
            "using Auckland TP108 rainfall-runoff methodology with SCS Curve Number losses.\n\n"
            "Rainfall:\n"
            "  Enter the EXISTING 24-hour design rainfall depth in mm, then select the\n"
            "  AEP Event and Climate Change Scenario. The depth is scaled using TP108\n"
            "  Table 1, then distributed across 144 × 10-minute intervals using the\n"
            "  TP108 Table 2 temporal profile for the selected scenario.\n\n"
            "Event identification:\n"
            "  • Storm Event Label — free text, e.g. 'Design Storm A'\n"
            "  • Event Date        — date picker; used in filenames\n\n"
            "Output modes:\n"
            "  0 — New output layer (+ CSVs & plots)\n"
            "  1 — Edit input layer in-place (+ CSVs & plots)\n"
            "  2 — CSV & Plots only, no vector layer output\n\n"
            "  Tick 'Export Rainfall CSV' in any mode to also save the 10-minute\n"
            "  rainfall time series to Rainfall_{tag}.csv, plus a 2-column\n"
            "  RainfallForTUFLOWInput_{tag}.csv (Time_hr, Rainfall_mm) for TUFLOW.\n"
            "  (Mode 2 always exports both rainfall CSVs.)\n\n"
            "Notes:\n"
            "  • numpy required (normally bundled with QGIS)\n"
            "  • pandas and matplotlib must be installed for CSV/plot export\n"
            "  • Area field in hectares; ToC field in minutes; min ToC = 10 min\n"
            "  • Impervious CN=98 Ia=0 mm; pervious Ia fixed at 5 mm"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER, self.tr('Subcatchment Layer (not needed for Rainfall Only)'),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_AREA, self.tr('Area Field (ha)'),
                defaultValue='TArea[ha]',
                parentLayerParameterName=self.INPUT_LAYER,
                type=QgsProcessingParameterField.Numeric,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_TOC, self.tr('Time of Concentration Field (minutes)'),
                defaultValue='ToC_min',
                parentLayerParameterName=self.INPUT_LAYER,
                type=QgsProcessingParameterField.Numeric,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_CN, self.tr('Curve Number Field (pervious CN)'),
                defaultValue='PervCN',
                parentLayerParameterName=self.INPUT_LAYER,
                type=QgsProcessingParameterField.Numeric,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_IMP, self.tr('Imperviousness Field (%)'),
                defaultValue='Imperv[%]',
                parentLayerParameterName=self.INPUT_LAYER,
                type=QgsProcessingParameterField.Numeric,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD_ID, self.tr('Catchment ID Field (optional)'),
                defaultValue=None,
                parentLayerParameterName=self.INPUT_LAYER,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RAINFALL_DEPTH, self.tr('Existing 24-hour Rainfall Depth (mm)'),
                type=QgsProcessingParameterNumber.Double,
                minValue=0.1, defaultValue=50.0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.AEP_EVENT, self.tr('AEP Event'),
                options=self.AEP_OPTIONS, defaultValue=5   # 1%
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.CC_SCENARIO, self.tr('Climate Change Scenario'),
                options=self.CC_OPTIONS, defaultValue=0    # Existing
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.STORM_LABEL,
                self.tr('Storm Event Label (optional)'),
                defaultValue='',
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.EVENT_DATE,
                self.tr('Event Date (optional)'),
                type=QgsProcessingParameterDateTime.Date,
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
            QgsProcessingParameterBoolean(
                self.OUTPUT_RAINFALL_CSV,
                self.tr('Export Rainfall Time Series CSV'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER, self.tr('Output Folder (CSV and plots)')
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT_LAYER, self.tr('Output Layer (with peak flows)'),
                optional=True
            )
        )

    # ── Hydrograph calculation ───────────────────────────────────────────────

    def _calc_hydrograph(self, rain_inc, cum_rain, area_ha, imp_pct, cn, tc_mins):
        import numpy as np

        def runoff_incremental(P_cum, CN, Ia):
            if CN <= 0:
                return np.zeros_like(P_cum, dtype=float)
            S = (1000.0 / CN - 10.0) * 25.4
            Q_cum = np.zeros_like(P_cum, dtype=float)
            mask = P_cum > Ia
            Q_cum[mask] = ((P_cum[mask] - Ia) ** 2) / (P_cum[mask] - Ia + S)
            return np.insert(np.diff(Q_cum), 0, Q_cum[0])

        Q_imp  = runoff_incremental(cum_rain, 98.0, 0.0)
        Q_perv = runoff_incremental(cum_rain, float(cn), 5.0)

        tc_hr = float(tc_mins) / 60.0
        Tp    = 0.083 + (0.667 * tc_hr)
        Tb    = 2.67 * Tp
        dt_hr = 10.0 / 60.0

        # Integer-based n_uh avoids ±1 variation from np.arange floating-point
        # when Tb / dt_hr is very close to an integer.
        n_uh    = int(Tb / dt_hr) + 2
        uh_time = np.arange(n_uh) * dt_hr
        uh_flow = np.where(
            uh_time <= Tp,
            (0.208 / Tp) * (uh_time / Tp),
            np.maximum((0.208 / Tp) * (1.0 - (uh_time - Tp) / (Tb - Tp)), 0.0)
        )
        uh_flow = np.maximum(uh_flow, 0.0)

        n_total       = len(rain_inc) + len(uh_flow) - 1
        time_axis     = np.arange(n_total) * dt_hr
        area_imp_km2  = (area_ha * (imp_pct / 100.0)) / 100.0
        area_perv_km2 = (area_ha * (1.0 - imp_pct / 100.0)) / 100.0

        flow_imp   = np.convolve(Q_imp,  uh_flow)[:n_total] * area_imp_km2
        flow_perv  = np.convolve(Q_perv, uh_flow)[:n_total] * area_perv_km2
        total_flow = flow_imp + flow_perv

        return time_axis, total_flow, flow_imp, flow_perv

    # ── Per-subcatchment plot ────────────────────────────────────────────────

    def _plot_subcatchment(self, catch_id, time_axis, total_flow, flow_imp, flow_perv,
                           rain_inc, rainfall_depth, aep_str, cc_str, pct_increase,
                           future_depth, storm_label, event_date,
                           area_ha, peak_flow, total_vol, output_folder, file_tag):
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        dt_hr     = 10.0 / 60.0
        rain_time = np.arange(len(rain_inc)) * dt_hr
        cum_vol   = np.cumsum(total_flow) * 600.0

        label_disp = storm_label if storm_label else '—'
        date_disp  = event_date  if event_date  else '—'

        fig, (ax1, ax2, ax3) = plt.subplots(
            3, 1, figsize=(14, 13), sharex=True,
            gridspec_kw={'height_ratios': [1, 1.6, 1]}
        )
        fig.suptitle(
            f"Subcatchment Flow Analysis  —  Auckland TP108\n"
            f"Catchment: {catch_id}   |   AEP: {aep_str}   |   CC Scenario: {cc_str}   |   "
            f"Storm: {label_disp}   |   Date: {date_disp}\n"
            f"Existing Depth: {rainfall_depth:.1f} mm   |   "
            f"CC Increment: {pct_increase:.1f}%   |   Design Depth: {future_depth:.1f} mm   |   "
            f"Area: {area_ha:.1f} ha   |   "
            f"Peak Flow: {peak_flow:.3f} m³/s   |   "
            f"Total Volume: {total_vol:,.0f} m³",
            fontsize=10, fontweight='bold'
        )

        ax1.bar(rain_time, rain_inc, width=dt_hr * 0.85, color='steelblue', alpha=0.75,
                label='Incremental Rainfall (mm)')
        ax1.set_ylabel('Rainfall (mm)', fontsize=9)
        ax1.invert_yaxis()
        ax1.legend(loc='upper right', fontsize=8)
        ax1.grid(True, axis='y', alpha=0.3, linestyle='--')
        ax1.set_axisbelow(True)
        ax1.set_title('Precipitation', fontsize=9, loc='left', pad=3)

        ax2.fill_between(time_axis, total_flow, color='skyblue', alpha=0.35)
        ax2.plot(time_axis, total_flow, color='#1a3a6b', linewidth=2.0,
                 label=f'Total  (peak = {peak_flow:.3f} m³/s)')
        ax2.plot(time_axis, flow_imp,  color='#c0392b', linewidth=1.2,
                 linestyle='--', label='Impervious')
        ax2.plot(time_axis, flow_perv, color='#27ae60', linewidth=1.2,
                 linestyle='--', label='Pervious')
        ax2.set_ylabel('Flow (m³/s)', fontsize=9)
        ax2.legend(loc='upper right', fontsize=8)
        ax2.grid(True, alpha=0.3, linestyle='--')
        ax2.set_axisbelow(True)
        ax2.set_title('Flow Hydrograph', fontsize=9, loc='left', pad=3)

        ax3.plot(time_axis, cum_vol, color='#8e44ad', linewidth=2.0,
                 label=f'Cumulative Volume (total = {total_vol:,.0f} m³)')
        ax3.fill_between(time_axis, cum_vol, alpha=0.15, color='#8e44ad')
        ax3.axhline(y=total_vol, color='#8e44ad', linestyle=':', linewidth=1.0, alpha=0.6)
        ax3.set_ylabel('Cumulative Volume (m³)', fontsize=9)
        ax3.set_xlabel('Time (hours)', fontsize=9)
        ax3.legend(loc='lower right', fontsize=8)
        ax3.grid(True, alpha=0.3, linestyle='--')
        ax3.set_axisbelow(True)
        ax3.set_title('Cumulative Runoff Volume', fontsize=9, loc='left', pad=3)

        max_hr   = max(float(time_axis[-1]), 26.0)
        tick_hrs = list(range(0, int(max_hr) + 2, 2))
        ax3.set_xticks(tick_hrs)
        ax3.set_xticklabels([f"{h}h" for h in tick_hrs], fontsize=8)
        ax3.set_xlim(0, max_hr)

        plt.tight_layout()
        plot_path = os.path.join(output_folder, f"Plot_{catch_id}_{file_tag}.png")
        fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return plot_path

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_tag(text):
        return ''.join(c if c.isalnum() or c in '-_' else '_' for c in text).strip('_')

    @staticmethod
    def _hr_to_hhmmss(h):
        total_s = int(round(h * 3600))
        return f'{total_s // 3600:02d}:{(total_s % 3600) // 60:02d}:{total_s % 60:02d}'

    @staticmethod
    def _close_rainfall_series(rain_inc, cum_rain):
        """
        rain_inc/cum_rain hold 144 × 10-min bins labelled by each bin's START
        time (0:00 .. 23:50), so the raw series stops at 23:50 rather than
        24:00. Append a closing point at exactly 24:00 hr (zero incremental
        rainfall, cumulative held at its final value) so exported CSVs span
        the full 24-hour storm duration.
        """
        import numpy as np
        dt_hr = 10.0 / 60.0
        rain_time = np.arange(len(rain_inc)) * dt_hr
        rt = np.append(rain_time, 24.0)
        ri = np.append(rain_inc, 0.0)
        cr = np.append(cum_rain, cum_rain[-1] if len(cum_rain) else 0.0)
        return rt, ri, cr

    # ── Main algorithm ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        try:
            import numpy as np
        except ImportError:
            raise QgsProcessingException(
                "numpy is required but not found in this QGIS Python environment."
            )
        try:
            import pandas as pd
        except ImportError:
            raise QgsProcessingException(
                "pandas is required but not found in this QGIS Python environment."
            )

        source          = self.parameterAsVectorLayer(parameters, self.INPUT_LAYER, context)
        field_area      = self.parameterAsString(parameters, self.FIELD_AREA, context)
        field_toc       = self.parameterAsString(parameters, self.FIELD_TOC, context)
        field_cn        = self.parameterAsString(parameters, self.FIELD_CN, context)
        field_imp       = self.parameterAsString(parameters, self.FIELD_IMP, context)
        field_id        = self.parameterAsString(parameters, self.FIELD_ID, context) or None
        rainfall_depth  = self.parameterAsDouble(parameters, self.RAINFALL_DEPTH, context)
        aep_idx         = self.parameterAsEnum(parameters, self.AEP_EVENT, context)
        cc_idx          = self.parameterAsEnum(parameters, self.CC_SCENARIO, context)
        storm_label     = self.parameterAsString(parameters, self.STORM_LABEL, context).strip()
        event_dt        = self.parameterAsDateTime(parameters, self.EVENT_DATE, context)
        event_date      = event_dt.toString('yyyy-MM-dd') if event_dt.isValid() else ''
        output_mode     = self.parameterAsInt(parameters, self.OUTPUT_MODE, context)
        export_rain_csv = self.parameterAsBool(parameters, self.OUTPUT_RAINFALL_CSV, context)
        output_folder   = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)

        aep_str = self.AEP_OPTIONS[aep_idx]
        cc_str  = self.CC_OPTIONS[cc_idx]

        # Modes 2 (CSV & Plots only) and 3 (Rainfall Only) always include rainfall CSV
        if output_mode in (2, 3):
            export_rain_csv = True

        save_to_input = (output_mode == 1)
        create_layer  = (output_mode == 0)
        rainfall_only = (output_mode == 3)

        if not rainfall_only and (source is None or not source.isValid()):
            raise QgsProcessingException(
                "A Subcatchment Layer is required unless Output Mode is "
                "'Rainfall Only (no catchment layer)'."
            )

        # ── Depth adjustment (TP108 Table 1) ────────────────────────────────
        if cc_str == 'Existing':
            pct_increase = 0.0
            future_depth = rainfall_depth
        else:
            pct_increase = self._TABLE_1[aep_str][cc_str]
            future_depth = rainfall_depth * (1 + pct_increase / 100)

        date_tag  = self._safe_tag(event_date.replace('-', ''))  if event_date  else 'NoDate'
        aep_tag   = aep_str.replace('%', 'pct')
        cc_tag    = 'Existing' if cc_str == 'Existing' else f"CC{cc_str.replace('°C', '')}"
        label_tag = self._safe_tag(storm_label) if storm_label else ''
        file_tag  = f"{date_tag}_{aep_tag}_{cc_tag}" + (f"_{label_tag}" if label_tag else "")

        feedback.pushInfo("─" * 55)
        feedback.pushInfo(f"  AEP             : {aep_str}")
        feedback.pushInfo(f"  CC Scenario     : {cc_str}")
        feedback.pushInfo(f"  Existing Depth  : {rainfall_depth:.1f} mm")
        feedback.pushInfo(f"  CC Increment    : {pct_increase:.1f}%")
        feedback.pushInfo(f"  Design Depth    : {future_depth:.3f} mm")
        feedback.pushInfo(f"  Storm Label     : {storm_label or '—'}")
        feedback.pushInfo(f"  Event Date      : {event_date or '—'}")
        feedback.pushInfo(f"  Output Mode     : {self.OUTPUT_MODE_OPTIONS[output_mode]}")
        feedback.pushInfo(f"  Export Rain CSV : {export_rain_csv}")
        feedback.pushInfo(f"  File Tag        : {file_tag}")
        feedback.pushInfo("─" * 55)

        # ── Build rainfall series (scenario-specific column) ────────────────
        col          = {'Existing': 1, '2.1°C': 2, '3.8°C': 3}[cc_str]
        total_factor = sum(row[0] * row[col] for row in self._PROFILE)
        rain_inc     = np.array([
            (future_depth / total_factor) * row[col]
            for row in self._PROFILE
            for _ in range(row[0])
        ])
        cum_rain = np.cumsum(rain_inc)

        # ── Rainfall Only (mode 3): no catchment layer needed ───────────────
        if rainfall_only:
            os.makedirs(output_folder, exist_ok=True)
            rt, ri, cr = self._close_rainfall_series(rain_inc, cum_rain)

            df_rain = pd.DataFrame({
                'Time':           [self._hr_to_hhmmss(h) for h in rt],
                'Time_hr':        np.round(rt, 4),
                'Incremental_mm': np.round(ri, 4),
                'Cumulative_mm':  np.round(cr, 4),
            })
            rain_csv_path = os.path.join(output_folder, f"Rainfall_{file_tag}.csv")
            df_rain.to_csv(rain_csv_path, index=False)
            feedback.pushInfo(f"Rainfall CSV  → {rain_csv_path}")

            df_tuflow = pd.DataFrame({
                'Time_hr':     np.round(rt, 4),
                'Rainfall_mm': np.round(ri, 4),
            })
            tuflow_csv_path = os.path.join(output_folder, f"RainfallForTUFLOWInput_{file_tag}.csv")
            df_tuflow.to_csv(tuflow_csv_path, index=False)
            feedback.pushInfo(f"TUFLOW Rainfall CSV  → {tuflow_csv_path}")

            feedback.setProgress(100)
            return {}

        # ── Output sink (mode 0) ──────────────────────────────────────────
        sink    = None
        sink_id = None
        out_fields = QgsFields()
        if create_layer:
            for f in source.fields():
                out_fields.append(f)
            out_fields.append(QgsField('PeakFlow_m3s', QVariant.Double))
            out_fields.append(QgsField('TotalVol_m3',  QVariant.Double))
            (sink, sink_id) = self.parameterAsSink(
                parameters, self.OUTPUT_LAYER, context,
                out_fields, source.wkbType(), source.sourceCrs()
            )
            if sink is None:
                raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT_LAYER))

        os.makedirs(output_folder, exist_ok=True)

        # ── Write-back fields (mode 1) ────────────────────────────────────
        attr_changes = {}
        pk_idx = vl_idx = -1
        if save_to_input:
            dp = source.dataProvider()
            if source.fields().lookupField('PeakFlow_m3s') < 0:
                dp.addAttributes([QgsField('PeakFlow_m3s', QVariant.Double)])
            if source.fields().lookupField('TotalVol_m3') < 0:
                dp.addAttributes([QgsField('TotalVol_m3', QVariant.Double)])
            source.updateFields()
            pk_idx = source.fields().lookupField('PeakFlow_m3s')
            vl_idx = source.fields().lookupField('TotalVol_m3')

        field_id_idx    = source.fields().lookupField(field_id) if field_id else -1
        total_features  = source.featureCount()
        src_field_count = source.fields().count()
        all_hydrographs = {}

        try:
            import matplotlib
            has_mpl = True
        except ImportError:
            has_mpl = False
            feedback.pushWarning(
                "matplotlib not available — plots skipped. "
                "Install via: pip install matplotlib"
            )

        # ── Export rainfall CSV ───────────────────────────────────────────
        if export_rain_csv:
            rt, ri, cr = self._close_rainfall_series(rain_inc, cum_rain)
            df_rain    = pd.DataFrame({
                'Time':            [self._hr_to_hhmmss(h) for h in rt],
                'Time_hr':         np.round(rt, 4),
                'Incremental_mm':  np.round(ri, 4),
                'Cumulative_mm':   np.round(cr, 4),
            })
            rain_csv_path = os.path.join(output_folder, f"Rainfall_{file_tag}.csv")
            df_rain.to_csv(rain_csv_path, index=False)
            feedback.pushInfo(f"Rainfall CSV  → {rain_csv_path}")

            # TUFLOW rainfall boundary input — Time (hr), Rainfall (mm), same
            # 10-min interval, no header extras.
            df_tuflow = pd.DataFrame({
                'Time_hr':     np.round(rt, 4),
                'Rainfall_mm': np.round(ri, 4),
            })
            tuflow_csv_path = os.path.join(output_folder, f"RainfallForTUFLOWInput_{file_tag}.csv")
            df_tuflow.to_csv(tuflow_csv_path, index=False)
            feedback.pushInfo(f"TUFLOW Rainfall CSV  → {tuflow_csv_path}")

        # ── Iterate subcatchments ─────────────────────────────────────────
        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(70 * i / max(total_features, 1)))

            catch_id = (
                str(feat.attribute(field_id_idx)).strip()
                if field_id_idx >= 0
                else f"SC{feat.id()}"
            )

            try:
                area_ha = float(feat[field_area])
                toc_min = max(float(feat[field_toc]), 10.0)
                cn      = float(feat[field_cn])
                imp_pct = float(feat[field_imp])
            except (TypeError, ValueError):
                feedback.pushWarning(f"  {catch_id}: null or invalid attribute — skipped.")
                continue

            time_axis, total_flow, flow_imp, flow_perv = self._calc_hydrograph(
                rain_inc, cum_rain, area_ha, imp_pct, cn, toc_min
            )

            peak_flow = float(np.max(total_flow))
            total_vol = float(np.sum(total_flow) * 600.0)

            all_hydrographs[catch_id] = (time_axis, total_flow, flow_imp, flow_perv)

            if save_to_input:
                attr_changes[feat.id()] = {
                    pk_idx: round(peak_flow, 4),
                    vl_idx: round(total_vol, 1),
                }

            if create_layer and sink is not None:
                out_feat = QgsFeature(out_fields)
                out_feat.setGeometry(feat.geometry())
                for j in range(src_field_count):
                    out_feat.setAttribute(j, feat.attribute(j))
                out_feat.setAttribute('PeakFlow_m3s', round(peak_flow, 4))
                out_feat.setAttribute('TotalVol_m3',  round(total_vol, 1))
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            feedback.pushInfo(
                f"  {catch_id}: Area={area_ha:.1f}ha  ToC={toc_min:.0f}min  "
                f"CN={cn:.0f}  Imp={imp_pct:.1f}%  "
                f"Peak={peak_flow:.3f} m³/s  Vol={total_vol:,.0f} m³"
            )

            if has_mpl:
                try:
                    self._plot_subcatchment(
                        catch_id, time_axis, total_flow, flow_imp, flow_perv,
                        rain_inc, rainfall_depth, aep_str, cc_str, pct_increase,
                        future_depth, storm_label, event_date,
                        area_ha, peak_flow, total_vol, output_folder, file_tag
                    )
                except Exception as exc:
                    feedback.pushWarning(f"    Plot failed for {catch_id}: {exc}")

        # ── Write back (mode 1) ───────────────────────────────────────────
        if save_to_input and attr_changes:
            source.dataProvider().changeAttributeValues(attr_changes)
            source.triggerRepaint()
            feedback.pushInfo(
                f"Results written back to input layer ({len(attr_changes)} features updated)."
            )

        # ── Export hydrograph CSVs ────────────────────────────────────────
        if all_hydrographs:
            feedback.setProgress(85)

            # Pad all series to the same length for the combined output
            max_len     = max(len(t) for t, _, _, _ in all_hydrographs.values())
            common_time = np.arange(max_len) * (10.0 / 60.0)
            time_labels = [self._hr_to_hhmmss(h) for h in common_time]

            df_combined = pd.DataFrame({'Time': time_labels, 'Time_hr': np.round(common_time, 4)})
            for cid, (t, tf, _, _) in all_hydrographs.items():
                col = np.zeros(max_len)
                col[:len(tf)] = tf
                df_combined[f'{cid}_m3s'] = np.round(col, 5)
            combined_path = os.path.join(output_folder, f"Hydrograph_{file_tag}_All.csv")
            df_combined.to_csv(combined_path, index=False)
            feedback.pushInfo(f"Combined CSV  → {combined_path}")

            n = len(all_hydrographs)
            for j, (cid, (t, tf, fi, fp)) in enumerate(all_hydrographs.items()):
                feedback.setProgress(85 + int(15 * j / max(n, 1)))
                df_ind = pd.DataFrame({
                    'Time':           [self._hr_to_hhmmss(h) for h in t],
                    'Time_hr':        np.round(t, 4),
                    'Total_Flow_m3s': np.round(tf, 5),
                    'Imp_Flow_m3s':   np.round(fi, 5),
                    'Perv_Flow_m3s':  np.round(fp, 5),
                })
                df_ind.to_csv(
                    os.path.join(output_folder, f"Hydrograph_{cid}_{file_tag}.csv"),
                    index=False
                )
            feedback.pushInfo(f"Individual CSVs ({n} subcatchments) → {output_folder}")

        feedback.setProgress(100)
        return {self.OUTPUT_LAYER: sink_id} if sink_id else {}
