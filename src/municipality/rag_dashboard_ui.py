from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode


def render_rag_dashboard_page(initial_gis_map_payload: dict[str, Any] | None = None) -> str:
    html = """
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>לוח מחוונים עירוני</title>
  <style>
    :root {
      --page-bg: #f7f9fc;
      --surface: #ffffff;
      --surface-soft: #fbfcfe;
      --line: #dfe7ef;
      --line-strong: #cfdbe8;
      --text: #141925;
      --muted: #677487;
      --blue: #0b68d1;
      --blue-soft: #eaf3ff;
      --purple: #6f46d9;
      --purple-soft: #f1ebff;
      --green: #11a866;
      --orange: #f07a28;
      --warning: #f59e0b;
      --shadow: 0 10px 28px rgba(26, 39, 61, 0.08);
      --shadow-soft: 0 4px 14px rgba(26, 39, 61, 0.08);
      --font-he: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans Hebrew", Arial, sans-serif;
    }

    * { box-sizing: border-box; }

    html, body { min-height: 100%; }

    body {
      margin: 0;
      background: var(--page-bg);
      color: var(--text);
      font-family: var(--font-he);
      font-size: 14px;
      line-height: 1.5;
    }

    button, input, a { font: inherit; }

    button, a, input { -webkit-tap-highlight-color: transparent; }

    button:focus-visible,
    a:focus-visible,
    input:focus-visible,
    [tabindex]:focus-visible {
      outline: 3px solid rgba(111, 70, 217, 0.28);
      outline-offset: 2px;
    }

    .appShell {
      min-height: 100vh;
      padding: 18px 14px 16px;
      background:
        radial-gradient(900px 340px at 68% -120px, rgba(223, 236, 255, 0.74), transparent 70%),
        linear-gradient(180deg, #fbfcff 0%, #f6f8fb 100%);
    }

    .SearchHeader {
      display: flex;
      align-items: center;
      gap: 14px;
      min-height: 72px;
      margin: 0 auto 14px;
      width: min(100%, 1520px);
    }

    .municipalityBrand {
      display: inline-flex;
      align-items: center;
      gap: 14px;
      flex: 0 0 auto;
      min-width: 224px;
      padding-inline: 8px 2px;
      color: #1265c9;
    }

    .brandWord {
      font-size: 40px;
      line-height: 1;
      font-weight: 850;
      letter-spacing: -0.04em;
    }

    .crest {
      width: 48px;
      height: 56px;
      flex: 0 0 auto;
      filter: drop-shadow(0 2px 2px rgba(20, 57, 111, 0.16));
    }

    .questionSearch {
      position: relative;
      flex: 1 1 auto;
      min-width: 420px;
      height: 62px;
      display: flex;
      align-items: center;
    }

    .questionSearch input {
      width: 100%;
      height: 62px;
      border: 1px solid var(--line-strong);
      border-radius: 10px;
      background: #fff;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.9), 0 1px 2px rgba(21, 31, 52, 0.03);
      color: #111827;
      direction: rtl;
      text-align: right;
      font-size: 22px;
      font-weight: 450;
      padding-block: 0;
      padding-inline-start: 24px;
      padding-inline-end: 58px;
      line-height: normal;
      appearance: none;
    }

    .questionSearch input::-webkit-search-cancel-button { appearance: none; }

    .searchSubmit {
      position: absolute;
      inset-inline-end: 12px;
      top: 50%;
      width: 38px;
      height: 38px;
      transform: translateY(-50%);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: #23324a;
      cursor: pointer;
    }

    .searchSubmit:hover { background: #f3f6fb; }

    .headerButton {
      height: 52px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding-inline: 18px;
      border: 1px solid var(--line-strong);
      border-radius: 10px;
      background: #fff;
      color: #121826;
      box-shadow: 0 1px 2px rgba(21, 31, 52, 0.03);
      cursor: pointer;
      white-space: nowrap;
      flex: 0 0 auto;
      font-weight: 700;
    }

    .headerButton:hover { background: #f8fafc; }

    .headerButton.popularButton {
      min-width: 188px;
      border-color: #9c7afa;
      color: var(--purple);
      background: #fff;
    }

    .headerButton.filterButton { min-width: 138px; }

    .filterCountBadge {
      min-width: 20px;
      height: 20px;
      display: none;
      align-items: center;
      justify-content: center;
      padding-inline: 6px;
      border-radius: 999px;
      background: var(--purple);
      color: #fff;
      font-size: 12px;
      font-weight: 900;
      line-height: 1;
    }

    .filterCountBadge.isVisible { display: inline-flex; }

    .headerButton.adminButton {
      min-width: 120px;
      padding-inline: 15px;
    }

    .icon {
      width: 18px;
      height: 18px;
      display: block;
      flex: 0 0 auto;
      stroke-width: 2;
    }

    .iconFill { fill: currentColor; stroke: none; }

    .dashboardGrid {
      direction: ltr;
      display: grid;
      grid-template-columns: 370px minmax(0, 1fr) 286px;
      gap: 12px;
      width: min(100%, 1520px);
      margin: 0 auto;
      height: calc(100vh - 106px);
      min-height: 760px;
    }

    .endDetailDrawer,
    .mainCivicWorkspace,
    .startDiscoveryPanel {
      direction: rtl;
    }

    .panelCard {
      background: rgba(255,255,255,0.98);
      border: 1px solid var(--line);
      border-radius: 6px;
      box-shadow: var(--shadow-soft);
    }

    .endDetailDrawer {
      grid-column: 1;
      padding: 22px 20px 18px;
      overflow: auto;
    }

    .mainCivicWorkspace {
      grid-column: 2;
      display: grid;
      grid-template-rows: minmax(0, 1fr) 218px;
      gap: 12px;
      min-width: 0;
      min-height: 0;
    }

    .startDiscoveryPanel {
      grid-column: 3;
      padding: 14px 14px;
      overflow: auto;
    }

    .drawerTop {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
      direction: ltr;
    }

    .drawerClose {
      width: 36px;
      height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 0;
      background: transparent;
      color: #111827;
      cursor: pointer;
      border-radius: 8px;
    }

    .drawerClose:hover { background: #f4f6fa; }

    .detailTitle {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 7px;
      min-width: 0;
      direction: rtl;
    }

    .detailTitle h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
      font-weight: 850;
      letter-spacing: -0.02em;
    }

    .sparkleIcon {
      width: 20px;
      height: 20px;
      color: var(--purple);
      display: block;
      flex: 0 0 auto;
      transform: translateY(-1px);
    }

    .questionLine {
      margin: 0 0 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      color: #0f172a;
      text-align: start;
    }

    .answerSection {
      padding-block: 12px;
      border-bottom: 1px solid var(--line);
    }

    .answerSection:first-of-type { padding-top: 0; }

    .answerSection:last-child { border-bottom: 0; padding-bottom: 0; }

    .sectionTitle {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 9px;
      font-size: 17px;
      line-height: 1.25;
      font-weight: 850;
      letter-spacing: -0.02em;
    }

    .sectionText {
      margin: 0;
      color: #172033;
      line-height: 1.72;
      text-align: start;
    }

    .mapLayerSummary {
      display: grid;
      gap: 7px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .mapLayerSummaryRow {
      display: grid;
      grid-template-columns: 12px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 28px;
      padding: 5px 7px;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      background: #fbfdff;
      color: #253047;
      font-size: 12px;
      font-weight: 760;
    }

    .mapLayerSummarySwatch {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: #94a3b8;
      box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.15);
    }

    .mapLayerSummaryRow[data-layer="selected_parcel"] .mapLayerSummarySwatch { background: #7c4dce; }
    .mapLayerSummaryRow[data-layer="nearby_parcels"] .mapLayerSummarySwatch { background: #9a8864; }
    .mapLayerSummaryRow[data-layer="transport"] .mapLayerSummarySwatch { background: #0b68d1; }
    .mapLayerSummaryRow[data-layer="schools"] .mapLayerSummarySwatch { background: #f59e0b; }
    .mapLayerSummaryRow[data-layer="osm"] .mapLayerSummarySwatch { background: #64748b; }

    .mapLayerSummaryCount {
      min-width: 24px;
      padding-inline: 6px;
      border-radius: 999px;
      background: #eff6ff;
      color: #0b68d1;
      font-size: 11px;
      font-weight: 900;
      text-align: center;
      direction: ltr;
    }

    .mapLayersSection {
      position: sticky;
      top: 0;
      z-index: 5;
      margin-top: 10px;
      border: 1px solid #dfe7ef;
      border-radius: 10px;
      padding: 10px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 8px 22px rgba(21, 31, 52, 0.08);
    }

    .confidenceRow {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 12px;
      color: #111827;
    }

    .infoDot {
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: #c9d1dc;
      color: #fff;
      font-size: 12px;
      font-weight: 800;
    }

    .confidenceBadge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: #0f9d58;
      font-weight: 800;
    }

    .confidenceDot {
      width: 16px;
      height: 16px;
      border-radius: 999px;
      background: rgba(17, 168, 102, 0.18);
      border: 5px solid #18ad6b;
      display: inline-block;
    }

    .decisionList {
      display: grid;
      gap: 12px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .decisionRow {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      column-gap: 8px;
      align-items: start;
      direction: rtl;
    }

    .decisionNumber {
      min-width: 18px;
      color: #111827;
      font-weight: 850;
      line-height: 1.55;
    }

    .decisionText {
      min-width: 0;
      line-height: 1.58;
      color: #111827;
      text-align: start;
    }

    .sourceLink {
      white-space: nowrap;
      align-self: start;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      min-height: 22px;
      padding-inline: 2px;
      color: var(--purple);
      font-weight: 700;
      text-decoration: none;
      border-bottom: 1px solid rgba(111, 70, 217, 0.38);
      font-size: 13px;
    }

    .sourceLink:hover { border-bottom-color: var(--purple); }

    .relatedChips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 4px;
    }

    .topicChip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 35px;
      max-width: 100%;
      padding-inline: 13px;
      border: 1px solid #b99df9;
      border-radius: 6px;
      background: #fff;
      color: var(--purple);
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      line-height: 1.2;
    }

    .limitationsTitle {
      color: #111827;
      margin-bottom: 7px;
    }

    .warningIcon { color: #f08b21; }

    .mapPanel {
      position: relative;
      min-height: 0;
      overflow: hidden;
      border-radius: 6px;
      background: #eef4f7;
    }

    .mapZoomControls {
      position: absolute;
      top: 12px;
      left: 12px;
      z-index: 3000;
      display: grid;
      gap: 8px;
      direction: ltr;
      pointer-events: auto;
    }

    .mapZoomControlGroup {
      display: grid;
      overflow: hidden;
      border: 1px solid #d7e1eb;
      border-radius: 6px;
      background: #fff;
      box-shadow: 0 3px 10px rgba(25, 43, 67, 0.18);
    }

    .mapZoomButton {
      width: 40px;
      height: 40px;
      border: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-bottom: 1px solid #e8edf3;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-size: 24px;
      font-weight: 950;
      line-height: 1;
      user-select: none;
    }

    .mapZoomButton:last-child { border-bottom: 0; }
    .mapZoomButton:hover { background: #f1f5f9; }

    .mapZoomIcon {
      width: 18px;
      height: 18px;
      display: block;
    }

    .mapLayerPopover {
      position: absolute;
      left: 12px;
      top: 150px;
      z-index: 3200;
      width: min(310px, calc(100% - 28px));
      max-height: min(78%, 520px);
      overflow: auto;
      padding: 12px 12px 11px;
      border: 1px solid rgba(215, 225, 235, 0.95);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 8px 22px rgba(23, 36, 55, 0.16);
      direction: rtl;
      color: #1f2937;
    }

    #map-view-layers.mapZoomButton {
      width: auto;
      min-width: 96px;
      gap: 7px;
      padding-inline: 10px;
      direction: rtl;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }

    .mapLayerPopover[hidden] { display: none; }

    .mapRendererStatus {
      position: absolute;
      right: 12px;
      bottom: 12px;
      z-index: 2800;
      padding: 5px 8px;
      border: 1px solid rgba(203, 213, 225, 0.86);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.92);
      color: #334155;
      font-size: 11px;
      font-weight: 850;
      direction: rtl;
      box-shadow: 0 4px 12px rgba(15, 23, 42, 0.12);
    }

    .mapLayerPopover h3 {
      margin: 0 0 9px;
      font-size: 14px;
      font-weight: 900;
      text-align: center;
    }

    .mapLayerPopoverTop {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 9px;
    }

    .mapLayerPopoverTop h3 { margin: 0; }

    .mapLayerPopoverClose {
      width: 28px;
      height: 28px;
      border: 1px solid #d8e1ec;
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-size: 18px;
      font-weight: 900;
      line-height: 1;
    }

    .mapLayerPopoverClose:hover { background: #f1f5f9; }

    .mapLayerPopoverSectionTitle {
      margin: 11px 0 7px;
      padding-top: 9px;
      border-top: 1px solid rgba(226, 232, 240, 0.9);
      color: #475569;
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.02em;
    }

    .mapLayerTools {
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
      padding: 9px;
      border: 1px solid rgba(226, 232, 240, 0.95);
      border-radius: 8px;
      background: rgba(248, 250, 252, 0.92);
    }

    .mapIconSizeControl {
      display: grid;
      gap: 5px;
      color: #334155;
      font-size: 12px;
      font-weight: 850;
    }

    .mapIconSizeControl input { width: 100%; }

    .mapLayerToolButton {
      min-height: 34px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-size: 12px;
      font-weight: 900;
    }

    .mapLayerToolButton:hover { background: #f1f5f9; }

    .mapLayerLegendList.compact {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px 8px;
    }

    .mapLayerLegendList {
      display: grid;
      gap: 7px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .mapLayerLegendList li {
      font-size: 12px;
      font-weight: 780;
      color: #334155;
    }

    .mapLayerLegendList label {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }

    .mapLayerLegendList input {
      margin: 0;
      accent-color: #0b68d1;
    }

    .mapLayerLegendSwatch {
      width: 17px;
      height: 17px;
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 5px;
      border: 2px solid currentColor;
      background: rgba(255,255,255,0.72);
    }

    .mapLayerLegendSwatch.parcel { color: #14532d; background: rgba(20,83,45,0.16); }
    .mapLayerLegendSwatch.nearby { color: #9a8864; background: rgba(245,238,220,0.75); }
    .mapLayerLegendSwatch.transport { color: #0b68d1; border-radius: 999px; background: #0b68d1; }
    .mapLayerLegendSwatch.school { color: #f59e0b; border-radius: 999px; background: #f59e0b; }
    .mapLayerLegendSwatch.municipal { color: #dc2626; border-radius: 999px; background: #dc2626; }
    .mapLayerLegendSwatch.osm { color: #64748b; border-radius: 999px; background: #64748b; }
    .mapLayerLegendSwatch.address { color: #9333ea; border-radius: 999px; background: #9333ea; }
    .mapLayerLegendSwatch.park { color: #22c55e; background: rgba(34,197,94,0.22); }
    .mapLayerLegendSwatch.beach { color: #0ea5e9; background: rgba(14,165,233,0.24); }
    .mapLayerLegendSwatch.bike { color: #06b6d4; background: rgba(6,182,212,0.18); }
    .mapLayerLegendSwatch.road { color: #94a3b8; background: rgba(148,163,184,0.18); }

    .mapFrame {
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 0;
      direction: ltr;
      overflow: hidden;
      border-radius: inherit;
      background: linear-gradient(90deg, #bde7f7 0%, #dff6fb 18%, #f5f7f6 29%, #f8faf8 100%);
      cursor: grab;
      touch-action: none;
      user-select: none;
    }

    .mapFrame.isPanning { cursor: grabbing; }

    .mapFrame.hasRealGis {
      cursor: grab;
      touch-action: none;
      user-select: none;
    }

    .mapFrame.maplibreActive {
      cursor: auto;
      touch-action: auto;
      user-select: auto;
    }

    .schematicMapSvg {
      width: 100%;
      height: 100%;
      display: block;
    }

    .realGisMap {
      position: absolute;
      inset: 0;
      z-index: 1;
      background: #e7efe9;
    }

    .realGisMap[hidden] { display: none; }

    .realGisStaticMap {
      position: absolute;
      inset: 0;
      z-index: 1;
      width: 100%;
      height: 100%;
      display: block;
      background:
        radial-gradient(circle at 18% 22%, rgba(186, 230, 253, 0.34), transparent 28%),
        linear-gradient(115deg, rgba(219, 234, 254, 0.54), transparent 34%),
        #edf2ee;
      transform-origin: 50% 50%;
      transition: transform 180ms ease;
    }

    .realGisStaticMap[hidden] { display: none; }

    .mapFrame.hasRealGis .schematicMapSvg { display: none; }

    .mapFrame.hasRealGis .mapExampleSelector,
    .mapFrame.hasRealGis .mapProvenanceBadge { display: none; }

    .mapFrame.hasRealGis .mapControls {
      top: 12px;
      left: 12px;
      z-index: 1000;
      gap: 8px;
      pointer-events: auto;
    }

    .mapFrame.maplibreActive .maplibregl-ctrl-top-left {
      top: 12px;
      left: 58px;
    }

    .mapFrame.hasRealGis .mapProvenanceBadge {
      border-color: rgba(20, 83, 45, 0.34);
      background: rgba(240, 253, 244, 0.95);
      color: #14532d;
    }

    .mapFrame.hasRealGis .mapProvenanceBadgeTitle::before {
      background: #16a34a;
      box-shadow: 0 0 0 4px rgba(22, 163, 74, 0.16);
    }

    .mapFrame.hasRealGis .mapProvenanceBadgeText { color: #166534; }

    .realGisTooltip {
      direction: rtl;
      text-align: right;
      font-family: var(--font-he);
      line-height: 1.45;
    }

    .realGisTooltip strong { display: block; margin-bottom: 3px; }

    .realGisStaticFeature { cursor: pointer; }

    #real-gis-static-map [data-map-layer]:not([data-unfilterable-raster]),
    #real-gis-static-map [data-map-icon],
    .schematicMapSvg [data-map-tooltip-title],
    .schematicMapSvg [role="button"][aria-label] { cursor: pointer; }

    .realGisStaticFeature:hover { filter: drop-shadow(0 0 5px rgba(11, 104, 209, 0.38)); }

    .mapFrame.isPanning .realGisStaticFeature {
      pointer-events: none;
      filter: none !important;
    }

    .realGisStaticLabel {
      pointer-events: none;
      paint-order: normal;
      stroke: none;
      font-weight: 800;
      fill: #172033;
      text-anchor: middle;
      direction: rtl;
    }

    .realGisHoverCard {
      position: absolute;
      z-index: 30;
      max-width: 240px;
      padding: 7px 9px;
      border: 1px solid rgba(203, 213, 225, 0.92);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 10px 24px rgba(15, 23, 42, 0.18);
      color: #172033;
      direction: rtl;
      font-size: 11px;
      line-height: 1.35;
      pointer-events: none;
    }

    .leaflet-container {
      font-family: var(--font-he);
      background: #e7efe9;
    }

    .leaflet-control-attribution {
      direction: ltr;
      font-size: 10px;
    }

    .maplibregl-map {
      font-family: var(--font-he);
      background: #e7efe9;
    }

    .maplibregl-ctrl-attrib {
      direction: ltr;
      font-size: 10px;
    }

    .gisMapLabel {
      direction: rtl;
      padding: 3px 7px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.68);
      color: #182033;
      font-family: var(--font-he);
      font-size: 13px;
      font-weight: 900;
      text-shadow: 0 1px 0 #fff;
      box-shadow: 0 1px 8px rgba(20, 31, 48, 0.08);
      pointer-events: none;
      white-space: nowrap;
    }

    .gisMapSeaLabel {
      background: transparent;
      color: #0b68d1;
      box-shadow: none;
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.72);
    }

    .gisMapMarker {
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 2px solid #fff;
      border-radius: 999px;
      color: #fff;
      box-shadow: 0 4px 10px rgba(22, 33, 54, 0.16), 0 0 0 3px rgba(255, 255, 255, 0.22);
      cursor: pointer;
    }

    .gisMapMarker svg {
      width: 11px;
      height: 11px;
      display: block;
      stroke-width: 2.2;
    }

    .gisMapMarker[data-kind="transport_stop"] { background: #0b68d1; }
    .gisMapMarker[data-kind="school"] { background: #f59e0b; }
    .gisMapMarker[data-kind="context_poi"] { background: #18a865; }
    .gisMapMarker[data-kind="building"] { background: #f97316; }

    .sourceBadge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 24px;
      padding-inline: 9px;
      border: 1px solid #d8e1ec;
      border-radius: 999px;
      background: #f8fafc;
      color: #334155;
      font-size: 11px;
      font-weight: 850;
      white-space: nowrap;
    }

    .sourceBadge::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: #94a3b8;
      flex: 0 0 auto;
    }

    .sourceBadge[data-status="official"] { border-color: #99d4b4; background: #eefdf4; color: #14532d; }
    .sourceBadge[data-status="official"]::before { background: #16a34a; }
    .sourceBadge[data-status="official_with_caveat"] { border-color: #f5cf8a; background: #fff7e6; color: #7c4a03; }
    .sourceBadge[data-status="official_with_caveat"]::before { background: #f59e0b; }
    .sourceBadge[data-status="municipal_open"] { border-color: #93c5fd; background: #eff6ff; color: #1d4ed8; }
    .sourceBadge[data-status="municipal_open"]::before { background: #2563eb; }
    .sourceBadge[data-status="municipal_license_under_review"] { border-color: #c4b5fd; background: #f5f3ff; color: #6d28d9; }
    .sourceBadge[data-status="municipal_license_under_review"]::before { background: #8b5cf6; }
    .sourceBadge[data-status="context_only"] { border-color: #cbd5e1; background: #f1f5f9; color: #475569; }
    .sourceBadge[data-status="context_only"]::before { background: #64748b; }
    .sourceBadge[data-status="temporary_input_only"] { border-color: #fecaca; background: #fff1f2; color: #be123c; }
    .sourceBadge[data-status="temporary_input_only"]::before { background: #e11d48; }
    .sourceBadge[data-status="unavailable"] { border-color: #e2e8f0; background: #f8fafc; color: #64748b; }
    .sourceBadge[data-status="unavailable"]::before { background: #94a3b8; }

    .mapInfoPanel,
    .coveragePanel {
      position: absolute;
      z-index: 5;
      direction: rtl;
      border: 1px solid rgba(207, 219, 232, 0.95);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 12px 32px rgba(21, 31, 52, 0.18);
      color: #172033;
    }

    .mapInfoPanel[hidden],
    .coveragePanel[hidden] { display: none; }

    .mapInfoPanel {
      right: 12px;
      bottom: 16px;
      width: min(390px, calc(100% - 28px));
      max-height: min(72%, 520px);
      overflow: auto;
      padding: 14px;
    }

    .coveragePanel {
      left: 14px;
      top: 74px;
      width: min(520px, calc(100% - 28px));
      max-height: min(68%, 480px);
      overflow: auto;
      padding: 14px;
    }

    .mapInfoTop,
    .coverageTop {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }

    .mapInfoTop h3,
    .coverageTop h3 {
      margin: 0;
      font-size: 16px;
      font-weight: 900;
    }

    .mapPanelClose {
      width: 30px;
      height: 30px;
      border: 1px solid #d8e1ec;
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-weight: 900;
    }

    .pointReportSections {
      display: grid;
      gap: 10px;
    }

    .pointReportSection {
      display: grid;
      gap: 7px;
      padding: 10px;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      background: #fbfdff;
    }

    .pointReportSection h4 {
      margin: 0;
      font-size: 13px;
      font-weight: 900;
    }

    .pointReportMessage {
      margin: 0;
      color: #64748b;
      font-size: 12px;
      line-height: 1.45;
    }

    .pointFeatureList {
      display: grid;
      gap: 6px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .pointFeatureItem {
      display: grid;
      gap: 5px;
      padding: 8px;
      border-radius: 8px;
      background: #fff;
      border: 1px solid #edf2f7;
      font-size: 12px;
    }

    .pointFeatureItem.compact {
      color: #64748b;
      font-weight: 850;
      text-align: center;
    }

    .coverageGrid {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }

    .coverageGrid th,
    .coverageGrid td {
      border-bottom: 1px solid #e2e8f0;
      padding: 7px 8px;
      text-align: start;
      vertical-align: top;
    }

    .coverageGrid th { color: #334155; font-weight: 900; }

    .statusLegendList {
      display: grid;
      gap: 7px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .mapControls {
      position: absolute;
      top: 12px;
      left: 12px;
      display: none;
      gap: 10px;
      z-index: 1000;
    }

    .mapExampleSelector {
      position: absolute;
      top: 12px;
      left: 76px;
      z-index: 4;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 42px;
      padding-inline: 10px;
      border: 1px solid #d7e1eb;
      border-radius: 10px;
      background: rgba(255,255,255,0.96);
      box-shadow: 0 4px 11px rgba(25, 43, 67, 0.14);
      direction: rtl;
      color: #172033;
      font-weight: 850;
    }

    .mapExampleSelector select {
      height: 30px;
      border: 1px solid #d8e1ec;
      border-radius: 8px;
      background: #fff;
      color: #111827;
      font-weight: 800;
      direction: rtl;
    }

    .mapControlGroup {
      display: grid;
      overflow: hidden;
      border: 1px solid #d7e1eb;
      border-radius: 7px;
      background: #fff;
      box-shadow: 0 4px 11px rgba(25, 43, 67, 0.14);
    }

    .mapControlButton {
      width: 38px;
      height: 38px;
      border: 0;
      border-bottom: 1px solid #e8edf3;
      background: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: #111827;
      cursor: pointer;
      pointer-events: auto;
    }

    .mapControlButton:last-child { border-bottom: 0; }
    .mapControlButton:hover { background: #f7f9fc; }

    .mapControlButtonText {
      color: #0f172a;
      font-size: 24px;
      font-weight: 950;
      line-height: 1;
    }

    .mapProvenanceBadge {
      position: absolute;
      top: 12px;
      right: 12px;
      z-index: 4;
      max-width: min(360px, calc(100% - 86px));
      padding: 10px 12px 11px;
      border: 1px solid rgba(160, 117, 23, 0.38);
      border-radius: 10px;
      background: rgba(255, 251, 235, 0.94);
      box-shadow: 0 8px 22px rgba(23, 36, 55, 0.14);
      color: #623d05;
      direction: rtl;
    }

    .mapProvenanceBadgeTitle {
      display: flex;
      align-items: center;
      gap: 7px;
      margin: 0 0 3px;
      font-size: 13px;
      font-weight: 900;
      line-height: 1.25;
    }

    .mapProvenanceBadgeTitle::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #d97706;
      box-shadow: 0 0 0 4px rgba(217, 119, 6, 0.16);
      flex: 0 0 auto;
    }

    .mapProvenanceBadgeText {
      margin: 0;
      font-size: 12px;
      line-height: 1.45;
      color: #7c4a03;
    }

    .legendCard {
      position: absolute;
      left: 14px;
      bottom: 16px;
      z-index: 3;
      width: 158px;
      padding: 14px 12px;
      border: 1px solid #d9e1ea;
      border-radius: 10px;
      background: rgba(255,255,255,0.94);
      box-shadow: 0 8px 22px rgba(23, 36, 55, 0.16);
      direction: rtl;
    }

    .mapFrame.hasRealGis .legendCard {
      display: block;
      z-index: 12;
      padding: 12px 11px;
      border-color: rgba(209, 219, 228, 0.9);
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(10px);
    }

    .mapFrame.hasRealGis .statusLegendList { display: none; }

    .mapFrame.hasRealGis .legendTitle {
      margin: 0 0 5px;
      font-size: 10px;
      text-align: start;
    }

    .mapFrame.hasRealGis .legendList {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 7px;
      margin-block-start: 6px;
    }

    .legendTitle {
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 850;
      text-align: center;
    }

    .layerCountsList {
      display: grid;
      gap: 5px;
      margin: 0 0 12px;
      padding: 0 0 10px;
      border-bottom: 1px solid #e2e8f0;
      list-style: none;
    }

    .mapFrame.hasRealGis .layerCountsList {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin: 0;
      padding: 0;
      border-bottom: 0;
    }

    .layerCountsList li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #334155;
      font-size: 11px;
      font-weight: 800;
    }

    .mapFrame.hasRealGis .layerCountsList li {
      gap: 5px;
      padding: 3px 6px;
      border: 1px solid #e2e8f0;
      border-radius: 999px;
      background: rgba(248, 250, 252, 0.92);
      font-size: 10px;
      line-height: 1.1;
    }

    .layerCountsList strong {
      min-width: 26px;
      padding-inline: 6px;
      border-radius: 999px;
      background: #eff6ff;
      color: #0b68d1;
      direction: ltr;
      text-align: center;
    }

    .mapFrame.hasRealGis .layerCountsList strong {
      min-width: auto;
      padding-inline: 5px;
      font-size: 10px;
    }

    .legendList {
      display: grid;
      gap: 9px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .legendItem {
      display: grid;
      grid-template-columns: 22px 1fr;
      align-items: center;
      gap: 8px;
      color: #253047;
      font-size: 12px;
    }

    .mapFrame.hasRealGis .legendItem {
      gap: 4px;
      font-size: 10px;
      line-height: 1.1;
      white-space: nowrap;
    }

    .legendSwatch {
      width: 18px;
      height: 18px;
      border-radius: 4px;
      display: inline-block;
      justify-self: center;
    }

    .mapFrame.hasRealGis .legendSwatch {
      width: 9px;
      height: 9px;
    }

    .legendSwatch.area { background: #efe7ff; border: 2px solid #7a4de8; }
    .legendSwatch.plan { background: #dff7e8; border: 2px solid #22a06b; border-radius: 999px; }
    .legendSwatch.neighborhood { background: #cbd5e1; border: 2px solid #94a3b8; border-radius: 999px; }
    .legendSwatch.transit { border-radius: 999px; background: #1377d4; position: relative; }
    .legendSwatch.school { border-radius: 999px; background: #f59e0b; }
    .legendSwatch.building { background: rgba(249, 115, 22, 0.22); border: 2px solid #f97316; }
    .legendSwatch.interest { border-radius: 999px; background: #64748b; }

    .timelinePanel {
      padding: 18px 22px 16px;
      min-width: 0;
    }

    .timelineHeader {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      margin-bottom: 13px;
    }

    .timelineHeader h2 {
      margin: 0;
      font-size: 17px;
      line-height: 1.25;
      font-weight: 850;
    }

    .timelineBody {
      position: relative;
      direction: ltr;
      padding-inline: 42px;
    }

    .timelineCards {
      display: grid;
      grid-template-columns: repeat(5, minmax(116px, 1fr));
      gap: 36px;
      align-items: stretch;
    }

    .timelineCard {
      min-height: 92px;
      border: 1px solid #dfe6ef;
      border-radius: 7px;
      background: #fff;
      padding: 12px 10px 10px;
      text-align: center;
      box-shadow: 0 3px 8px rgba(21, 31, 52, 0.04);
      direction: rtl;
    }

    .timelineCard.selected {
      border-color: #8b62ef;
      background: #f3edff;
      box-shadow: 0 0 0 2px rgba(139, 98, 239, 0.10);
    }

    .timelineCard.approved { border-color: #7fd7ae; background: #f0fff7; }
    .timelineCard.public { border-color: #b9d7ff; background: #eff7ff; }

    .timelineDate {
      display: block;
      margin-bottom: 7px;
      color: #273248;
      font-weight: 500;
      direction: ltr;
    }

    .timelineCard.selected .timelineDate { color: var(--purple); font-weight: 850; }
    .timelineCard.public .timelineDate { color: #0b68d1; font-weight: 850; }

    .timelineLabel {
      display: block;
      font-weight: 850;
      color: #111827;
      line-height: 1.38;
      font-size: 13px;
    }

    .timelineSub {
      display: block;
      color: #4b5563;
      line-height: 1.4;
      margin-top: 2px;
      font-size: 12px;
    }

    .timelineRail {
      position: relative;
      height: 42px;
      margin-top: 7px;
    }

    .timelineRail::before {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 20px;
      height: 2px;
      background: #a9cbfa;
    }

    .timelineNode {
      position: absolute;
      top: 14px;
      width: 14px;
      height: 14px;
      transform: translateX(-50%);
      border-radius: 999px;
      border: 2px solid #8a99aa;
      background: #fff;
      z-index: 1;
    }

    .timelineNode.selected {
      top: 10px;
      width: 22px;
      height: 22px;
      border: 4px solid var(--purple);
      background: #fff;
      box-shadow: 0 0 0 2px rgba(111, 70, 217, 0.14);
    }

    .timelineArrow {
      position: absolute;
      top: 110px;
      width: 38px;
      height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #dce4ee;
      border-radius: 7px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      box-shadow: 0 2px 6px rgba(21, 31, 52, 0.04);
      z-index: 2;
    }

    .timelineArrow:hover { background: #f8fafc; }
    .timelineArrow.prev { left: 0; }
    .timelineArrow.next { right: 0; }

    .discoverySection {
      padding-bottom: 12px;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--line);
    }

    .discoverySection:last-child { border-bottom: 0; margin-bottom: 0; padding-bottom: 0; }

    .panelHeading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 12px;
    }

    .panelHeading h2 {
      margin: 0;
      font-size: 16px;
      font-weight: 850;
      letter-spacing: -0.02em;
    }

    .rowList {
      display: grid;
      gap: 6px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .discoveryRow,
    .treeRow {
      width: 100%;
      min-height: 35px;
      display: grid;
      grid-template-columns: 22px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      border: 1px solid #e0e7ef;
      border-radius: 6px;
      background: #fff;
      color: #111827;
      padding: 4px 8px;
      text-align: start;
      cursor: pointer;
      font-weight: 700;
    }

    .discoveryRow:hover,
    .treeRow:hover { background: #f8fafc; }

    .discoveryRow.selected,
    .treeRow.selected {
      border-color: #b89df7;
      background: linear-gradient(90deg, #f3ecff 0%, #f8f4ff 100%);
      color: var(--purple);
    }

    .discoveryRow.topicRow {
      grid-template-columns: minmax(0, 1fr) auto;
    }

    .rowLabel {
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      min-width: 0;
    }

    .countPill {
      min-width: 42px;
      height: 26px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #d5dce8;
      border-radius: 999px;
      background: #fff;
      color: #243044;
      font-weight: 600;
      direction: ltr;
      justify-self: end;
    }

    .selected .countPill {
      border-color: #b99df9;
      color: var(--purple);
      background: #fff;
    }

    .showMoreButton,
    .showTreeButton {
      width: 100%;
      min-height: 36px;
      margin-top: 8px;
      border: 1px solid #e0e7ef;
      border-radius: 6px;
      background: #fff;
      color: #0b68d1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      cursor: pointer;
      font-weight: 850;
    }

    .showMoreButton.flat {
      border: 0;
      min-height: 28px;
      margin-top: 4px;
      background: transparent;
    }

    .treeHeader {
      display: grid;
      grid-template-columns: 22px minmax(0, 1fr) 18px;
      gap: 8px;
      align-items: center;
      padding: 6px 8px;
      color: #111827;
      font-weight: 850;
    }

    .treeChildren {
      display: grid;
      gap: 6px;
      margin-top: 6px;
      padding-inline-start: 18px;
    }

    .treeRow {
      grid-template-columns: minmax(0, 1fr) auto;
      padding-inline: 10px 8px;
    }

    .treeRow.collapsed {
      grid-template-columns: 22px minmax(0, 1fr) 18px;
      margin-top: 10px;
      border: 0;
      font-weight: 850;
      background: transparent;
    }

    .filterDialog[hidden],
    .popularPopover[hidden],
    .evidenceDialog[hidden] { display: none; }

    .filterDialog,
    .evidenceDialog {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px;
      width: min(420px, calc(100vw - 32px));
      box-shadow: 0 20px 54px rgba(21, 31, 52, 0.22);
      direction: rtl;
    }

    .filterDialog::backdrop,
    .evidenceDialog::backdrop { background: rgba(15, 23, 42, 0.26); }

    .evidenceDialog {
      width: min(520px, calc(100vw - 32px));
    }

    .evidenceMeta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 10px 0;
    }

    .evidencePill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding-inline: 10px;
      border: 1px solid #d8e1ec;
      border-radius: 999px;
      background: #f8fafc;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }

    .evidenceSnippet {
      margin: 0;
      padding: 12px;
      border: 1px solid #e1e8f0;
      border-radius: 10px;
      background: #fbfdff;
      line-height: 1.7;
      color: #172033;
    }

    .evidenceSourceStatus {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-block-start: 12px;
      min-height: 34px;
      padding-inline: 12px;
      border: 1px solid #d8e1ec;
      border-radius: 9px;
      background: #f8fafc;
      color: #64748b;
      font-weight: 800;
    }

    .evidenceSourceUrl {
      margin: 10px 0 0;
      color: #475569;
      direction: ltr;
      text-align: left;
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.5;
    }

    .dialogTop {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }

    .dialogTop h2 { margin: 0; font-size: 18px; }

    .dialogClose {
      width: 34px;
      height: 34px;
      border: 0;
      border-radius: 8px;
      background: #f3f6fb;
      cursor: pointer;
    }

    .dialogFilters {
      display: grid;
      gap: 10px;
      color: #253047;
    }

    .dialogFilters label {
      display: grid;
      gap: 5px;
      font-weight: 800;
    }

    .dialogFilters select {
      height: 38px;
      border: 1px solid #d8e0eb;
      border-radius: 8px;
      padding-inline: 10px;
      background: #fff;
    }

    .dialogActions {
      display: flex;
      justify-content: flex-start;
      gap: 10px;
      margin-top: 14px;
    }

    .dialogAction {
      min-height: 38px;
      border: 1px solid #d6e0ec;
      border-radius: 8px;
      padding-inline: 14px;
      background: #fff;
      cursor: pointer;
      font-weight: 800;
    }

    .dialogAction.primary {
      border-color: var(--purple);
      background: var(--purple);
      color: #fff;
    }

    .popularPopover {
      position: fixed;
      top: 84px;
      right: 500px;
      z-index: 10;
      width: 310px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      box-shadow: 0 16px 42px rgba(21, 31, 52, 0.18);
      direction: rtl;
    }

    .popularPopover h2 { margin: 0 0 8px; font-size: 16px; }

    .popularChoice {
      width: 100%;
      min-height: 38px;
      border: 1px solid #e0e7ef;
      border-radius: 8px;
      background: #fff;
      text-align: start;
      padding-inline: 10px;
      cursor: pointer;
    }

    .assistiveStatus {
      position: fixed;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }

    .compatPanel[hidden] { display: none; }

    @media (max-width: 1280px) {
      .dashboardGrid { grid-template-columns: 348px minmax(0, 1fr) 270px; }
      .municipalityBrand { min-width: 188px; }
      .brandWord { font-size: 34px; }
      .questionSearch { min-width: 360px; }
      .timelineCards { gap: 20px; }
    }

    @media (max-width: 1100px) {
      .SearchHeader { flex-wrap: wrap; }
      .municipalityBrand { min-width: auto; }
      .questionSearch { order: 2; flex-basis: 100%; min-width: 0; }
      .dashboardGrid {
        direction: rtl;
        grid-template-columns: 1fr;
      }
      .startDiscoveryPanel,
      .mainCivicWorkspace,
      .endDetailDrawer { grid-column: auto; }
      .startDiscoveryPanel { order: 1; }
      .mainCivicWorkspace { order: 2; grid-template-rows: 520px auto; }
      .endDetailDrawer { order: 3; }
    }

    @media (max-width: 760px) {
      .appShell { padding: 10px; }
      .SearchHeader { gap: 8px; }
      .headerButton { flex: 1 1 auto; min-width: 0; height: 48px; padding-inline: 10px; }
      .questionSearch input { font-size: 17px; height: 56px; }
      .questionSearch { height: 56px; }
      .dashboardGrid { min-height: auto; }
      .mainCivicWorkspace { grid-template-rows: 420px auto; }
      .timelineCards { grid-template-columns: 1fr; gap: 8px; }
      .timelineRail { display: none; }
      .timelineArrow { display: none; }
      .timelineBody { padding-inline: 0; }
    }
  </style>
</head>
<body>
  <div class="appShell" id="rag-dashboard" data-dashboard-endpoint="/api/ui/rag-dashboard/mock" data-source-status="fallback" data-build-id="source-preview-text-only-v2">
    <header class="SearchHeader" aria-label="סרגל חיפוש ראשי">
      <div class="municipalityBrand" aria-label="עירייה">
        <svg class="crest" viewBox="0 0 64 74" aria-hidden="true">
          <path d="M32 4 58 10v22c0 17-10 29-26 38C16 61 6 49 6 32V10Z" fill="#f7fbff" stroke="#0b68d1" stroke-width="3" />
          <path d="M32 10 51 15v17c0 13-7 22-19 29-12-7-19-16-19-29V15Z" fill="#e9f4ff" stroke="#0b68d1" stroke-width="1.8" />
          <path d="M22 26h20v23H22Z" fill="#ffffff" stroke="#0b68d1" stroke-width="1.6" />
          <path d="M25 30h4m3 0h4m3 0h4M25 36h4m3 0h4m3 0h4M25 42h4m3 0h4m3 0h4" stroke="#0b68d1" stroke-width="1.4" stroke-linecap="round" />
          <path d="M18 51h28" stroke="#f0b429" stroke-width="2" stroke-linecap="round" />
          <path d="M31 18h4v10h-4z" fill="#7fb342" />
        </svg>
        <strong class="brandWord">עירייה</strong>
      </div>

      <form id="ask-playground-form" class="questionSearch" aria-label="שאלת חיפוש">
        <input id="ask-question" name="question" type="search" value="מה הוחלט לגבי תכנית רובע טו?" aria-label="שאלה" autocomplete="off" />
        <button class="searchSubmit" type="submit" aria-label="חיפוש">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
            <circle cx="10.7" cy="10.7" r="6.8" />
            <path d="m16 16 5 5" stroke-linecap="round" />
          </svg>
        </button>
      </form>

      <button id="popular-searches-button" class="headerButton popularButton" type="button" aria-haspopup="true" aria-expanded="false">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="m12 3 2.85 5.78 6.38.93-4.62 4.5 1.09 6.36L12 17.58 6.3 20.57l1.09-6.36-4.62-4.5 6.38-.93Z" stroke-linejoin="round" />
        </svg>
        <span>חיפושים פופולריים</span>
      </button>

      <button id="filters-button" class="headerButton filterButton" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="filter-modal">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="M4 6h16M7 12h10M10 18h4" stroke-linecap="round" />
          <circle cx="8" cy="6" r="2" fill="#fff" />
          <circle cx="16" cy="12" r="2" fill="#fff" />
          <circle cx="12" cy="18" r="2" fill="#fff" />
        </svg>
        <span>מסננים</span>
        <b class="filterCountBadge" aria-label="מספר מסננים פעילים"></b>
      </button>

      <button class="headerButton adminButton" type="button" aria-label="מנהל">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <circle cx="12" cy="8" r="4" />
          <path d="M4.5 21a7.5 7.5 0 0 1 15 0" stroke-linecap="round" />
        </svg>
        <span>מנהל</span>
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="m8 10 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </header>

    <main class="dashboardGrid" aria-label="מרחב עבודה עירוני">
      <aside class="endDetailDrawer panelCard" aria-label="תשובה">
        <div class="drawerTop">
          <button class="drawerClose" type="button" aria-label="סגירת תשובה">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" stroke-linecap="round" />
            </svg>
          </button>
          <div class="detailTitle">
            <h1>תשובה</h1>
            <svg class="sparkleIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <path d="M13 2 10.9 8.2 5 10.5l5.9 2.3L13 20l2.1-7.2 5.9-2.3-5.9-2.3Z" stroke-linejoin="round" />
              <path d="M5 3v4M3 5h4" stroke-linecap="round" />
            </svg>
          </div>
        </div>

        <p class="questionLine">שאלה: מה הוחלט לגבי תכנית רובע טו?</p>

        <section class="answerSection" aria-labelledby="brief-title">
          <h2 id="brief-title" class="sectionTitle">תקציר</h2>
          <p class="sectionText">הוועדה המקומית אישרה להפקדה את תכנית רובע טו כולל שימושים מעורבים, מגורים, מסחר, שטחי ציבור פתוחים ודרכי תחבורה. ההפקדה נקבעה להתקדם בהתאמה לתנאים המפורטים בהחלטה.</p>
          <div class="confidenceRow" aria-label="ביטחון התשובה">
            <span class="infoDot" aria-hidden="true">i</span>
            <span>ביטחון התשובה:</span>
            <span class="confidenceBadge"><span class="confidenceDot" aria-hidden="true"></span>גבוהה</span>
          </div>
        </section>

        <section class="answerSection mapLayersSection" aria-labelledby="map-layers-title">
          <h2 id="map-layers-title" class="sectionTitle">שכבות במפה</h2>
          <ul id="map-layer-summary" class="mapLayerSummary" aria-label="שכבות במפה">
            <li class="mapLayerSummaryRow" data-layer="selected_parcel"><span class="mapLayerSummarySwatch"></span><span>אזור נבחר</span><strong class="mapLayerSummaryCount">1</strong></li>
            <li class="mapLayerSummaryRow" data-layer="nearby_parcels"><span class="mapLayerSummarySwatch"></span><span>חלקות בעיר</span><strong class="mapLayerSummaryCount">103</strong></li>
            <li class="mapLayerSummaryRow" data-layer="transport"><span class="mapLayerSummarySwatch"></span><span>תחבורה</span><strong class="mapLayerSummaryCount">8</strong></li>
            <li class="mapLayerSummaryRow" data-layer="schools"><span class="mapLayerSummarySwatch"></span><span>חינוך</span><strong class="mapLayerSummaryCount">6</strong></li>
            <li class="mapLayerSummaryRow" data-layer="osm"><span class="mapLayerSummarySwatch"></span><span>OSM הקשר</span><strong class="mapLayerSummaryCount">15</strong></li>
          </ul>
        </section>

        <section class="answerSection" aria-labelledby="decisions-title">
          <h2 id="decisions-title" class="sectionTitle">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <path d="M14 4 20 10 10 20H4v-6Z" stroke-linejoin="round" />
              <path d="m13 5 6 6" />
            </svg>
            החלטות עיקריות
          </h2>
          <ol class="decisionList">
            <li class="decisionRow">
              <span class="decisionNumber">1.</span>
              <span class="decisionText">אישור להפקדה בתנאים</span>
              <a class="sourceLink" href="#source-decision-1" data-source-link="decision_1">
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="M7 3h7l4 4v14H7Z" stroke-linejoin="round" />
                  <path d="M14 3v5h5M9 13h6M9 17h5" stroke-linecap="round" />
                </svg>
                מקור
              </a>
            </li>
            <li class="decisionRow">
              <span class="decisionNumber">2.</span>
              <span class="decisionText">נקבעו תנאים להמשך קידום התכנית והפקדתה.</span>
              <a class="sourceLink" href="#source-decision-2" data-source-link="decision_2">
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="M7 3h7l4 4v14H7Z" stroke-linejoin="round" />
                  <path d="M14 3v5h5M9 13h6M9 17h5" stroke-linecap="round" />
                </svg>
                מקור
              </a>
            </li>
            <li class="decisionRow">
              <span class="decisionNumber">3.</span>
              <span class="decisionText">נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית.</span>
              <a class="sourceLink" href="#source-decision-3" data-source-link="decision_3">
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="M7 3h7l4 4v14H7Z" stroke-linejoin="round" />
                  <path d="M14 3v5h5M9 13h6M9 17h5" stroke-linecap="round" />
                </svg>
                מקור
              </a>
            </li>
            <li class="decisionRow">
              <span class="decisionNumber">4.</span>
              <span class="decisionText">הצגת התכנית לציבור ושמיעת ההתנגדויות בכפוף לפרסום הודעה כדין.</span>
              <a class="sourceLink" href="#source-decision-4" data-source-link="decision_4">
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="M7 3h7l4 4v14H7Z" stroke-linejoin="round" />
                  <path d="M14 3v5h5M9 13h6M9 17h5" stroke-linecap="round" />
                </svg>
                מקור
              </a>
            </li>
          </ol>
        </section>

        <section class="answerSection" aria-labelledby="related-title">
          <h2 id="related-title" class="sectionTitle">נושאים קשורים</h2>
          <div class="relatedChips" role="list">
            <a class="topicChip" href="#topic-rova" role="listitem">רובע טו</a>
            <a class="topicChip" href="#topic-plan" role="listitem">תכנית רובע טו</a>
            <a class="topicChip" href="#topic-planning" role="listitem">תכנון ובנייה</a>
            <a class="topicChip" href="#topic-detailed" role="listitem">תוכניות מפורטות</a>
            <a class="topicChip" href="#topic-transit" role="listitem">תחבורה ציבורית</a>
            <a class="topicChip" href="#topic-open" role="listitem">שטחים פתוחים</a>
            <a class="topicChip" href="#topic-env" role="listitem">השפעה סביבתית</a>
          </div>
        </section>

        <section class="answerSection" aria-labelledby="limitations-title">
          <h2 id="limitations-title" class="sectionTitle limitationsTitle">
            <svg class="icon warningIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <path d="M12 3 22 20H2Z" stroke-linejoin="round" />
              <path d="M12 9v5M12 17h.01" stroke-linecap="round" />
            </svg>
            מגבלות
          </h2>
          <p class="sectionText">ייתכנו שינויים בהחלטות עד לאישור סופי. מומלץ לפתוח את המקור לפני הסקת מסקנות.</p>
        </section>

      </aside>

      <section class="mainCivicWorkspace" aria-label="מפה וציר זמן">
        <section class="mapPanel panelCard" aria-label="מפה סכמטית">
          <div class="mapZoomControls" aria-label="פקדי מפה">
            <div class="mapZoomControlGroup">
              <button id="map-view-reset" class="mapZoomButton" type="button" aria-label="מרכז מפה">
                <svg class="mapZoomIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke-linecap="round"/></svg>
              </button>
            </div>
            <div class="mapZoomControlGroup">
              <button id="map-view-zoom-in" class="mapZoomButton" type="button" aria-label="התקרבות">+</button>
              <button id="map-view-zoom-out" class="mapZoomButton" type="button" aria-label="התרחקות">-</button>
            </div>
            <div class="mapZoomControlGroup">
              <button id="map-view-layers" class="mapZoomButton" type="button" aria-label="סינון שכבות ואובייקטים במפה" aria-controls="map-layer-popover" aria-expanded="false">
                <svg class="mapZoomIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m12 4 8 4-8 4-8-4Z" stroke-linejoin="round"/><path d="m5 12 7 3.5 7-3.5M5 16l7 3.5 7-3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <span>סינון מפה</span>
              </button>
            </div>
          </div>
          <aside id="map-layer-popover" class="mapLayerPopover" aria-label="מקרא שכבות מפה" hidden>
            <div class="mapLayerPopoverTop">
              <h3>שכבות וסינון</h3>
              <button class="mapLayerPopoverClose" type="button" data-map-layer-close aria-label="סגירת סינון מפה">×</button>
            </div>
            <div class="mapLayerTools" aria-label="כלי תצוגת מפה">
              <label class="mapIconSizeControl" for="map-icon-size">
                גודל אייקונים: <strong id="map-icon-size-label">קטן</strong>
                <input id="map-icon-size" type="range" min="0.05" max="0.6" step="0.01" value="0.05" />
              </label>
              <label class="mapIconSizeControl" for="map-label-size">
                גודל שמות: <strong id="map-label-size-label">קטן</strong>
                <input id="map-label-size" type="range" min="0.08" max="0.9" step="0.02" value="0.08" />
              </label>
              <button id="map-toggle-all-filters" class="mapLayerToolButton" type="button">כיבוי כל המסננים</button>
            </div>
            <p class="mapLayerPopoverSectionTitle">בסיס המפה</p>
            <ul class="mapLayerLegendList">
              <li><label><input type="checkbox" data-map-layer-toggle="basemap" autocomplete="off" /><span class="mapLayerLegendSwatch road"></span><span>רקע עירוני מפורט (Raster לא מסונן)</span></label></li>
              <li><label><input type="checkbox" data-map-layer-toggle="parcel" checked /><span class="mapLayerLegendSwatch parcel"></span><span>אזור נבחר</span></label></li>
              <li><label><input type="checkbox" data-map-layer-toggle="nearby_parcels" autocomplete="off" /><span class="mapLayerLegendSwatch nearby"></span><span>חלקות בעיר</span></label></li>
            </ul>
            <p class="mapLayerPopoverSectionTitle">סינון אובייקטים</p>
            <ul class="mapLayerLegendList compact">
              <li><label><input type="checkbox" data-map-object-toggle="parcels" autocomplete="off" /><span class="mapLayerLegendSwatch parcel"></span><span>חלקות בעיר</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="buildings" autocomplete="off" /><span class="mapLayerLegendSwatch osm"></span><span>מבנים עירוניים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="osm_roads" checked /><span class="mapLayerLegendSwatch road"></span><span>OSM דרכים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="osm_railways" /><span class="mapLayerLegendSwatch transport"></span><span>OSM רכבת/מסילות</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="transport" checked /><span class="mapLayerLegendSwatch transport"></span><span>תחבורה</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="education" checked /><span class="mapLayerLegendSwatch school"></span><span>חינוך</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="neighborhoods" checked /><span class="mapLayerLegendSwatch municipal"></span><span>שכונות</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="addresses" checked /><span class="mapLayerLegendSwatch address"></span><span>כתובות</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="municipal_pois" checked /><span class="mapLayerLegendSwatch municipal"></span><span>מבני ציבור</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="municipal_parks" checked /><span class="mapLayerLegendSwatch park"></span><span>גנים עירוניים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="osm_parks" autocomplete="off" /><span class="mapLayerLegendSwatch park"></span><span>OSM שטחים ירוקים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="municipal_beaches" checked /><span class="mapLayerLegendSwatch beach"></span><span>חופים עירוניים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="osm_water" /><span class="mapLayerLegendSwatch beach"></span><span>OSM מים/נחלים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="municipal_bike_paths" checked /><span class="mapLayerLegendSwatch bike"></span><span>שבילי אופניים</span></label></li>
              <li><label><input type="checkbox" data-map-object-toggle="osm_interest" checked /><span class="mapLayerLegendSwatch osm"></span><span>OSM מוקדי עניין</span></label></li>
            </ul>
          </aside>
          <div id="map-renderer-status" class="mapRendererStatus">SVG GIS פעיל</div>
          <div class="mapFrame">
            <svg class="schematicMapSvg" viewBox="0 0 900 620" role="img" aria-labelledby="map-title map-desc" preserveAspectRatio="none">
              <title id="map-title">מפה סכמטית של רובע טו</title>
              <desc id="map-desc">ים במערב, רשת רחובות בהירה, אזור נבחר סגול, תוואי תחבורה ציבורית כחול ופארקים ירוקים.</desc>
              <defs>
                <linearGradient id="seaGradient" x1="0" x2="1" y1="0" y2="0">
                  <stop offset="0" stop-color="#aee2f4" />
                  <stop offset="1" stop-color="#d8f3fb" />
                </linearGradient>
                <pattern id="streetGrid" width="42" height="42" patternUnits="userSpaceOnUse" patternTransform="rotate(18)">
                  <path d="M0 21H42M21 0V42" stroke="#dfe4e8" stroke-width="1.2" opacity="0.78" />
                </pattern>
                <filter id="mapShadow" x="-30%" y="-30%" width="160%" height="160%">
                  <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#1b2a3e" flood-opacity="0.18" />
                </filter>
              </defs>

              <rect x="0" y="0" width="230" height="620" fill="url(#seaGradient)" />
              <path d="M225 -20 C200 100 206 190 178 280 C151 378 157 470 124 642 L900 642 L900 -20Z" fill="#f7f8f6" />
              <path d="M225 -20 C200 100 206 190 178 280 C151 378 157 470 124 642" fill="none" stroke="#f2eadf" stroke-width="9" opacity="0.95" />
              <path d="M225 -20 C200 100 206 190 178 280 C151 378 157 470 124 642" fill="none" stroke="#ffffff" stroke-width="3" opacity="0.9" />
              <rect x="170" y="0" width="730" height="620" fill="url(#streetGrid)" opacity="0.9" />

              <g fill="#cdeedb" opacity="0.74">
                <path d="M635 20c42 20 79 11 99 54 17 37-10 81-50 89-36 7-76-21-84-58-8-36 1-85 35-85Z" />
                <path d="M735 350c36-10 75 22 70 58-6 44-58 54-91 34-34-21-24-79 21-92Z" />
                <path d="M615 502c45-11 83 20 78 59-5 35-50 45-82 30-38-18-44-78 4-89Z" />
                <path d="M292 430c31-20 76-3 82 35 6 36-31 58-66 50-35-8-47-75-16-85Z" />
                <path d="M375 75c26-17 62 1 62 33 0 31-40 45-64 30-24-15-26-49 2-63Z" />
              </g>

              <g fill="#bfe3ff" opacity="0.58">
                <path d="M750 196c21-23 62-10 67 20 4 25-24 45-49 37-24-8-38-31-18-57Z" />
                <path d="M680 452c24-18 57 5 50 33-6 23-40 31-58 15-18-16-13-36 8-48Z" />
                <circle cx="695" cy="68" r="9" />
                <circle cx="565" cy="186" r="7" />
                <circle cx="303" cy="305" r="7" />
              </g>

              <g fill="none" stroke-linecap="round">
                <path d="M180 118 C286 106 340 118 420 142 S580 151 742 120" stroke="#ffffff" stroke-width="11" opacity="0.92" />
                <path d="M234 470 C335 433 418 404 520 395 S714 402 850 358" stroke="#ffffff" stroke-width="10" opacity="0.92" />
                <path d="M296 2 C338 104 350 190 394 278 S468 423 489 620" stroke="#ffffff" stroke-width="9" opacity="0.86" />
                <path d="M640 0 C620 94 628 181 682 278 S722 445 705 620" stroke="#ffffff" stroke-width="9" opacity="0.86" />
                <path d="M212 330 C337 304 476 295 630 307 S792 310 900 286" stroke="#ffffff" stroke-width="8" opacity="0.86" />
                <path d="M180 118 C286 106 340 118 420 142 S580 151 742 120" stroke="#d9e1e8" stroke-width="2" opacity="0.88" />
                <path d="M234 470 C335 433 418 404 520 395 S714 402 850 358" stroke="#d9e1e8" stroke-width="2" opacity="0.88" />
                <path d="M296 2 C338 104 350 190 394 278 S468 423 489 620" stroke="#d9e1e8" stroke-width="2" opacity="0.78" />
                <path d="M640 0 C620 94 628 181 682 278 S722 445 705 620" stroke="#d9e1e8" stroke-width="2" opacity="0.78" />
                <path d="M212 330 C337 304 476 295 630 307 S792 310 900 286" stroke="#d9e1e8" stroke-width="2" opacity="0.78" />
              </g>

              <g fill="#d8f2df" opacity="0.65">
                <rect x="262" y="141" width="42" height="42" rx="4" transform="rotate(18 283 162)" />
                <rect x="476" y="174" width="39" height="39" rx="4" transform="rotate(18 495 193)" />
                <rect x="588" y="420" width="47" height="46" rx="5" transform="rotate(18 611 443)" />
                <rect x="278" y="532" width="43" height="48" rx="5" transform="rotate(18 299 556)" />
                <rect x="745" y="502" width="46" height="40" rx="5" transform="rotate(18 768 522)" />
                <rect x="346" y="263" width="39" height="44" rx="5" transform="rotate(18 365 285)" />
              </g>

              <path data-map-entity-id="entity_rova_tet_vav_transit_route" data-map-tooltip-title="תוואי תחבורה ציבורית" tabindex="0" role="button" aria-label="תוואי תחבורה ציבורית" d="M525 -10 C508 54 514 123 548 184 C574 231 608 263 600 328 C592 388 559 447 562 518 C563 556 581 592 606 632" fill="none" stroke="#1676d2" stroke-width="2" stroke-dasharray="8 8" opacity="0.95" />
              <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="transport" x="629" y="318" fill="#172033" font-size="17">תחבורה ציבורית</text>

              <path data-map-entity-id="entity_rova_tet_vav_selected_area" data-map-tooltip-title="רובע טו" tabindex="0" role="button" aria-label="רובע טו" d="M336 330 377 238 455 250 518 287 491 344 426 341 404 392 362 367 366 346Z" fill="rgba(111,70,217,0.20)" stroke="#6f46d9" stroke-width="2.2" />
              <rect data-map-entity-id="entity_rova_tet_vav_selected_area" data-map-tooltip-title="רובע טו" x="414" y="292" width="78" height="34" rx="9" fill="#6f46d9" filter="url(#mapShadow)" />
              <text data-map-entity-id="entity_rova_tet_vav_selected_area" data-map-tooltip-title="רובע טו" id="map-selected-label" x="453" y="314" fill="#fff" font-size="17" font-weight="800" text-anchor="middle" direction="rtl">רובע טו</text>

              <g fill="#1f2937" font-size="17" font-weight="850" text-anchor="middle" direction="rtl">
                <text id="map-area-north" data-map-label="true" data-base-font-size="17" x="590" y="44">צפון העיר</text>
                <text id="map-area-center" data-map-label="true" data-base-font-size="17" x="489" y="261">מרכז העיר</text>
                <text id="map-area-west" data-map-label="true" data-base-font-size="17" x="238" y="307">מערב העיר</text>
                <text id="map-area-east" data-map-label="true" data-base-font-size="17" x="806" y="292">מזרח העיר</text>
                <text id="map-area-south" data-map-label="true" data-base-font-size="17" x="450" y="555">דרום העיר</text>
              </g>
              <text id="map-sea-label" x="136" y="166" fill="#0b68d1" font-size="16" font-weight="850" text-anchor="middle" direction="rtl">חוף הים</text>

              <g class="mapMarkers" filter="url(#mapShadow)">
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" data-map-tooltip-title="תחנת תחבורה ציבורית" transform="translate(520 90)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" data-map-tooltip-title="תחנת תחבורה ציבורית" transform="translate(587 248)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" data-map-tooltip-title="תחנת תחבורה ציבורית" transform="translate(552 373)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" data-map-tooltip-title="תחנת תחבורה ציבורית" transform="translate(528 500)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>

                <g tabindex="0" role="button" aria-label="פארק עירוני" data-map-tooltip-title="פארק עירוני" transform="translate(330 86)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק עירוני" data-map-tooltip-title="פארק עירוני" transform="translate(590 324)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק עירוני" data-map-tooltip-title="פארק עירוני" transform="translate(301 410)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק עירוני" data-map-tooltip-title="פארק עירוני" transform="translate(684 213)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק עירוני" data-map-tooltip-title="פארק עירוני" transform="translate(764 545)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>

                <g tabindex="0" role="button" aria-label="מבנה ציבור" data-map-tooltip-title="מבנה ציבור" transform="translate(375 220)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
                <g tabindex="0" role="button" aria-label="מבנה ציבור" data-map-tooltip-title="מבנה ציבור" transform="translate(585 216)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
                <g tabindex="0" role="button" aria-label="מבנה ציבור" data-map-tooltip-title="מבנה ציבור" transform="translate(720 382)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
              </g>
              <g class="schematicMapLabels">
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="osm_parks" x="330" y="61" fill="#172033" font-size="17">פארק עירוני</text>
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="osm_parks" x="590" y="299" fill="#172033" font-size="17">פארק עירוני</text>
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="osm_parks" x="301" y="385" fill="#172033" font-size="17">פארק עירוני</text>
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="municipal_pois" x="375" y="195" fill="#172033" font-size="17">מבנה ציבור</text>
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="municipal_pois" x="585" y="191" fill="#172033" font-size="17">מבנה ציבור</text>
                <text class="realGisStaticLabel" data-map-label="true" data-base-font-size="17" data-map-object="municipal_pois" x="720" y="357" fill="#172033" font-size="17">מבנה ציבור</text>
              </g>
            </svg>

            <div id="real-gis-map" class="realGisMap" role="img" aria-label="מפת GIS אמיתית עם חלקה ונקודות עניין" hidden></div>
            <svg id="real-gis-static-map" class="realGisStaticMap" viewBox="0 0 900 620" role="img" aria-label="מפת GIS אמיתית ללא ספריית מפה חיצונית" hidden></svg>

            <aside class="mapProvenanceBadge" data-spatial-representation="schematic" aria-label="מקוריות המפה">
              <p class="mapProvenanceBadgeTitle">מפה סכמטית בלבד</p>
              <p class="mapProvenanceBadgeText">אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.</p>
            </aside>

            <label class="mapExampleSelector" for="map-example-select">
              דוגמה
              <select id="map-example-select" aria-label="בחירת דוגמת מפה">
                <option value="tel_aviv_parcel" selected>תל אביב - חלקת MAPI רשמית</option>
              </select>
            </label>

            <div class="mapControls" aria-label="פקדי מפה">
              <div class="mapControlGroup">
                <button id="map-control-center" class="mapControlButton" type="button" aria-label="מרכז מפה">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" stroke-linecap="round" /></svg>
                </button>
              </div>
              <div class="mapControlGroup">
                <button id="map-control-zoom-in" class="mapControlButton" type="button" aria-label="התקרבות">
                  <span class="mapControlButtonText" aria-hidden="true">+</span>
                </button>
                <button id="map-control-zoom-out" class="mapControlButton" type="button" aria-label="התרחקות">
                  <span class="mapControlButtonText" aria-hidden="true">-</span>
                </button>
              </div>
              <div class="mapControlGroup">
                <button id="coverage-matrix-button" class="mapControlButton" type="button" aria-label="שכבות מפה" title="מטריצת כיסוי שכבות" aria-controls="coverage-panel" aria-expanded="false">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5Z" stroke-linejoin="round" /><path d="m4 12 8 4 8-4M4 16l8 4 8-4" stroke-linejoin="round" /></svg>
                </button>
              </div>
            </div>

            <section class="legendCard" aria-label="מקרא מפה; הרקע העירוני המפורט הוא תמונת Raster לא מסוננת; חלקת MAPI רשמית; תכנית אם קיימת בנקודה; גבול תל אביב רשמי; מוסד חינוך; מבנה הקשר OSM; מוקד הקשר OSM">
              <h2 class="legendTitle">שכבות</h2>
              <ul id="map-layer-counts" class="layerCountsList" aria-label="ספירת שכבות מפה"></ul>
              <ul class="legendList">
                <li class="legendItem"><span class="legendSwatch area"></span><span>אזור נבחר</span></li>
                <li class="legendItem"><span class="legendSwatch transit"></span><span>תחבורה ציבורית</span></li>
                <li class="legendItem"><span class="legendSwatch plan"></span><span>פארקים</span></li>
                <li class="legendItem"><span class="legendSwatch school"></span><span>מבני ציבור</span></li>
                <li class="legendItem"><span class="legendSwatch interest"></span><span>מוקדי עניין</span></li>
              </ul>
              <h2 class="legendTitle">מקורות</h2>
              <ul class="statusLegendList" aria-label="סטטוס מקורות">
                <li><span class="sourceBadge" data-status="official">רשמי</span></li>
                <li><span class="sourceBadge" data-status="official_with_caveat">רשמי עם סייג</span></li>
                <li><span class="sourceBadge" data-status="municipal_open">עירוני פתוח</span></li>
                <li><span class="sourceBadge" data-status="municipal_license_under_review">בבדיקת רישיון</span></li>
                <li><span class="sourceBadge" data-status="context_only">הקשר בלבד</span></li>
                <li><span class="sourceBadge" data-status="temporary_input_only">זמני בלבד</span></li>
                <li><span class="sourceBadge" data-status="unavailable">לא זמין</span></li>
              </ul>
            </section>

            <aside id="point-report-panel" class="mapInfoPanel" aria-live="polite" aria-label="דוח נקודה במפה" hidden>
              <div class="mapInfoTop">
                <h3>דוח נקודה</h3>
                <button class="mapPanelClose" type="button" data-close-panel="point-report-panel" aria-label="סגירת דוח נקודה">×</button>
              </div>
              <div class="pointReportSections">
                <p class="pointReportMessage">לחיצה על המפה תציג רשות, חלקה, תכניות, שכונה ונקודות עניין סמוכות.</p>
              </div>
            </aside>

            <aside id="coverage-panel" class="coveragePanel" aria-live="polite" aria-label="מטריצת כיסוי שכבות" hidden>
              <div class="coverageTop">
                <h3>כיסוי שכבות</h3>
                <button class="mapPanelClose" type="button" data-close-panel="coverage-panel" aria-label="סגירת מטריצת כיסוי">×</button>
              </div>
              <div id="coverage-panel-body">
                <p class="pointReportMessage">הכיסוי נטען מהשרת ומציג אילו שכבות זמינות, חסרות או בבדיקת רישיון.</p>
              </div>
            </aside>
          </div>
        </section>

        <section class="timelinePanel panelCard" aria-labelledby="timeline-title">
          <div class="timelineHeader">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <h2 id="timeline-title">ציר זמן</h2>
          </div>
          <div class="timelineBody">
            <button class="timelineArrow prev" type="button" aria-label="אירוע קודם">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m15 18-6-6 6-6" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
            <button class="timelineArrow next" type="button" aria-label="אירוע הבא">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m9 18 6-6-6-6" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
            <div class="timelineCards" role="list">
              <button class="timelineCard" type="button" role="listitem">
                <span class="timelineDate">15.07.2024</span>
                <span class="timelineLabel">פרסום להפקדה</span>
                <span class="timelineSub">התחלת תהליך</span>
              </button>
              <button class="timelineCard approved" type="button" role="listitem">
                <span class="timelineDate">02.07.2024</span>
                <span class="timelineLabel">החלטה מס׳ 1123</span>
                <span class="timelineSub">אישור התנאים</span>
              </button>
              <button class="timelineCard selected" type="button" role="listitem" aria-current="true">
                <span class="timelineDate">23.06.2024</span>
                <span class="timelineLabel">ישיבה מס׳ 478</span>
                <span class="timelineSub">אישור להפקדה</span>
              </button>
              <button class="timelineCard public" type="button" role="listitem">
                <span class="timelineDate">28.05.2024</span>
                <span class="timelineLabel">דיון ציבורי</span>
                <span class="timelineSub">הצגת התכנית</span>
              </button>
              <button class="timelineCard" type="button" role="listitem">
                <span class="timelineDate">10.05.2024</span>
                <span class="timelineLabel">דיונים מקדימים</span>
                <span class="timelineSub">פרסום תכנית</span>
              </button>
            </div>
            <div class="timelineRail" aria-hidden="true">
              <span class="timelineNode" style="left: 0%"></span>
              <span class="timelineNode" style="left: 25%"></span>
              <span class="timelineNode selected" style="left: 50%"></span>
              <span class="timelineNode" style="left: 75%"></span>
              <span class="timelineNode" style="left: 100%"></span>
            </div>
          </div>
        </section>
      </section>

      <aside class="startDiscoveryPanel panelCard" aria-label="גילוי נושאים">
        <section class="discoverySection" aria-labelledby="categories-title">
          <div class="panelHeading">
            <h2 id="categories-title">קטגוריות</h2>
            <svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
            </svg>
          </div>
          <ul class="rowList">
            <li><button class="discoveryRow selected" type="button" data-item-id="planning"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 10h16v10H4zM8 10V6h8v4M2 20h22" stroke-linejoin="round" /></svg><span class="rowLabel">תכנון ובנייה</span><span class="countPill">342</span></button></li>
            <li><button class="discoveryRow" type="button" data-item-id="transport"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M6 20V5h8v15M14 9h5v11M8 9h2M8 13h2M8 17h2M16 13h1M16 17h1" stroke-linecap="round" stroke-linejoin="round" /></svg><span class="rowLabel">תחבורה</span><span class="countPill">128</span></button></li>
            <li><button class="discoveryRow" type="button" data-item-id="education"><svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m12 4 10 5-10 5L2 9Zm-6 8.2 6 3 6-3V17c-2.2 2.1-9.8 2.1-12 0Z" /></svg><span class="rowLabel">חינוך</span><span class="countPill">95</span></button></li>
            <li><button class="discoveryRow" type="button" data-item-id="welfare"><svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 21s-8-4.7-8-11a4.8 4.8 0 0 1 8-3.5A4.8 4.8 0 0 1 20 10c0 6.3-8 11-8 11Z" /></svg><span class="rowLabel">רווחה</span><span class="countPill">76</span></button></li>
            <li><button class="discoveryRow" type="button" data-item-id="environment"><svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20 4c-7.5.4-13.3 4-14.5 9.8-.6 3 1 5.8 3.7 6.7 5.6 1.8 10.4-4.9 10.8-16.5ZM7 19c2.6-4.3 5.5-7.2 10-9" /></svg><span class="rowLabel">סביבה</span><span class="countPill">64</span></button></li>
          </ul>
          <button class="showMoreButton flat" type="button">הצג עוד <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m8 10 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
        </section>

        <section class="discoverySection" aria-labelledby="hot-title">
          <div class="panelHeading">
            <h2 id="hot-title">נושאים בולטים</h2>
            <svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13.5 2.5c.5 4-2.8 5.4-1 8.1.9-1.5 1.4-2.7 1.4-4.5 3 2.2 5.1 5 5.1 9 0 4-3.1 7.2-7 7.2S5 19 5 15.3c0-2.7 1.5-5.1 4.1-7.1-.2 2 .3 3.4 1.7 4.4.2-3.5 1.7-6.1 2.7-10.1Z" /></svg>
          </div>
          <ul class="rowList">
            <li><button class="discoveryRow topicRow selected" type="button"><span class="rowLabel">תכנית רובע טו</span><span class="countPill">23</span></button></li>
            <li><button class="discoveryRow topicRow" type="button"><span class="rowLabel">הקמת קו רכבת קלה</span><span class="countPill">18</span></button></li>
            <li><button class="discoveryRow topicRow" type="button"><span class="rowLabel">שדרוג פארק לכיש</span><span class="countPill">14</span></button></li>
            <li><button class="discoveryRow topicRow" type="button"><span class="rowLabel">תכנית מתאר חדשה</span><span class="countPill">11</span></button></li>
          </ul>
          <button class="showMoreButton flat" type="button">הצג עוד <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m8 10 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
        </section>

        <section class="discoverySection" aria-labelledby="tree-title">
          <div class="panelHeading">
            <h2 id="tree-title">עץ נושאים</h2>
            <svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M11 21v-5H8a4 4 0 0 1-1.4-7.7A5.5 5.5 0 0 1 17 6.5 4.5 4.5 0 0 1 18 15h-5v6Z" /></svg>
          </div>
          <div class="topic-root">
            <div class="treeHeader">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 10h16v10H4zM8 10V6h8v4M2 20h22" stroke-linejoin="round" /></svg>
              <span>תכנון ובנייה</span>
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m8 10 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </div>
            <div class="treeChildren topic-child">
              <button class="treeRow" type="button"><span class="rowLabel">תוכניות מתאר</span><span class="countPill">12</span></button>
              <button class="treeRow selected" type="button"><span class="rowLabel">תוכניות מפורטות</span><span class="countPill">23</span></button>
              <button class="treeRow" type="button"><span class="rowLabel">היתרים</span><span class="countPill">7</span></button>
            </div>
          </div>
          <button class="treeRow collapsed" type="button">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M6 20V5h8v15M14 9h5v11M8 9h2M8 13h2M8 17h2M16 13h1M16 17h1" stroke-linecap="round" stroke-linejoin="round" /></svg>
            <span>תחבורה</span>
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m9 18 6-6-6-6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
          <button class="showTreeButton" type="button">הצג כל העץ <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m8 10 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
        </section>
      </aside>
    </main>
  </div>

  <dialog id="filter-modal" class="filterDialog" hidden aria-labelledby="filter-title">
    <div class="dialogTop">
      <h2 id="filter-title">סינון תוצאות</h2>
      <button class="dialogClose" type="button" aria-label="סגירת מסננים">×</button>
    </div>
    <div class="dialogFilters">
      <label>אזור<select name="area"><option>כל העיר</option><option>רובע טו</option></select></label>
      <label>טווח זמן<select name="time_range"><option>2024</option><option>כל השנים</option></select></label>
      <label>קטגוריה<select name="category"><option value="">כל הקטגוריות</option><option value="planning">תכנון ובנייה</option><option value="transport">תחבורה</option></select></label>
      <label>סוגי מקורות<select name="source_types"><option>פרוטוקולים ונספחים</option><option>פרוטוקולים</option></select></label>
      <label>ודאות<select name="confidence"><option>גבוהה ובינונית</option><option>כל הרמות</option></select></label>
    </div>
    <div class="dialogActions">
      <button class="dialogAction" type="button">איפוס</button>
      <button class="dialogAction primary" type="button">החל סינון</button>
    </div>
  </dialog>

  <section id="popular-popover" class="popularPopover" hidden aria-labelledby="popular-title">
    <h2 id="popular-title">חיפושים פופולריים</h2>
    <button class="popularChoice" type="button">מה הוחלט לגבי תכנית רובע טו?</button>
  </section>

  <dialog id="evidence-preview" class="evidenceDialog" hidden aria-labelledby="evidence-preview-title">
    <div class="dialogTop">
      <h2 id="evidence-preview-title">מקור</h2>
      <button class="dialogClose evidenceClose" type="button" aria-label="סגירת מקור">×</button>
    </div>
    <div class="evidenceMeta" aria-label="פרטי מקור">
      <span class="evidencePill" id="evidence-artifact-kind"></span>
      <span class="evidencePill" id="evidence-page-span"></span>
      <span class="evidencePill" id="evidence-confidence"></span>
    </div>
    <p class="evidencePill" id="evidence-header-path"></p>
    <p class="evidenceSnippet" id="evidence-snippet"></p>
    <span class="evidenceSourceStatus" id="evidence-source-status">אין כפתור פתיחה: קישור מקור מוצג כטקסט בלבד</span>
    <p class="evidenceSourceUrl" id="evidence-source-url">קישור מקור אינו זמין</p>
  </dialog>

  <span id="ask-playground-status" class="assistiveStatus" aria-live="polite"></span>

  <section class="compatPanel" hidden aria-hidden="true">
    <label>debug mode (show PDF-first debug)<input id="ask-debug-mode" type="checkbox" checked /></label>
    <input id="ask-top-k" type="number" value="8" />
    <input id="ask-topic" type="text" />
    <input id="ask-year" type="number" />
    <input id="ask-show-extended" type="checkbox" />
    <section id="ask-playground-answer"><p id="ask-playground-answer-text"></p><div id="ask-playground-answer-sections"></div><details id="ask-playground-extended-panel"><summary>Extended</summary><p id="ask-playground-extended-text"></p></details><ul id="ask-playground-limitations"></ul></section>
    <section id="ask-playground-provider-warning"><p id="ask-playground-provider-warning-text"></p></section>
    <section id="ask-playground-refusal"><p id="ask-playground-refusal-text"></p></section>
    <section id="ask-playground-citations"><ul id="ask-playground-citations-list"></ul></section>
    <section id="ask-playground-thresholds"><pre id="ask-playground-thresholds-json"></pre></section>
    <section id="ask-playground-trace"><ul id="ask-playground-trace-list"></ul></section>
    <section id="ask-playground-artifacts"><p id="ask-playground-artifacts-title"></p><ul id="ask-playground-artifacts-list"></ul></section>
    <ul id="ask-playground-meta"></ul><pre id="ask-playground-json"></pre>
  </section>

  <script>
    (() => {
      const form = document.getElementById("ask-playground-form");
      const questionInput = document.getElementById("ask-question");
      const statusNode = document.getElementById("ask-playground-status");
      const debugModeInput = document.getElementById("ask-debug-mode");
      const topKInput = document.getElementById("ask-top-k");
      const filtersButton = document.getElementById("filters-button");
      const filterModal = document.getElementById("filter-modal");
      const popularButton = document.getElementById("popular-searches-button");
      const popularPopover = document.getElementById("popular-popover");
      const mapExampleSelect = document.getElementById("map-example-select");
      const dashboardRoot = document.getElementById("rag-dashboard");
      const DASHBOARD_DATA_ENDPOINT = dashboardRoot?.dataset.dashboardEndpoint || "/api/ui/rag-dashboard/mock";
      const DASHBOARD_QUERY_ENDPOINT = "/api/ui/rag-dashboard/query";
      const DASHBOARD_INTERACTION_ENDPOINT = "/api/ui/rag-dashboard/interaction";
      const DASHBOARD_EVIDENCE_ENDPOINT = "/api/ui/rag-dashboard/evidence";
      const DASHBOARD_GIS_MAP_ENDPOINT = "/api/ui/rag-dashboard/gis-map";
      const POINT_REPORT_ENDPOINT = "/v1/point-report";
      const COVERAGE_ENDPOINT = "/v1/coverage";
      const DASHBOARD_QUERY_TIMEOUT_MS = 45000;
      const evidenceDialog = document.getElementById("evidence-preview");
      let currentDashboardData = null;
      let dashboardState = null;
      let dashboardRequestSeq = 0;
      let lastFocusedEvidenceLink = null;
      let activeFilters = {};
      let realGisMap = null;
      let realGisClickBound = false;
      let realGisFitBounds = null;
      let realGisHoverPopup = null;
      let realGisUserAdjustedView = false;
      let staticGisZoom = 1;
      let staticGisViewBox = { x: 0, y: 0, width: 900, height: 620 };
      let realGisDecorations = [];
      let currentMapExample = mapExampleSelect?.value || "tel_aviv_parcel";
      let currentMapIconScale = 0.12;
      let currentMapLabelScale = 0.18;
      let gisMapProfileRequest = null;
      window.__municipalMapIconScale = currentMapIconScale;
      window.__municipalMapLabelScale = currentMapLabelScale;
      const MAP_POI_ICON_LIMIT = 160;
      const MAP_MUNICIPAL_ICON_LIMIT = 120;
      const MAP_PARK_ICON_LIMIT = 120;

      const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
      const isAlmostEqualText = (left, right) => normalizeText(left) === normalizeText(right);
      const hasMeaningfulExtraInfo = (baseText, extendedText) => {
        const base = normalizeText(baseText);
        const extended = normalizeText(extendedText);
        return Boolean(extended && extended !== base && extended.length > base.length + 40);
      };
      window.isAlmostEqualText = isAlmostEqualText;
      window.hasMeaningfulExtraInfo = hasMeaningfulExtraInfo;

      const setText = (selector, value) => {
        const node = document.querySelector(selector);
        if (node && value !== undefined && value !== null) {
          node.textContent = String(value);
        }
      };

      const setAttr = (selector, attr, value) => {
        const node = document.querySelector(selector);
        if (node && value !== undefined && value !== null) {
          node.setAttribute(attr, String(value));
        }
      };

      const applyStaticGisZoom = () => {
        const staticMap = document.getElementById("real-gis-static-map");
        if (!staticMap || staticMap.hidden) {
          return false;
        }
        const width = staticGisViewBox.width / staticGisZoom;
        const height = staticGisViewBox.height / staticGisZoom;
        const x = staticGisViewBox.x + (staticGisViewBox.width - width) / 2;
        const y = staticGisViewBox.y + (staticGisViewBox.height - height) / 2;
        staticMap.setAttribute("viewBox", `${x.toFixed(1)} ${y.toFixed(1)} ${width.toFixed(1)} ${height.toFixed(1)}`);
        return true;
      };

      const resetStaticGisZoom = () => {
        staticGisZoom = 1;
        applyStaticGisZoom();
      };

      const zoomStaticGisMap = (delta) => {
        staticGisZoom = Math.max(0.75, Math.min(4.0, staticGisZoom + delta));
        return applyStaticGisZoom();
      };

      const zoomDashboardMap = (delta) => {
        const direction = Number(delta) > 0 ? 1 : -1;
        realGisUserAdjustedView = true;
        const mapNode = document.getElementById("real-gis-map");
        if (realGisMap && mapNode && !mapNode.hidden && typeof realGisMap.easeTo === "function") {
          try {
            realGisMap.resize();
            const minZoom = typeof realGisMap.getMinZoom === "function" ? realGisMap.getMinZoom() : 0;
            const maxZoom = typeof realGisMap.getMaxZoom === "function" ? realGisMap.getMaxZoom() : 22;
            const targetZoom = Math.max(minZoom, Math.min(maxZoom, realGisMap.getZoom() + direction));
            realGisMap.stop?.();
            realGisMap.easeTo({ zoom: targetZoom, duration: 180, essential: true });
          } catch (error) {
            console.warn("dashboard_maplibre_zoom_failed", error);
            zoomStaticGisMap(direction > 0 ? 0.35 : -0.35);
          }
          return false;
        }
        zoomStaticGisMap(direction > 0 ? 0.35 : -0.35);
        return false;
      };

      const recenterDashboardMap = () => {
        realGisUserAdjustedView = false;
        if (realGisMap && realGisFitBounds) {
          realGisMap.resize();
          realGisMap.fitBounds(realGisFitBounds, { padding: 82, maxZoom: window.__municipalDashboardGisMap?.zoom || 17, duration: 280 });
          return false;
        }
        resetStaticGisZoom();
        return false;
      };

      window.zoomDashboardMap = zoomDashboardMap;
      window.recenterDashboardMap = recenterDashboardMap;

      const setButtonLabel = (selector, value) => {
        const label = document.querySelector(`${selector} span`);
        if (label && value !== undefined && value !== null) {
          label.textContent = String(value);
        }
      };

      const filterDefaults = {
        area: "כל העיר",
        time_range: "כל השנים",
        category: "",
        source_types: "",
        confidence: "כל הרמות"
      };

      const categoryFilterToId = (value) => {
        const compact = String(value || "").trim();
        const map = {
          "תכנון ובנייה": "planning",
          "תחבורה": "transport",
          "חינוך": "education",
          "רווחה": "welfare",
          "סביבה": "environment",
          planning: "planning",
          transport: "transport",
          education: "education",
          welfare: "welfare",
          environment: "environment"
        };
        return map[compact] || "";
      };

      const applySelectedCategoryToRows = (categoryId) => {
        if (!categoryId) {
          return;
        }
        const rows = document.querySelectorAll(".startDiscoveryPanel .discoverySection:nth-of-type(1) .discoveryRow");
        for (const row of rows) {
          const selected = row.dataset.itemId === categoryId;
          row.classList.toggle("selected", selected);
          row.setAttribute("aria-pressed", selected ? "true" : "false");
        }
        if (dashboardRoot) {
          dashboardRoot.dataset.selectedCategoryId = categoryId;
        }
      };

      const normalizeFilters = (filters = {}) => {
        const out = {};
        for (const [key, value] of Object.entries(filters || {})) {
          const compact = String(value || "").trim();
          if (compact && compact !== filterDefaults[key]) {
            out[key] = compact;
          }
        }
        return out;
      };

      const collectFilterValues = () => {
        if (!filterModal) {
          return activeFilters || {};
        }
        const values = {};
        for (const select of filterModal.querySelectorAll("select[name]")) {
          values[select.name] = select.value || "";
        }
        return normalizeFilters(values);
      };

      const syncFilterModal = (filters = {}) => {
        if (!filterModal) {
          return;
        }
        for (const select of filterModal.querySelectorAll("select[name]")) {
          const value = filters[select.name] ?? filterDefaults[select.name] ?? "";
          select.value = value;
        }
      };

      const updateFilterBadge = (count) => {
        const badge = document.querySelector(".filterCountBadge");
        const normalized = Number.isFinite(Number(count)) ? Number(count) : 0;
        if (!badge) {
          return;
        }
        badge.textContent = normalized > 0 ? String(normalized) : "";
        badge.classList.toggle("isVisible", normalized > 0);
      };

      const setIconHeadingText = (selector, value) => {
        const node = document.querySelector(selector);
        if (!node || value === undefined || value === null) {
          return;
        }
        const icon = node.querySelector("svg");
        node.textContent = "";
        if (icon) {
          node.appendChild(icon);
          node.appendChild(document.createTextNode(" "));
        }
        node.appendChild(document.createTextNode(String(value)));
      };

      const setConfidenceBadgeText = (value) => {
        const badge = document.querySelector(".confidenceBadge");
        if (!badge || value === undefined || value === null) {
          return;
        }
        const dot = badge.querySelector(".confidenceDot");
        badge.textContent = "";
        if (dot) {
          badge.appendChild(dot);
        }
        badge.appendChild(document.createTextNode(String(value)));
      };

      const replaceSourceLinkLabel = (link, label) => {
        if (!link) {
          return;
        }
        const icon = link.querySelector("svg");
        link.textContent = "";
        if (icon) {
          link.appendChild(icon);
        }
        link.appendChild(document.createTextNode(label || "מקור"));
      };

      const createTopicChip = (topic) => {
        const chip = document.createElement("a");
        chip.className = "topicChip";
        chip.href = `#${encodeURIComponent(topic.target_topic_id || topic.id || topic.label || "topic")}`;
        chip.setAttribute("role", "listitem");
        chip.dataset.relatedId = topic.id || "";
        chip.dataset.topicId = topic.target_topic_id || topic.id || "";
        chip.textContent = topic.label || "";
        return chip;
      };

      const updateDecisionRows = (decisions, sourceLabel) => {
        const rows = Array.from(document.querySelectorAll(".decisionList .decisionRow"));
        for (let idx = 0; idx < rows.length; idx += 1) {
          const decision = Array.isArray(decisions) ? decisions[idx] : null;
          if (!decision) {
            rows[idx].hidden = true;
            continue;
          }
          rows[idx].hidden = false;
          const number = rows[idx].querySelector(".decisionNumber");
          const text = rows[idx].querySelector(".decisionText");
          const link = rows[idx].querySelector(".sourceLink");
          if (number) {
            number.textContent = `${idx + 1}.`;
          }
          if (text) {
            text.textContent = decision.summary || decision.raw_decision_text || decision.title || "";
          }
          if (link) {
            const evidenceRef = decision.resident_evidence_links?.[0]?.evidence_ref || decision.evidence_refs?.[0] || decision.id;
            link.href = `/api/ui/rag-dashboard/evidence/${encodeURIComponent(evidenceRef)}`;
            link.dataset.sourceLink = decision.id || evidenceRef;
            link.dataset.evidenceRef = evidenceRef;
            replaceSourceLinkLabel(link, decision.resident_evidence_links?.[0]?.label_he || sourceLabel);
          }
        }
      };

      const updateRowList = (selector, rows) => {
        const buttons = Array.from(document.querySelectorAll(`${selector} .discoveryRow, ${selector} .treeRow`));
        for (let idx = 0; idx < buttons.length; idx += 1) {
          const row = Array.isArray(rows) ? rows[idx] : null;
          if (!row) {
            buttons[idx].hidden = true;
            continue;
          }
          buttons[idx].hidden = false;
          buttons[idx].classList.toggle("selected", row.selected === true);
          buttons[idx].setAttribute("aria-pressed", row.selected === true ? "true" : "false");
          buttons[idx].dataset.itemId = row.id || "";
          const label = buttons[idx].querySelector(".rowLabel");
          const count = buttons[idx].querySelector(".countPill");
          if (label) {
            label.textContent = row.label || "";
          }
          if (count) {
            count.textContent = String(row.count ?? "");
          }
        }
      };

      const updateTimeline = (events) => {
        const cards = Array.from(document.querySelectorAll(".timelineCard"));
        const displayEvents = Array.isArray(events) ? [...events].reverse() : [];
        for (let idx = 0; idx < cards.length; idx += 1) {
          const event = displayEvents[idx];
          if (!event) {
            cards[idx].hidden = true;
            continue;
          }
          cards[idx].hidden = false;
          cards[idx].classList.toggle("selected", event.selected === true);
          cards[idx].setAttribute("aria-current", event.selected === true ? "true" : "false");
          cards[idx].dataset.eventId = event.id || "";
          const date = cards[idx].querySelector(".timelineDate");
          const label = cards[idx].querySelector(".timelineLabel");
          const sub = cards[idx].querySelector(".timelineSub");
          if (date) {
            date.textContent = event.date_label || "";
          }
          if (label) {
            label.textContent = event.title || "";
          }
          if (sub) {
            sub.textContent = event.summary || "";
          }
        }
      };

      const poiColor = (category) => {
        if (["school", "municipal_school", "kindergarten"].includes(category)) {
          return "#f59e0b";
        }
        if (category === "transport_stop") {
          return "#0b68d1";
        }
        if (category === "address_point") {
          return "#9333ea";
        }
        if (category === "park") {
          return "#18a66a";
        }
        if (["municipal_poi", "community_center", "culture"].includes(category)) {
          return "#fb923c";
        }
        if (category === "parking") {
          return "#2563eb";
        }
        return "#9ca3af";
      };

      const poiIcon = (category) => {
        if (category === "transport_stop") return "bus";
        if (["school", "municipal_school"].includes(category)) return "school";
        if (["municipal_poi", "community_center", "culture"].includes(category)) return "building";
        if (category === "park") return "tree";
        if (category === "kindergarten") return "child";
        if (category === "address_point") return "home";
        if (category === "parking") return "parking";
        return "pin";
      };

      const compactSourceLabel = (sourceOrFeature) => {
        const source = sourceOrFeature?.source || sourceOrFeature || {};
        const sourceId = source.source_id || sourceOrFeature?.source_id || "";
        if (sourceId === "tel_aviv_open_data_discovered") {
          return "עירוני תל אביב - בבדיקת רישיון";
        }
        if (source.display_status === "municipal_license_under_review") {
          return "עירוני - בבדיקת רישיון";
        }
        if (source.display_status === "context_only") {
          return "OSM - הקשר בלבד";
        }
        return source.name_he || source.name_en || sourceId || "מקור לא ידוע";
      };

      const mapObjectType = (category) => {
        if (category === "transport_stop") return "transport";
        if (["school", "municipal_school", "kindergarten"].includes(category)) return "education";
        if (category === "address_point") return "addresses";
        if (category === "park") return "osm_parks";
        if (category === "interest") return "osm_interest";
        if (["community_center", "culture", "parking", "municipal_poi"].includes(category)) return "municipal_pois";
        return "osm_interest";
      };

      const mapExampleCopy = (example) => ({
        tel_aviv_parcel: "דמו תל אביב בלבד: חלקת MAPI רשמית מודגשת, חלקות MAPI כרשת קדסטרלית עירונית, גבול רשות רשמי, תחבורה/חינוך רשמיים, ו-OSM כהקשר בלבד."
      }[example] || "דמו תל אביב בלבד עם שכבות GIS אמיתיות ו-provenance לכל פריט.");

      const municipalitySlugByCode = {
        "0070": "ashdod",
        "3000": "jerusalem",
        "4000": "haifa",
        "5000": "tel_aviv",
        "9000": "beer_sheva",
        "0831": "yeruham"
      };

      const selectedMunicipalityForQuery = () => {
        const contextCode = String(currentDashboardData?.main_civic_workspace?.map_context?.municipality_code || dashboardRoot?.dataset.gisQuestionMunicipalityCode || "").trim();
        const mapCode = String(window.__municipalDashboardGisMap?.query?.municipality_code || "").trim();
        const code = contextCode || mapCode;
        if (code && municipalitySlugByCode[code]) {
          return municipalitySlugByCode[code];
        }
        return String(dashboardState?.municipality_id || currentDashboardData?.state?.municipality_id || "").trim() || undefined;
      };

      const mapExampleLayers = (example) => {
        const base = { parcel: true, nearbyParcels: false, pois: false, plans: false, boundaries: false, neighborhoods: false, addressPoints: false, municipalPois: false, contextPois: false, buildings: false, basemap: false };
        const configs = {
          tel_aviv_parcel: { nearbyParcels: false, pois: true, plans: true, boundaries: false, neighborhoods: true, addressPoints: false, municipalPois: true, contextPois: false, buildings: false, basemap: false }
        };
        return { ...base, ...(configs[example] || configs.tel_aviv_parcel) };
      };

      const emptyFeatureCollection = () => ({ type: "FeatureCollection", features: [] });

      const metersToLngLat = (center, eastMeters, northMeters) => {
        const lat = Number(center?.lat ?? 31.78);
        const lon = Number(center?.lon ?? 34.78);
        const metersPerLon = Math.max(1, 111320 * Math.cos(lat * Math.PI / 180));
        return [lon + eastMeters / metersPerLon, lat + northMeters / 110540];
      };

      const lineFeature = (center, title, points, properties = {}) => ({
        type: "Feature",
        properties: { title, ...properties },
        geometry: { type: "LineString", coordinates: points.map(([east, north]) => metersToLngLat(center, east, north)) }
      });

      const polygonFeature = (center, title, points, properties = {}) => ({
        type: "Feature",
        properties: { title, ...properties },
        geometry: { type: "Polygon", coordinates: [points.map(([east, north]) => metersToLngLat(center, east, north))] }
      });

      const visualBasemapCollections = (center) => {
        const streets = [];
        for (let index = -5; index <= 5; index += 1) {
          streets.push(lineFeature(center, "רחוב עירוני", [[-3200, index * 430 - 900], [3200, index * 430 + 620]], { class: "street" }));
          streets.push(lineFeature(center, "רחוב עירוני", [[index * 520 - 1100, -2600], [index * 520 + 820, 2600]], { class: "street" }));
        }
        const arterials = [
          lineFeature(center, "ציר ראשי", [[-3000, -1500], [-1200, -720], [900, -620], [2850, -1150]], { class: "arterial" }),
          lineFeature(center, "שדרה עירונית", [[-2100, 1450], [-700, 1050], [950, 1180], [2550, 1580]], { class: "arterial" }),
          lineFeature(center, "דרך חוף", [[-2500, -2350], [-2300, -900], [-2150, 750], [-1950, 2500]], { class: "arterial" })
        ];
        return {
          sea: { type: "FeatureCollection", features: [polygonFeature(center, "חוף הים", [[-4100, -3200], [-2350, -3200], [-2050, 3200], [-4100, 3200], [-4100, -3200]])] },
          parks: { type: "FeatureCollection", features: [
            polygonFeature(center, "פארק", [[-1650, 950], [-1180, 1120], [-1050, 640], [-1550, 520], [-1650, 950]]),
            polygonFeature(center, "פארק", [[1120, 720], [1720, 960], [1920, 520], [1290, 300], [1120, 720]]),
            polygonFeature(center, "פארק", [[-1320, -1220], [-760, -990], [-930, -1570], [-1460, -1480], [-1320, -1220]]),
            polygonFeature(center, "פארק", [[1380, -1280], [1960, -1120], [1840, -1710], [1280, -1620], [1380, -1280]])
          ] },
          water: { type: "FeatureCollection", features: [
            polygonFeature(center, "אגם", [[1950, 1060], [2350, 900], [2440, 520], [2060, 360], [1760, 620], [1950, 1060]]),
            polygonFeature(center, "בריכה", [[820, -1600], [1160, -1470], [1070, -1810], [760, -1770], [820, -1600]])
          ] },
          streets: { type: "FeatureCollection", features: streets },
          arterials: { type: "FeatureCollection", features: arterials },
          transit: { type: "FeatureCollection", features: [lineFeature(center, "תוואי תחבורה ציבורית", [[450, 2800], [340, 1700], [680, 820], [520, -130], [310, -1040], [90, -2600]])] },
          labels: [
            { title: "צפון העיר", coordinates: metersToLngLat(center, 700, 2080), className: "gisMapLabel" },
            { title: "מרכז העיר", coordinates: metersToLngLat(center, -120, 210), className: "gisMapLabel" },
            { title: "מערב העיר", coordinates: metersToLngLat(center, -1750, -350), className: "gisMapLabel" },
            { title: "מזרח העיר", coordinates: metersToLngLat(center, 2050, -120), className: "gisMapLabel" },
            { title: "דרום העיר", coordinates: metersToLngLat(center, -220, -2060), className: "gisMapLabel" },
            { title: "חוף הים", coordinates: metersToLngLat(center, -2950, 950), className: "gisMapLabel gisMapSeaLabel" }
          ]
        };
      };

      const sourceLine = (feature) => {
        return compactSourceLabel(feature);
      };

      const mapPopupHtml = (title, sourceName, sourceId, provenanceId) => `<div class="realGisTooltip"><strong>${escapeHtml(title || "פריט GIS")}</strong><span>סוג/מקור: ${escapeHtml(sourceName || "לא ידוע")}</span><br><span>Source ID: ${escapeHtml(sourceId || "חסר")}</span><br><span>Provenance: ${escapeHtml(provenanceId || "חסר")}</span></div>`;

      const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

      const sourceBadgeHtml = (featureOrSource) => {
        const source = featureOrSource?.source || featureOrSource || {};
        const status = source.display_status || featureOrSource?.display_status || "unavailable";
        const label = compactSourceLabel(featureOrSource);
        return `<span class="sourceBadge" data-status="${escapeHtml(status)}">${escapeHtml(label)}</span>`;
      };

      const featureTitle = (feature) => {
        const raw = feature?.parcel_label || feature?.label || feature?.plan_number || feature?.plan_name || feature?.name_he || feature?.name_en || feature?.poi_category || feature?.municipality_name_he;
        return cleanMapLabel(raw) || "פריט GIS";
      };

      const cleanMapLabel = (value) => {
        const label = String(value || "").trim();
        if (!label || label.includes("מקורות GIS שנמצאו") || label.toLowerCase().includes("discovered sources")) {
          return "";
        }
        return label;
      };

      const primaryMapGeometry = (payload) => payload?.parcel?.geometry || payload?.primary_feature?.geometry || payload?.layers?.plans?.items?.[0]?.geometry || null;

      const selectedAreaFeature = (payload) => {
        return payload?.parcel?.geometry ? { ...payload.parcel, label: "אזור נבחר", feature_type: "selected_area" } : null;
      };

      const layerCount = (layer) => Number(layer?.total_count ?? layer?.count ?? 0);

      const realGisLegendLabels = ["אזור נבחר", "תחבורה ציבורית", "פארקים", "מבני ציבור", "מוקדי עניין"];
      const applyRealGisLegendLabels = () => {
        const labels = Array.from(document.querySelectorAll(".legendItem span:last-child"));
        for (let index = 0; index < labels.length; index += 1) {
          if (realGisLegendLabels[index]) {
            labels[index].textContent = realGisLegendLabels[index];
          }
        }
      };

      const osmContextCount = (payload, key = "displayed_count") => [
        "buildings",
        "context_pois",
        "context_roads",
        "context_railways",
        "context_waterways",
        "context_water",
        "context_landuse",
        "municipal_parks",
        "municipal_beaches",
        "municipal_bike_paths"
      ].reduce((total, layerKey) => total + Number(payload.layers?.[layerKey]?.[key] || payload.layers?.[layerKey]?.count || 0), 0);

      const renderMapLayerCounts = (payload) => {
        const list = document.getElementById("map-layer-counts");
        if (!list || !payload) {
          return;
        }
        const osmLayer = {
          displayed_count: osmContextCount(payload, "displayed_count"),
          total_count: osmContextCount(payload, "total_count"),
        };
        const rows = [
          ["חלקות", payload.layers?.nearby_parcels],
          ["תחבורה/חינוך", payload.nearby_pois],
          ["OSM", osmLayer],
        ].filter(([, layer]) => layer && layerCount(layer) > 0);
        list.replaceChildren(...rows.map(([label, layer]) => {
          const item = document.createElement("li");
          const labelNode = document.createElement("span");
          const countNode = document.createElement("strong");
          labelNode.textContent = label;
          const displayed = Number(layer.displayed_count ?? layer.count ?? 0);
          const total = layerCount(layer);
          countNode.textContent = total > displayed ? `${displayed}/${total}` : `${displayed}`;
          item.append(labelNode, countNode);
          return item;
        }));
      };

      const renderMapLayerSummary = (payload) => {
        const list = document.getElementById("map-layer-summary");
        if (!list || !payload) {
          return;
        }
        const transport = payload.nearby_pois?.categories?.transport_stop || {};
        const schools = payload.nearby_pois?.categories?.school || {};
        const osmDisplayed = osmContextCount(payload, "displayed_count");
        const rows = [
          { key: "selected_parcel", label: "אזור נבחר", count: selectedAreaFeature(payload) ? 1 : 0 },
          { key: "nearby_parcels", label: "חלקות בעיר", count: Number(payload.layers?.nearby_parcels?.displayed_count || payload.layers?.nearby_parcels?.count || 0) },
          { key: "transport", label: "תחבורה", count: Number(transport.displayed_count || 0) },
          { key: "schools", label: "חינוך", count: Number(schools.displayed_count || 0) },
          { key: "osm", label: "OSM הקשר", count: osmDisplayed },
        ].filter((row) => row.count > 0);
        list.replaceChildren(...rows.map((row) => {
          const item = document.createElement("li");
          item.className = "mapLayerSummaryRow";
          item.dataset.layer = row.key;
          const swatch = document.createElement("span");
          swatch.className = "mapLayerSummarySwatch";
          const label = document.createElement("span");
          label.textContent = row.label;
          const count = document.createElement("strong");
          count.className = "mapLayerSummaryCount";
          count.textContent = String(row.count);
          item.append(swatch, label, count);
          return item;
        }));
      };

      const mapContextLayerLabel = (layerKey) => ({
        selected_parcel: "חלקה נבחרת",
        selected_plan: "תכנית נבחרת",
        municipality: "רשות מקומית",
        plans: "תכניות",
        neighborhoods: "שכונה / רובע",
        transport_stops: "תחבורה",
        schools: "חינוך",
        context_roads: "דרכים OSM",
        context_landuse: "שטחים פתוחים OSM",
        buildings: "מבנים OSM"
      }[layerKey] || layerKey || "שכבה");

      const renderDashboardMapContext = (mapContext) => {
        if (!mapContext || typeof mapContext !== "object") {
          return;
        }
        const layers = Array.isArray(mapContext.layers) ? mapContext.layers : [];
        const summary = document.getElementById("map-layer-summary");
        if (summary) {
          const rows = layers
            .filter((layer) => Number(layer?.count || 0) > 0 || ["not_found", "not_ingested", "not_enabled", "municipal_license_under_review", "context_only"].includes(layer?.status))
            .map((layer) => ({ key: layer.layer_key || "layer", label: mapContextLayerLabel(layer.layer_key), count: Number(layer.count || 0), status: layer.status || "unknown" }));
          summary.replaceChildren(...rows.map((row) => {
            const item = document.createElement("li");
            item.className = "mapLayerSummaryRow";
            item.dataset.layer = row.key;
            item.title = row.status;
            const swatch = document.createElement("span");
            swatch.className = "mapLayerSummarySwatch";
            const label = document.createElement("span");
            label.textContent = row.label;
            const count = document.createElement("strong");
            count.className = "mapLayerSummaryCount";
            count.textContent = String(row.count);
            item.append(swatch, label, count);
            return item;
          }));
        }
        const layerCounts = document.getElementById("map-layer-counts");
        if (layerCounts) {
          const rows = layers.filter((layer) => Number(layer?.count || 0) > 0).slice(0, 6);
          layerCounts.replaceChildren(...rows.map((layer) => {
            const item = document.createElement("li");
            const label = document.createElement("span");
            const count = document.createElement("strong");
            label.textContent = mapContextLayerLabel(layer.layer_key);
            count.textContent = String(layer.count || 0);
            item.append(label, count);
            return item;
          }));
        }
        const caveats = Array.isArray(mapContext.caveats) ? mapContext.caveats.filter(Boolean) : [];
        if (caveats.length) {
          setText(".mapProvenanceBadgeTitle", "הקשר GIS לשאלה");
          setText(".mapProvenanceBadgeText", caveats[0]);
          setAttr(".mapProvenanceBadge", "data-spatial-representation", "gis_context");
        }
        if (dashboardRoot) {
          dashboardRoot.dataset.gisQuestionContextStatus = mapContext.status || "";
          dashboardRoot.dataset.gisQuestionMunicipalityCode = mapContext.municipality_code || "";
          dashboardRoot.dataset.gisQuestionLayerCount = String(layers.length);
        }
      };

      const setPanelHidden = (id, hidden) => {
        const panel = document.getElementById(id);
        if (panel) {
          panel.hidden = hidden;
        }
      };

      const featureListHtml = (items = []) => {
        if (!Array.isArray(items) || items.length === 0) {
          return "";
        }
        const visibleItems = items.slice(0, 4);
        const extraCount = Math.max(0, items.length - visibleItems.length);
        return `<ul class="pointFeatureList">${visibleItems.map((item) => `<li class="pointFeatureItem"><strong>${escapeHtml(featureTitle(item))}</strong>${sourceBadgeHtml(item)}<span>Provenance: ${escapeHtml(item.provenance_id || "חסר")}</span></li>`).join("")}${extraCount ? `<li class="pointFeatureItem compact">עוד ${extraCount} פריטים מוסתרים כדי לא להסתיר את המפה.</li>` : ""}</ul>`;
      };

      const pointLayerHtml = (title, layer) => {
        const status = layer?.status || "unavailable";
        const message = layer?.message || (status === "found" ? "" : `שכבה לא זמינה: ${status}`);
        const items = layer?.items || [];
        return `<section class="pointReportSection"><h4>${escapeHtml(title)} · ${escapeHtml(status)}</h4>${message ? `<p class="pointReportMessage">${escapeHtml(message)}</p>` : ""}${featureListHtml(items)}</section>`;
      };

      const renderPointReport = (payload) => {
        const panel = document.getElementById("point-report-panel");
        const body = panel?.querySelector(".pointReportSections");
        if (!panel || !body) {
          return;
        }
        if (!payload || payload.municipality?.status === "not_found") {
          body.innerHTML = `<p class="pointReportMessage">${escapeHtml(payload?.municipality?.message || "לא נמצאה רשות מקומית בנקודה שנבחרה.")}</p>`;
          panel.hidden = false;
          return;
        }
        const municipality = payload.municipality || {};
        body.innerHTML = [
          `<section class="pointReportSection"><h4>רשות מקומית</h4><strong>${escapeHtml(municipality.municipality_name_he || municipality.municipality_code || "לא ידוע")}</strong>${sourceBadgeHtml(municipality)}<span>Provenance: ${escapeHtml(municipality.provenance_id || "חסר")}</span></section>`,
          pointLayerHtml("חלקה", payload.parcel),
          pointLayerHtml("תכניות", payload.plans),
          pointLayerHtml("שכונה / רובע", payload.neighborhood),
          pointLayerHtml("נקודות עניין סמוכות", payload.nearby_pois),
        ].join("");
        panel.hidden = false;
      };

      const loadPointReport = async (lon, lat) => {
        const panel = document.getElementById("point-report-panel");
        const body = panel?.querySelector(".pointReportSections");
        if (panel && body) {
          body.innerHTML = `<p class="pointReportMessage">טוען דוח נקודה...</p>`;
          panel.hidden = false;
        }
        const response = await fetch(`${POINT_REPORT_ENDPOINT}?lon=${encodeURIComponent(lon)}&lat=${encodeURIComponent(lat)}`, { headers: { Accept: "application/json" } });
        if (!response.ok) {
          throw new Error(`point-report HTTP ${response.status}`);
        }
        const payload = await response.json();
        renderPointReport(payload);
      };

      const coverageStatusLabel = (status) => ({
        national_available: "ארצי זמין",
        municipal_open_available: "עירוני פתוח",
        municipal_license_under_review: "בבדיקת רישיון",
        context_available: "הקשר זמין",
        not_found: "לא נמצא",
        not_enabled: "לא מופעל",
        not_ingested: "טרם נטען"
      }[status] || status || "לא ידוע");

      const renderCoverage = (payload) => {
        const body = document.getElementById("coverage-panel-body");
        if (!body) {
          return;
        }
        const items = payload?.items || [];
        if (!items.length) {
          body.innerHTML = `<p class="pointReportMessage">לא נמצאו רשומות כיסוי להצגה.</p>`;
          return;
        }
        body.innerHTML = `<table class="coverageGrid"><thead><tr><th>רשות</th><th>שכבה</th><th>סטטוס</th><th>מקור</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escapeHtml(item.municipality_name_he || item.municipality_code)}</td><td>${escapeHtml(item.layer_key)}</td><td>${sourceBadgeHtml({ source: item.source || { display_status: item.status, name_he: coverageStatusLabel(item.status) } })}</td><td>${escapeHtml(item.source_id || "-")}</td></tr>`).join("")}</tbody></table>`;
      };

      const loadCoverage = async () => {
        const button = document.getElementById("coverage-matrix-button");
        const mapLayerButton = document.getElementById("map-view-layers");
        const panel = document.getElementById("coverage-panel");
        const body = document.getElementById("coverage-panel-body");
        if (panel) {
          panel.hidden = false;
        }
        if (button) {
          button.setAttribute("aria-expanded", "true");
        }
        if (mapLayerButton) {
          mapLayerButton.setAttribute("aria-expanded", "true");
        }
        if (body) {
          body.innerHTML = `<p class="pointReportMessage">טוען מטריצת כיסוי...</p>`;
        }
        const response = await fetch(COVERAGE_ENDPOINT, { headers: { Accept: "application/json" } });
        if (!response.ok) {
          throw new Error(`coverage HTTP ${response.status}`);
        }
        renderCoverage(await response.json());
      };

      const collectGeoPoints = (coordinates, out = []) => {
        if (Array.isArray(coordinates) && coordinates.length >= 2 && coordinates.slice(0, 2).every((value) => Number.isFinite(Number(value)))) {
          out.push([Number(coordinates[0]), Number(coordinates[1])]);
          return out;
        }
        if (Array.isArray(coordinates)) {
          for (const item of coordinates) {
            collectGeoPoints(item, out);
          }
        }
        return out;
      };

      const viewBoxString = (box) => `${box.x.toFixed(2)} ${box.y.toFixed(2)} ${box.width.toFixed(2)} ${box.height.toFixed(2)}`;

      const boxFromProjectedPoints = (points) => {
        if (!points.length) {
          return null;
        }
        const xs = points.map((point) => point[0]);
        const ys = points.map((point) => point[1]);
        return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
      };

      const fitBoxToAspect = (box, aspect) => {
        if (!box) {
          return null;
        }
        const currentAspect = box.width / (box.height || 1);
        if (currentAspect > aspect) {
          const height = box.width / aspect;
          return { ...box, y: box.y - (height - box.height) / 2, height };
        }
        const width = box.height * aspect;
        return { ...box, x: box.x - (width - box.width) / 2, width };
      };

      const clampBoxToWorld = (box, world) => {
        const width = Math.min(world.width, Math.max(1, box.width));
        const height = Math.min(world.height, Math.max(1, box.height));
        return {
          x: Math.max(world.x, Math.min(world.x + world.width - width, box.x)),
          y: Math.max(world.y, Math.min(world.y + world.height - height, box.y)),
          width,
          height,
        };
      };

      const focusViewBoxForFeature = (feature, project) => {
        const points = collectGeoPoints(feature?.geometry?.coordinates || []).map(project);
        const raw = boxFromProjectedPoints(points);
        const world = project.worldViewBox || { x: 0, y: 0, width: 900, height: 620 };
        if (!raw) {
          return world;
        }
        const padX = Math.max(raw.width * 0.72, 38);
        const padY = Math.max(raw.height * 0.72, 26);
        const padded = { x: raw.x - padX, y: raw.y - padY, width: raw.width + padX * 2, height: raw.height + padY * 2 };
        return clampBoxToWorld(fitBoxToAspect(padded, 900 / 620), world);
      };

      const staticProjection = (payload) => {
        const areaFeature = selectedAreaFeature(payload);
        const worldPoints = [];
        for (const item of payload.layers?.municipal_boundaries?.items || []) {
          collectGeoPoints(item.geometry?.coordinates || [], worldPoints);
        }
        for (const item of payload.layers?.neighboring_municipal_boundaries?.items || []) {
          collectGeoPoints(item.geometry?.coordinates || [], worldPoints);
        }
        if (worldPoints.length === 0) {
          collectGeoPoints(areaFeature?.geometry?.coordinates || [], worldPoints);
          collectGeoPoints(primaryMapGeometry(payload)?.coordinates || [], worldPoints);
          for (const layer of Object.values(payload.layers || {})) {
            for (const item of layer?.items || []) {
              collectGeoPoints(item.geometry?.coordinates || [], worldPoints);
            }
          }
          for (const poi of payload.nearby_pois?.items || []) {
            collectGeoPoints(poi.geometry?.coordinates || [], worldPoints);
          }
        }
        if (worldPoints.length === 0) {
          return null;
        }
        const lons = worldPoints.map((point) => point[0]);
        const lats = worldPoints.map((point) => point[1]);
        let minLon = Math.min(...lons);
        let maxLon = Math.max(...lons);
        let minLat = Math.min(...lats);
        let maxLat = Math.max(...lats);
        const lonPad = Math.max((maxLon - minLon) * 0.18, 0.001);
        const latPad = Math.max((maxLat - minLat) * 0.18, 0.001);
        minLon -= lonPad;
        maxLon += lonPad;
        minLat -= latPad;
        maxLat += latPad;
        const project = ([lon, lat]) => {
          const x = ((lon - minLon) / (maxLon - minLon || 1)) * 900;
          const y = 620 - ((lat - minLat) / (maxLat - minLat || 1)) * 620;
          return [x, y];
        };
        project.bounds = { minLon, minLat, maxLon, maxLat };
        project.worldViewBox = { x: 0, y: 0, width: 900, height: 620 };
        project.focusViewBox = focusViewBoxForFeature(areaFeature || { geometry: primaryMapGeometry(payload) }, project);
        return project;
      };

      const expandBounds = (bounds, factor) => {
        const lonCenter = (bounds.minLon + bounds.maxLon) / 2;
        const latCenter = (bounds.minLat + bounds.maxLat) / 2;
        const lonHalf = ((bounds.maxLon - bounds.minLon) || 0.001) * factor / 2;
        const latHalf = ((bounds.maxLat - bounds.minLat) || 0.001) * factor / 2;
        return {
          minLon: lonCenter - lonHalf,
          maxLon: lonCenter + lonHalf,
          minLat: latCenter - latHalf,
          maxLat: latCenter + latHalf,
        };
      };

      const polygonPaths = (geometry, project) => {
        const polygons = geometry?.type === "Polygon" ? [geometry.coordinates] : geometry?.coordinates || [];
        return polygons.map((polygon) => {
          const rings = Array.isArray(polygon) ? polygon : [];
          return rings.map((ring) => {
            const projected = (ring || []).map(project);
            if (projected.length === 0) {
              return "";
            }
            return `M ${projected.map((point) => `${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join(" L ")} Z`;
          }).join(" ");
        }).join(" ");
      };

      const linePaths = (geometry, project) => {
        const lines = geometry?.type === "LineString" ? [geometry.coordinates] : geometry?.coordinates || [];
        return lines.map((line) => {
          const projected = (line || []).map(project);
          if (projected.length === 0) {
            return "";
          }
          return `M ${projected.map((point) => `${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join(" L ")}`;
        }).join(" ");
      };

      const geometryLabelPoint = (geometry, project) => {
        const points = collectGeoPoints(geometry?.coordinates || []);
        if (!points.length) {
          return null;
        }
        const lon = points.reduce((total, point) => total + point[0], 0) / points.length;
        const lat = points.reduce((total, point) => total + point[1], 0) / points.length;
        return project([lon, lat]);
      };

      const showStaticFeatureTooltip = (event, feature) => {
        const mapFrame = document.querySelector(".mapFrame");
        if (!mapFrame) {
          return;
        }
        let tooltip = mapFrame.querySelector(".realGisHoverCard");
        if (!tooltip) {
          tooltip = document.createElement("div");
          tooltip.className = "realGisHoverCard";
          mapFrame.appendChild(tooltip);
        }
        const rect = mapFrame.getBoundingClientRect();
        tooltip.innerHTML = mapPopupHtml(featureTitle(feature), sourceLine(feature), feature?.source_id, feature?.provenance_id);
        tooltip.style.left = `${Math.min(Math.max(8, event.clientX - rect.left + 12), rect.width - 250)}px`;
        tooltip.style.top = `${Math.min(Math.max(8, event.clientY - rect.top + 12), rect.height - 95)}px`;
      };

      window.__showMunicipalMapTooltip = showStaticFeatureTooltip;

      window.__hideMunicipalMapTooltip = () => {
        document.querySelector(".realGisHoverCard")?.remove();
      };

      const hideStaticFeatureTooltip = () => {
        document.querySelector(".realGisHoverCard")?.remove();
      };

      const showSchematicFeatureTooltip = (event, node) => {
        const title = node?.dataset?.mapTooltipTitle || node?.getAttribute?.("aria-label") || "פריט מפה";
        showStaticFeatureTooltip(event, {
          label: title,
          source_id: "schematic_map",
          provenance_id: "fallback_schematic",
          source: { name_he: "מפה סכמטית", display_status: "context_only" }
        });
      };

      const decorateStaticFeature = (node, feature, title) => {
        node.classList.add("realGisStaticFeature");
        node.setAttribute("tabindex", "0");
        node.setAttribute("role", "button");
        node.setAttribute("aria-label", title || featureTitle(feature));
        const label = document.createElementNS("http://www.w3.org/2000/svg", "title");
        label.textContent = title || `${featureTitle(feature)} · ${feature?.source_id || "מקור לא ידוע"}`;
        node.appendChild(label);
        node.addEventListener("mouseenter", (event) => showStaticFeatureTooltip(event, feature));
        node.addEventListener("mousemove", (event) => showStaticFeatureTooltip(event, feature));
        node.addEventListener("mouseleave", hideStaticFeatureTooltip);
        node.addEventListener("click", (event) => {
          if (window.__municipalSvgSuppressFeatureClick) {
            event.preventDefault();
            event.stopPropagation();
            hideStaticFeatureTooltip();
            return;
          }
          showStaticFeatureTooltip(event, feature);
        });
      };

      const appendStaticPolygonFeature = (svg, feature, project, attrs, title) => {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", polygonPaths(feature.geometry, project));
        for (const [key, value] of Object.entries(attrs)) {
          path.setAttribute(key, value);
        }
        decorateStaticFeature(path, feature, title);
        svg.appendChild(path);
        return path;
      };

      const appendStaticLineFeature = (svg, feature, project, attrs, title) => {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", linePaths(feature.geometry, project));
        for (const [key, value] of Object.entries(attrs)) {
          path.setAttribute(key, value);
        }
        path.setAttribute("fill", "none");
        decorateStaticFeature(path, feature, title);
        svg.appendChild(path);
        return path;
      };

      const appendStaticTextLabel = (svg, feature, project, label, attrs = {}) => {
        const point = geometryLabelPoint(feature.label_point || feature.geometry, project);
        if (!point || !label) {
          return null;
        }
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.classList.add("realGisStaticLabel");
        text.setAttribute("x", point[0].toFixed(1));
        text.setAttribute("y", point[1].toFixed(1));
        const baseFontSize = Number(attrs["font-size"] || 13);
        text.setAttribute("data-map-label", "true");
        text.setAttribute("data-base-font-size", String(baseFontSize));
        const scaledFontSize = baseFontSize * (window.__municipalMapLabelScale || 0.18);
        text.setAttribute("font-size", String(scaledFontSize));
        text.style.fontSize = `${scaledFontSize}px`;
        for (const [key, value] of Object.entries(attrs)) {
          if (key === "font-size") {
            continue;
          }
          text.setAttribute(key, value);
        }
        text.textContent = label;
        svg.appendChild(text);
        return text;
      };

      const appendStaticLayerTextLabels = (svg, items, project, objectType, fallbackLabel = "") => {
        for (const item of items || []) {
          const label = cleanMapLabel(item?.name_he || item?.name_en || fallbackLabel);
          if (!label) {
            continue;
          }
          appendStaticTextLabel(svg, item, project, label, {
            "data-map-layer": "municipal",
            "data-map-object": objectType,
            fill: "#222831",
            "font-size": "11"
          });
        }
      };

      const appendStaticPillLabel = (svg, feature, project, label, attrs = {}) => {
        const point = geometryLabelPoint(feature.geometry, project);
        if (!point || !label) {
          return null;
        }
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const width = Math.max(20, Math.min(42, String(label).length * 2.7 + 7));
        const height = 5.4;
        group.setAttribute("data-map-layer", attrs["data-map-layer"] || "parcel");
        group.setAttribute("data-map-object", attrs["data-map-object"] || "parcels");
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", (point[0] - width / 2).toFixed(1));
        rect.setAttribute("y", (point[1] - height / 2).toFixed(1));
        rect.setAttribute("width", width.toFixed(1));
        rect.setAttribute("height", String(height));
        rect.setAttribute("rx", "2.4");
        rect.setAttribute("fill", attrs.fill || "#7c4dce");
        rect.setAttribute("opacity", "0.94");
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", point[0].toFixed(1));
        text.setAttribute("y", (point[1] + 1.05).toFixed(1));
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("direction", "rtl");
        text.setAttribute("font-size", attrs["font-size"] || "2.8");
        text.setAttribute("font-weight", "900");
        text.setAttribute("fill", attrs["text-fill"] || "#ffffff");
        text.textContent = label;
        group.append(rect, text);
        svg.appendChild(group);
        return group;
      };

      const iconGlyph = (kind) => ({
        bus: "M -9 -5 H 9 V 5 H 7 V 8 H 4 V 5 H -4 V 8 H -7 V 5 H -9 Z M -6 -2 H -1 V 1 H -6 Z M 1 -2 H 6 V 1 H 1 Z",
        school: "M -9 -1 L 0 -8 L 9 -1 L 6 -1 V 8 H -6 V -1 Z M -2 2 H 2 V 8 H -2 Z",
        building: "M -8 8 V -7 H 8 V 8 H 4 V 4 H 1 V 8 H -1 V 4 H -4 V 8 Z M -5 -4 H -3 V -2 H -5 Z M -1 -4 H 1 V -2 H -1 Z M 3 -4 H 5 V -2 H 3 Z M -5 0 H -3 V 2 H -5 Z M 3 0 H 5 V 2 H 3 Z",
        tree: "M 0 -9 C -5 -9 -8 -5 -6 -1 C -10 1 -8 6 -3 5 H -1 V 9 H 1 V 5 H 3 C 8 6 10 1 6 -1 C 8 -5 5 -9 0 -9 Z",
        child: "M 0 -8 A 3 3 0 1 1 0 -2 A 3 3 0 1 1 0 -8 M -7 8 L -3 -1 H 3 L 7 8 H 3 L 1 3 H -1 L -3 8 Z",
        home: "M -8 0 L 0 -7 L 8 0 H 5 V 8 H -5 V 0 Z",
        parking: "M -8 -8 H 2 C 7 -8 8 -1 3 1 H -3 V 8 H -8 Z M -3 -4 V -1 H 1 C 3 -1 3 -4 1 -4 Z",
        culture: "M -8 -6 H 8 V 2 C 8 6 4 8 0 8 C -4 8 -8 6 -8 2 Z M -5 -3 C -3 -1 -1 -1 1 -3 M 3 -3 C 4 -1 5 -1 6 -3",
        community: "M -8 8 V 0 L -3 -4 L 0 -1 L 3 -4 L 8 0 V 8 H 3 V 3 H -3 V 8 Z",
        pin: "M 0 9 C -5 3 -7 0 -7 -4 A 7 7 0 1 1 7 -4 C 7 0 5 3 0 9 Z M 0 -1 A 3 3 0 1 0 0 -7 A 3 3 0 1 0 0 -1"
      }[kind] || "M 0 9 C -5 3 -7 0 -7 -4 A 7 7 0 1 1 7 -4 C 7 0 5 3 0 9 Z");

      const appendStaticIconNode = (svg, feature, x, y, category, layer, title, objectTypeOverride = null) => {
        const color = poiColor(category);
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const baseTransform = `translate(${x.toFixed(1)} ${y.toFixed(1)})`;
        group.setAttribute("transform", `${baseTransform} scale(${window.__municipalMapIconScale || 0.12})`);
        group.setAttribute("data-map-icon", "true");
        group.setAttribute("data-base-transform", baseTransform);
        group.setAttribute("data-map-layer", layer);
        group.setAttribute("data-map-object", objectTypeOverride || mapObjectType(category));
        group.setAttribute("opacity", "0.92");
        const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        halo.setAttribute("r", "15");
        halo.setAttribute("fill", "rgba(255,255,255,0.90)");
        halo.setAttribute("stroke", color);
        halo.setAttribute("stroke-width", "1.4");
        const glyph = document.createElementNS("http://www.w3.org/2000/svg", "path");
        glyph.setAttribute("d", iconGlyph(poiIcon(category)));
        glyph.setAttribute("fill", color);
        glyph.setAttribute("stroke", "none");
        group.append(halo, glyph);
        decorateStaticFeature(group, feature, title);
        svg.appendChild(group);
        return group;
      };

      const appendStaticIconFeature = (svg, feature, project, layer, title, objectTypeOverride = null) => {
        const coordinates = feature.geometry?.coordinates;
        if (!Array.isArray(coordinates) || coordinates.length < 2) {
          return null;
        }
        const [x, y] = project([Number(coordinates[0]), Number(coordinates[1])]);
        const category = feature.poi_category || feature.feature_type || "pin";
        return appendStaticIconNode(svg, feature, x, y, category, layer, title, objectTypeOverride);
      };

      const appendStaticGeometryIconFeature = (svg, feature, project, category, layer, objectType, title) => {
        const point = geometryLabelPoint(feature.geometry, project);
        if (!point) {
          return null;
        }
        return appendStaticIconNode(svg, { ...feature, poi_category: category }, point[0], point[1], category, layer, title, objectType);
      };

      const appendStaticBasemapImage = (svg, project) => {
        if (!project?.bounds) {
          return;
        }
        const { minLon, minLat, maxLon, maxLat } = project.bounds;
        const params = new URLSearchParams({
          f: "image",
          bbox: `${minLon},${minLat},${maxLon},${maxLat}`,
          bboxSR: "4326",
          imageSR: "4326",
          size: "900,620",
          format: "png32",
          transparent: "false"
        });
        const image = document.createElementNS("http://www.w3.org/2000/svg", "image");
        image.setAttribute("x", "0");
        image.setAttribute("y", "0");
        image.setAttribute("width", "900");
        image.setAttribute("height", "620");
        image.setAttribute("preserveAspectRatio", "none");
        image.setAttribute("href", `https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2MapHeb/MapServer/export?${params.toString()}`);
        image.setAttribute("data-map-layer", "basemap");
        image.setAttribute("data-unfilterable-raster", "true");
        image.setAttribute("opacity", "0.42");
        svg.appendChild(image);
      };

      const shouldRenderMapItem = (layer, objectType = null) => {
        const layerInput = document.querySelector(`[data-map-layer-toggle="${layer}"]`);
        const objectInput = objectType ? document.querySelector(`[data-map-object-toggle="${objectType}"]`) : null;
        return (!layerInput || layerInput.checked) && (!objectInput || objectInput.checked);
      };

      const appendStaticLandMask = (svg, payload, project) => {
        for (const item of payload.layers?.neighboring_municipal_boundaries?.items || []) {
          if (!item?.geometry) {
            continue;
          }
          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          path.setAttribute("d", polygonPaths(item.geometry, project));
          path.setAttribute("data-map-layer", "land");
          path.setAttribute("data-map-object", "neighboring_municipality");
          path.setAttribute("fill", "#edf2ee");
          path.setAttribute("fill-opacity", "0.88");
          path.setAttribute("stroke", "rgba(100, 116, 139, 0.34)");
          path.setAttribute("stroke-width", "0.85");
          path.setAttribute("stroke-linejoin", "round");
          path.style.pointerEvents = "none";
          svg.appendChild(path);
        }
        for (const item of payload.layers?.municipal_boundaries?.items || []) {
          if (!item?.geometry) {
            continue;
          }
          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          path.setAttribute("d", polygonPaths(item.geometry, project));
          path.setAttribute("data-map-layer", "land");
          path.setAttribute("data-map-object", "land");
          path.setAttribute("fill", "#edf2ee");
          path.setAttribute("fill-opacity", "0.96");
          path.setAttribute("stroke", "rgba(15, 118, 110, 0.74)");
          path.setAttribute("stroke-width", "1.7");
          path.setAttribute("stroke-linejoin", "round");
          path.style.pointerEvents = "none";
          svg.appendChild(path);
        }
      };

      const renderStaticGisMap = (payload, options = {}) => {
        const targetSvg = document.getElementById("real-gis-static-map");
        const mapFrame = document.querySelector(".mapFrame");
        const project = staticProjection(payload || {});
        const primaryGeometry = primaryMapGeometry(payload);
        if (!targetSvg || !mapFrame || !primaryGeometry || !project) {
          return false;
        }
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        try {
        const previousViewBox = targetSvg.getAttribute("viewBox");
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = payload.title_he || "מפת GIS אמיתית";
        svg.appendChild(title);
        const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        background.setAttribute("x", "0");
        background.setAttribute("y", "0");
        background.setAttribute("width", "900");
        background.setAttribute("height", "620");
        background.setAttribute("fill", "#bde7f7");
        svg.appendChild(background);
        if (shouldRenderMapItem("basemap")) {
          appendStaticBasemapImage(svg, project);
        }
        if (shouldRenderMapItem("osm", "osm_water")) {
          for (const item of payload.layers?.context_water?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_water", fill: "rgba(164, 216, 235, 0.44)", stroke: "rgba(87, 167, 199, 0.22)", "stroke-width": "0.45", "stroke-linejoin": "round" }, `${featureTitle(item)} · מים OSM`);
          }
          for (const item of payload.layers?.context_waterways?.items || []) {
            appendStaticLineFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_water", stroke: "rgba(87, 167, 199, 0.45)", "stroke-width": "1.1", "stroke-linecap": "round", "stroke-linejoin": "round" }, `${featureTitle(item)} · ערוץ מים OSM`);
          }
        }
        appendStaticLandMask(svg, payload, project);
        if (shouldRenderMapItem("municipal", "municipal_beaches")) {
          for (const item of payload.layers?.municipal_beaches?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "municipal", "data-map-object": "municipal_beaches", fill: "rgba(164, 216, 235, 0.34)", stroke: "rgba(87, 167, 199, 0.30)", "stroke-width": "0.55", "stroke-linejoin": "round" }, `${featureTitle(item)} · חוף עירוני בבדיקת רישיון`);
          }
        }
        if (shouldRenderMapItem("osm", "osm_parks")) {
          for (const item of payload.layers?.context_landuse?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_parks", fill: "rgba(190, 233, 203, 0.54)", stroke: "rgba(93, 173, 120, 0.16)", "stroke-width": "0.45", "stroke-linejoin": "round" }, `${featureTitle(item)} · שימוש קרקע OSM`);
          }
        }
        if (shouldRenderMapItem("municipal", "municipal_parks")) {
          for (const item of payload.layers?.municipal_parks?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "municipal", "data-map-object": "municipal_parks", fill: "rgba(190, 233, 203, 0.42)", stroke: "rgba(74, 160, 104, 0.18)", "stroke-width": "0.45", "stroke-linejoin": "round" }, `${featureTitle(item)} · גן/פארק עירוני בבדיקת רישיון`);
          }
        }
        if (shouldRenderMapItem("osm", "osm_roads")) {
          for (const item of payload.layers?.context_roads?.items || []) {
            appendStaticLineFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_roads", stroke: "rgba(148, 163, 184, 0.42)", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" }, `${featureTitle(item)} · דרך OSM`);
            appendStaticLineFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_roads", stroke: "rgba(255, 255, 255, 0.72)", "stroke-width": "1.25", "stroke-linecap": "round", "stroke-linejoin": "round" }, `${featureTitle(item)} · דרך OSM`);
          }
        }
        if (shouldRenderMapItem("osm", "osm_railways")) {
          for (const item of payload.layers?.context_railways?.items || []) {
            appendStaticLineFeature(svg, item, project, { "data-map-layer": "osm", "data-map-object": "osm_railways", stroke: "rgba(11, 104, 209, 0.58)", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-dasharray": "7 7" }, `${featureTitle(item)} · תוואי תחבורה OSM - הקשר בלבד`);
          }
        }
        if (shouldRenderMapItem("municipal", "municipal_bike_paths")) {
          for (const item of payload.layers?.municipal_bike_paths?.items || []) {
            appendStaticLineFeature(svg, item, project, { "data-map-layer": "municipal", "data-map-object": "municipal_bike_paths", stroke: "rgba(6, 182, 212, 0.46)", "stroke-width": "1", "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-dasharray": "5 5" }, `${featureTitle(item)} · שביל אופניים עירוני בבדיקת רישיון`);
          }
        }
        if (shouldRenderMapItem("nearby_parcels", "parcels")) {
          for (const item of payload.layers?.nearby_parcels?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "nearby_parcels", "data-map-object": "parcels", fill: "none", stroke: "rgba(101, 84, 52, 0.55)", "stroke-width": "0.55", "stroke-linejoin": "round" }, `${featureTitle(item)} · חלקת MAPI בעיר`);
          }
        }
        if (shouldRenderMapItem("municipal", "buildings")) {
          for (const item of payload.layers?.buildings?.items || []) {
            appendStaticPolygonFeature(svg, item, project, { "data-map-layer": "municipal", "data-map-object": "buildings", fill: "rgba(68, 76, 86, 0.26)", stroke: "rgba(30, 41, 59, 0.48)", "stroke-width": "0.42", "stroke-linejoin": "round" }, `${featureTitle(item)} · מבנה עירוני בבדיקת רישיון`);
          }
        }
        if (shouldRenderMapItem("parcel", "parcels")) {
          const selectedArea = selectedAreaFeature(payload);
          if (selectedArea?.geometry) {
            appendStaticPolygonFeature(svg, selectedArea, project, { "data-map-layer": "parcel", "data-map-object": "selected_area", fill: "rgba(124, 77, 206, 0.20)", stroke: "rgba(255,255,255,0.96)", "stroke-width": "7", "stroke-linejoin": "round" }, "אזור נבחר · פוליגון עירוני אמיתי");
            appendStaticPolygonFeature(svg, selectedArea, project, { "data-map-layer": "parcel", "data-map-object": "selected_area", fill: "rgba(124, 77, 206, 0.22)", stroke: "#7c4dce", "stroke-width": "2.6", "stroke-linejoin": "round" }, "אזור נבחר · פוליגון עירוני אמיתי");
          }
          const selectedParcel = { ...payload.parcel, geometry: primaryGeometry };
          appendStaticPolygonFeature(svg, selectedParcel, project, { "data-map-layer": "parcel", "data-map-object": "selected_area", fill: "rgba(255,255,255,0.52)", stroke: "#5b21b6", "stroke-width": "2.1", "stroke-linejoin": "round" }, `${payload.parcel?.label || "חלקת MAPI"} · חלקת MAPI 7103/43`);
        }
        if (shouldRenderMapItem("osm", "osm_parks")) {
          for (const item of (payload.layers?.context_landuse?.items || []).slice(0, MAP_PARK_ICON_LIMIT)) {
            appendStaticGeometryIconFeature(svg, item, project, "park", "osm", "osm_parks", `${featureTitle(item)} · פארק/שימוש קרקע OSM`);
          }
        }
        if (shouldRenderMapItem("municipal", "municipal_parks")) {
          for (const item of (payload.layers?.municipal_parks?.items || []).slice(0, MAP_PARK_ICON_LIMIT)) {
            appendStaticGeometryIconFeature(svg, item, project, "park", "municipal", "municipal_parks", `${featureTitle(item)} · גן/פארק עירוני בבדיקת רישיון`);
          }
        }
        for (const poi of (payload.nearby_pois?.items || []).slice(0, MAP_POI_ICON_LIMIT)) {
          const objectType = mapObjectType(poi.poi_category || poi.feature_type || "pin");
          if (shouldRenderMapItem("pois", objectType)) {
            appendStaticIconFeature(svg, poi, project, "pois", `${cleanMapLabel(poi.name_he || poi.name_en) || poi.poi_category || "נקודת עניין"} · ${poi.provenance_id}`);
          }
        }
        if (shouldRenderMapItem("municipal", "addresses")) {
          for (const point of (payload.layers?.address_points?.items || []).slice(0, MAP_MUNICIPAL_ICON_LIMIT)) {
            appendStaticIconFeature(svg, { ...point, poi_category: "address_point" }, project, "municipal", `${cleanMapLabel(point.name_he) || "כתובת עירונית"} · ${point.provenance_id}`);
          }
        }
        if (shouldRenderMapItem("municipal", "municipal_pois")) {
          for (const poi of (payload.layers?.municipal_pois?.items || []).slice(0, MAP_MUNICIPAL_ICON_LIMIT)) {
            appendStaticIconFeature(svg, { ...poi, poi_category: poi.poi_category || "municipal_poi" }, project, "municipal", `${cleanMapLabel(poi.name_he || poi.name_en) || poi.poi_category || "מבנה ציבור"} · ${poi.provenance_id}`);
          }
        }
        if (shouldRenderMapItem("osm", "osm_interest")) {
          for (const poi of (payload.layers?.context_pois?.items || []).slice(0, MAP_MUNICIPAL_ICON_LIMIT)) {
            appendStaticIconFeature(svg, { ...poi, poi_category: "interest" }, project, "osm", `${cleanMapLabel(poi.name_he || poi.name_en) || poi.poi_category || "מוקד עניין OSM"} · ${poi.provenance_id}`, "osm_interest");
          }
        }
        if (shouldRenderMapItem("municipal", "neighborhoods")) {
          appendStaticLayerTextLabels(svg, payload.layers?.neighborhoods?.items, project, "neighborhoods");
        }
        targetSvg.replaceChildren(...Array.from(svg.childNodes));
        targetSvg.hidden = false;
        staticGisViewBox = { x: 0, y: 0, width: 900, height: 620 };
        targetSvg.dataset.worldViewBox = viewBoxString(project.worldViewBox || staticGisViewBox);
        targetSvg.dataset.focusViewBox = viewBoxString(project.focusViewBox || staticGisViewBox);
        if (project.bounds) {
          targetSvg.dataset.geoBounds = [project.bounds.minLon, project.bounds.minLat, project.bounds.maxLon, project.bounds.maxLat].map((value) => Number(value).toFixed(8)).join(",");
        }
        if (options.preserveViewBox && previousViewBox) {
          targetSvg.setAttribute("viewBox", previousViewBox);
        } else {
          targetSvg.setAttribute("viewBox", targetSvg.dataset.focusViewBox);
          staticGisZoom = 1;
        }
        mapFrame.classList.add("hasRealGis");
        setAttr(".mapPanel", "aria-label", "מפת GIS אמיתית");
        setText("#map-title", payload.title_he || "מפת GIS אמיתית");
        setText("#map-desc", "מפת GIS אמיתית בסגנון נקי: רקע וקטורי מסונן, אזור נבחר סגול, תחבורה, פארקים ומבני ציבור.");
        setText(".legendTitle", "שכבות");
        applyRealGisLegendLabels();
        setText(".mapProvenanceBadgeTitle", "מפת GIS אמיתית");
        setText(".mapProvenanceBadgeText", "מוצגת שכבת GIS אמיתית עם provenance. הרקע המפורט הוא אופציונלי כדי שלא יופיעו מבנים לא-מסוננים.");
        setAttr(".mapProvenanceBadge", "data-spatial-representation", "real_gis_static");
        renderMapLayerCounts(payload);
        renderMapLayerSummary(payload);
        window.__municipalDashboardGisMap = payload;
        window.__applyMunicipalSvgFilters?.();
        window.__scheduleMunicipalVisibleBuildingLoad?.();
        if (dashboardRoot) {
          dashboardRoot.dataset.realGisAvailable = "true";
          dashboardRoot.dataset.gisMapStatus = "static-rendered";
        }
        return true;
        } catch (error) {
          window.__municipalMapLastError = error;
          if (dashboardRoot) {
            dashboardRoot.dataset.gisMapStatus = targetSvg.hidden ? "fallback" : "static-render-failed-kept-previous";
          }
          console.warn("municipal_svg_render_failed", error);
          return !targetSvg.hidden && targetSvg.childElementCount > 0;
        }
      };
      window.__renderMunicipalSvgGisMap = renderStaticGisMap;

      const mapFeature = (item, fallbackColor) => ({
        type: "Feature",
        properties: {
          title: featureTitle(item),
          category: item?.poi_category || item?.feature_type || "feature",
          sourceName: sourceLine(item),
          sourceId: item?.source_id || "",
          status: item?.source?.display_status || "unavailable",
          provenanceId: item?.provenance_id || "",
          color: item?.poi_category ? poiColor(item.poi_category) : fallbackColor
        },
        geometry: item?.geometry
      });

      const featureCollection = (items, fallbackColor, geometryTypes = null) => ({
        type: "FeatureCollection",
        features: (items || [])
          .filter((item) => item?.geometry && (!geometryTypes || geometryTypes.includes(item.geometry.type)))
          .map((item) => mapFeature(item, fallbackColor))
      });

      const updateGeoJsonSource = (map, sourceId, collection) => {
        if (map.getSource(sourceId)) {
          map.getSource(sourceId).setData(collection);
        } else {
          map.addSource(sourceId, { type: "geojson", data: collection });
        }
      };

      const ensureFillLayer = (map, sourceId, fillLayerId, lineLayerId, color, opacity = 0.22, lineWidth = 1.6) => {
        if (!map.getLayer(fillLayerId)) {
          map.addLayer({ id: fillLayerId, type: "fill", source: sourceId, paint: { "fill-color": color, "fill-opacity": opacity } });
        }
        map.setPaintProperty(fillLayerId, "fill-color", color);
        map.setPaintProperty(fillLayerId, "fill-opacity", opacity);
        if (!map.getLayer(lineLayerId)) {
          map.addLayer({ id: lineLayerId, type: "line", source: sourceId, paint: { "line-color": color, "line-width": lineWidth, "line-opacity": 0.72 } });
        }
        map.setPaintProperty(lineLayerId, "line-color", color);
        map.setPaintProperty(lineLayerId, "line-width", lineWidth);
        map.setPaintProperty(lineLayerId, "line-opacity", 0.72);
      };

      const ensureCircleLayer = (map, sourceId, layerId) => {
        if (!map.getLayer(layerId)) {
          map.addLayer({ id: layerId, type: "circle", source: sourceId, paint: { "circle-radius": ["case", ["==", ["get", "category"], "school"], 5, 4], "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.4, "circle-opacity": 0.62 } });
        }
        map.setPaintProperty(layerId, "circle-color", ["get", "color"]);
      };

      const mapLibreLabelSize = () => 15 * (window.__municipalMapLabelScale || 0.18);

      const ensureSymbolLabelLayer = (map, sourceId, layerId) => {
        if (!map.getLayer(layerId)) {
          map.addLayer({
            id: layerId,
            type: "symbol",
            source: sourceId,
            layout: {
              "text-field": ["get", "title"],
              "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
              "text-size": mapLibreLabelSize(),
              "text-anchor": "center",
              "text-allow-overlap": false,
              "text-ignore-placement": false
            },
            paint: {
              "text-color": "#172033",
              "text-halo-color": "rgba(255,255,255,0.94)",
              "text-halo-width": 2
            }
          });
        }
        map.setLayoutProperty(layerId, "text-size", mapLibreLabelSize());
      };

      const applyMapLibreLabelScale = () => {
        if (!realGisMap) {
          return;
        }
        for (const layerId of ["dashboard-neighborhoods-label", "dashboard-address-points-label", "dashboard-municipal-pois-label"]) {
          if (realGisMap.getLayer(layerId)) {
            realGisMap.setLayoutProperty(layerId, "text-size", mapLibreLabelSize());
          }
        }
      };

      const ensurePocBasemap = (map, center) => {
        const basemap = visualBasemapCollections(center);
        updateGeoJsonSource(map, "poc-sea", basemap.sea);
        updateGeoJsonSource(map, "poc-parks", basemap.parks);
        updateGeoJsonSource(map, "poc-water", basemap.water);
        updateGeoJsonSource(map, "poc-streets", basemap.streets);
        updateGeoJsonSource(map, "poc-arterials", basemap.arterials);
        updateGeoJsonSource(map, "poc-transit", basemap.transit);
        const addLayer = (id, definition) => {
          if (!map.getLayer(id)) {
            map.addLayer({ id, ...definition });
          }
        };
        addLayer("poc-sea-fill", { type: "fill", source: "poc-sea", paint: { "fill-color": "#bde7f7", "fill-opacity": 0.88 } });
        addLayer("poc-parks-fill", { type: "fill", source: "poc-parks", paint: { "fill-color": "#cdeedb", "fill-opacity": 0.68 } });
        addLayer("poc-water-fill", { type: "fill", source: "poc-water", paint: { "fill-color": "#bfdbfe", "fill-opacity": 0.54 } });
        addLayer("poc-streets-casing", { type: "line", source: "poc-streets", paint: { "line-color": "#ffffff", "line-width": 5.8, "line-opacity": 0.86 } });
        addLayer("poc-streets-line", { type: "line", source: "poc-streets", paint: { "line-color": "#dce4ec", "line-width": 1.15, "line-opacity": 0.72 } });
        addLayer("poc-arterials-casing", { type: "line", source: "poc-arterials", paint: { "line-color": "#ffffff", "line-width": 8.5, "line-opacity": 0.92 } });
        addLayer("poc-arterials-line", { type: "line", source: "poc-arterials", paint: { "line-color": "#d3dde7", "line-width": 2.2, "line-opacity": 0.86 } });
        addLayer("poc-transit-line", { type: "line", source: "poc-transit", paint: { "line-color": "#1676d2", "line-width": 2.1, "line-dasharray": [3, 3], "line-opacity": 0.78 } });
        return basemap.labels;
      };

      const clearMapDecorations = () => {
        for (const decoration of realGisDecorations) {
          decoration.remove();
        }
        realGisDecorations = [];
      };

      const markerSvg = (kind) => {
        if (kind === "transport_stop") {
          return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><rect x="6" y="4" width="12" height="13" rx="2"/><path d="M8 9h8M9 19h.01M15 19h.01" stroke-linecap="round"/></svg>';
        }
        if (kind === "school" || kind === "building") {
          return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M5 20V9l7-4 7 4v11M3 20h18M9 20v-6h6v6M8 11h.01M12 11h.01M16 11h.01" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        }
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M12 20v-6M8 14c-3 0-5-2-4-5 1-3 4-3 5-1 1-4 7-4 8 0 3 0 5 2 4 5-1 3-5 4-8 1-1 1-3 1-5 0Z" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      };

      const addMapMarker = (map, coordinates, className, html, title = "", feature = null) => {
        if (!Array.isArray(coordinates) || coordinates.length < 2) {
          return;
        }
        const element = document.createElement("div");
        element.className = className;
        element.innerHTML = html;
        if (title) {
          element.title = title;
          element.setAttribute("aria-label", title);
        }
        if (feature) {
          element.addEventListener("click", (event) => {
            event.stopPropagation();
            new window.maplibregl.Popup({ closeButton: true, closeOnClick: true })
              .setLngLat(coordinates)
              .setHTML(mapPopupHtml(featureTitle(feature), sourceLine(feature), feature.source_id, feature.provenance_id))
              .addTo(map);
          });
          element.addEventListener("mouseenter", () => {
            realGisHoverPopup?.remove();
            realGisHoverPopup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14 })
              .setLngLat(coordinates)
              .setHTML(mapPopupHtml(featureTitle(feature), sourceLine(feature), feature.source_id, feature.provenance_id))
              .addTo(map);
          });
          element.addEventListener("mouseleave", () => {
            realGisHoverPopup?.remove();
            realGisHoverPopup = null;
          });
        }
        const marker = new window.maplibregl.Marker({ element }).setLngLat(coordinates).addTo(map);
        realGisDecorations.push(marker);
        return marker;
      };

      const geometryCenter = (geometry) => {
        const points = collectGeoPoints(geometry?.coordinates || []);
        if (!points.length) {
          return null;
        }
        return [
          points.reduce((total, point) => total + point[0], 0) / points.length,
          points.reduce((total, point) => total + point[1], 0) / points.length
        ];
      };

      const addMapDecorations = (map, payload, visibleLayers, basemapLabels) => {
        clearMapDecorations();
        for (const label of basemapLabels || []) {
          addMapMarker(map, label.coordinates, label.className, escapeHtml(label.title), label.title);
        }
        if (visibleLayers.pois) {
          for (const item of payload.nearby_pois?.items || []) {
            const marker = addMapMarker(map, item.geometry?.coordinates, "gisMapMarker", markerSvg(item.poi_category), featureTitle(item), item);
            if (marker) {
              marker.getElement().dataset.kind = item.poi_category || "context_poi";
            }
          }
        }
        if (visibleLayers.contextPois) {
          for (const item of payload.layers?.context_pois?.items || []) {
            const marker = addMapMarker(map, item.geometry?.coordinates, "gisMapMarker", markerSvg("context_poi"), featureTitle(item), item);
            if (marker) {
              marker.getElement().dataset.kind = "context_poi";
            }
          }
        }
        // Buildings are drawn as quiet polygons only; markers made the parcel map too noisy.
      };

      const renderRealGisMap = (payload, options = {}) => {
        const renderedStatic = renderStaticGisMap(payload, options);
        setText("#map-renderer-status", renderedStatic ? "SVG GIS פעיל" : "מפת גיבוי");
        return renderedStatic;
        const mapNode = document.getElementById("real-gis-map");
        const mapFrame = document.querySelector(".mapFrame");
        const primaryGeometry = primaryMapGeometry(payload);
        if (!mapNode || !mapFrame || !payload || payload.status !== "found" || !primaryGeometry || !window.maplibregl) {
          return renderedStatic;
        }
        const staticMap = document.getElementById("real-gis-static-map");
        if (staticMap) {
          staticMap.hidden = true;
        }
        mapNode.hidden = false;
        mapNode.style.transform = "";
        resetStaticGisZoom();
        mapFrame.classList.add("hasRealGis");
        mapFrame.classList.add("maplibreActive");
        setAttr(".mapPanel", "aria-label", "מפת GIS אמיתית");
        setText("#map-title", payload.title_he || "מפת GIS אמיתית");
        setText("#map-desc", mapExampleCopy(payload.selected_example));
        setText(".legendTitle", "שכבות");
        setText(".mapProvenanceBadgeTitle", "מפת MapLibre עם GIS אמיתי");
        setText(".mapProvenanceBadgeText", "לחיצה על המפה מפעילה דוח נקודה מה-API; כל פריט מוצג עם מקור ו-provenance.");
        setAttr(".mapProvenanceBadge", "data-spatial-representation", "maplibre_real_gis");

        if (!realGisMap) {
          realGisMap = new window.maplibregl.Map({
            container: mapNode,
            style: {
              version: 8,
              sources: {
                osm: {
                  type: "raster",
                  tiles: [(payload.basemap?.tile_url || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").replace("{s}", "a")],
                  tileSize: 256,
                  attribution: "OpenStreetMap contributors · context only"
                }
              },
              layers: [
                { id: "poc-background", type: "background", paint: { "background-color": "#f6f8f5" } },
                { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.18, "raster-saturation": -0.92, "raster-contrast": -0.18, "raster-brightness-max": 1 } }
              ]
            },
            center: payload.center ? [payload.center.lon, payload.center.lat] : [34.78, 32.08],
            zoom: payload.zoom || 15,
            interactive: true,
            attributionControl: true
          });
        }
        realGisMap.dragPan?.enable();
        realGisMap.scrollZoom?.enable();
        realGisMap.doubleClickZoom?.enable();
        realGisMap.touchZoomRotate?.enable();
        window.__municipalDashboardMapLibre = realGisMap;

        const drawLayers = () => {
          const visibleLayers = mapExampleLayers(payload.selected_example);
          const basemapLabels = payload.visual_context?.mode === "generated_poc" ? ensurePocBasemap(realGisMap, payload.center) : [];
          if (realGisMap.getLayer("osm")) {
            realGisMap.setPaintProperty("osm", "raster-opacity", Number(payload.visual_context?.tile_opacity ?? 0.62));
          }
          const parcelCollection = payload.parcel?.geometry ? { type: "FeatureCollection", features: [mapFeature(payload.parcel, "#14532d")] } : emptyFeatureCollection();
          const nearbyParcelCollection = visibleLayers.nearbyParcels ? featureCollection(payload.layers?.nearby_parcels?.items || [], "#9a8864", ["Polygon", "MultiPolygon"]) : emptyFeatureCollection();
          const poiCollection = visibleLayers.pois ? featureCollection(payload.nearby_pois?.items || [], "#0b68d1", ["Point"]) : emptyFeatureCollection();
          const planCollection = visibleLayers.plans ? featureCollection(payload.layers?.plans?.items || [], "#7c3aed", ["Polygon", "MultiPolygon"]) : emptyFeatureCollection();
          const boundaryCollection = visibleLayers.boundaries ? featureCollection(payload.layers?.municipal_boundaries?.items || [], "#0f766e", ["Polygon", "MultiPolygon"]) : emptyFeatureCollection();
          const neighborhoodCollection = visibleLayers.neighborhoods ? featureCollection(payload.layers?.neighborhoods?.items || [], "#2563eb", ["Polygon", "MultiPolygon"]) : emptyFeatureCollection();
          const addressCollection = visibleLayers.addressPoints ? featureCollection(payload.layers?.address_points?.items || [], "#9333ea", ["Point"]) : emptyFeatureCollection();
          const municipalPoiCollection = visibleLayers.municipalPois ? featureCollection(payload.layers?.municipal_pois?.items || [], "#dc2626", ["Point"]) : emptyFeatureCollection();
          const buildingCollection = visibleLayers.buildings ? featureCollection(payload.layers?.buildings?.items || [], "#f97316", ["Polygon", "MultiPolygon"]) : emptyFeatureCollection();
          const contextPoiCollection = visibleLayers.contextPois ? featureCollection(payload.layers?.context_pois?.items || [], "#64748b", ["Point"]) : emptyFeatureCollection();
          updateGeoJsonSource(realGisMap, "dashboard-boundaries", boundaryCollection);
          ensureFillLayer(realGisMap, "dashboard-boundaries", "dashboard-boundaries-fill", "dashboard-boundaries-outline", "#0f766e", 0.015, 0.9);
          updateGeoJsonSource(realGisMap, "dashboard-neighborhoods", neighborhoodCollection);
          ensureFillLayer(realGisMap, "dashboard-neighborhoods", "dashboard-neighborhoods-fill", "dashboard-neighborhoods-outline", "#2563eb", 0.10, 1.3);
          ensureSymbolLabelLayer(realGisMap, "dashboard-neighborhoods", "dashboard-neighborhoods-label");
          updateGeoJsonSource(realGisMap, "dashboard-address-points", addressCollection);
          ensureCircleLayer(realGisMap, "dashboard-address-points", "dashboard-address-points-circle");
          ensureSymbolLabelLayer(realGisMap, "dashboard-address-points", "dashboard-address-points-label");
          updateGeoJsonSource(realGisMap, "dashboard-municipal-pois", municipalPoiCollection);
          ensureCircleLayer(realGisMap, "dashboard-municipal-pois", "dashboard-municipal-pois-circle");
          ensureSymbolLabelLayer(realGisMap, "dashboard-municipal-pois", "dashboard-municipal-pois-label");
          updateGeoJsonSource(realGisMap, "dashboard-plans", planCollection);
          ensureFillLayer(realGisMap, "dashboard-plans", "dashboard-plans-fill", "dashboard-plans-outline", "#7c3aed", 0.13, 1.4);
          updateGeoJsonSource(realGisMap, "dashboard-buildings", buildingCollection);
          ensureFillLayer(realGisMap, "dashboard-buildings", "dashboard-buildings-fill", "dashboard-buildings-outline", "#f97316", 0.14, 0.7);
          updateGeoJsonSource(realGisMap, "dashboard-nearby-parcels", nearbyParcelCollection);
          ensureFillLayer(realGisMap, "dashboard-nearby-parcels", "dashboard-nearby-parcels-fill", "dashboard-nearby-parcels-outline", "#9a8864", 0.20, 0.9);
          updateGeoJsonSource(realGisMap, "dashboard-parcel", parcelCollection);
          ensureFillLayer(realGisMap, "dashboard-parcel", "dashboard-parcel-fill", "dashboard-parcel-outline", "#14532d", 0.34, 3.4);
          updateGeoJsonSource(realGisMap, "dashboard-context-pois", contextPoiCollection);
          ensureCircleLayer(realGisMap, "dashboard-context-pois", "dashboard-context-pois-circle");
          updateGeoJsonSource(realGisMap, "dashboard-pois", poiCollection);
          ensureCircleLayer(realGisMap, "dashboard-pois", "dashboard-pois-circle");
          syncMapLayerToggles();
          addMapDecorations(realGisMap, payload, visibleLayers, basemapLabels);
          renderMapLayerCounts(payload);
          renderMapLayerSummary(payload);
          const points = collectGeoPoints(primaryGeometry?.coordinates || []);
          for (const collection of [nearbyParcelCollection, poiCollection, planCollection, neighborhoodCollection, addressCollection, municipalPoiCollection, buildingCollection, contextPoiCollection]) {
            for (const feature of collection.features || []) {
              collectGeoPoints(feature.geometry?.coordinates || [], points);
            }
          }
          if (points.length > 0) {
            const bounds = points.reduce((acc, point) => acc.extend(point), new window.maplibregl.LngLatBounds(points[0], points[0]));
            const shouldFitInitialView = !realGisFitBounds || !realGisUserAdjustedView;
            realGisFitBounds = bounds;
            if (shouldFitInitialView) {
              realGisMap.fitBounds(bounds, { padding: 82, maxZoom: payload.zoom || 17, duration: 0 });
            }
          }
          if (!realGisClickBound) {
            realGisClickBound = true;
            realGisMap.on("click", (event) => {
              loadPointReport(event.lngLat.lng, event.lngLat.lat).catch((error) => console.warn("point_report_failed", error));
            });
            for (const layerId of ["dashboard-parcel-fill", "dashboard-nearby-parcels-fill", "dashboard-plans-fill", "dashboard-neighborhoods-fill", "dashboard-buildings-fill", "dashboard-boundaries-fill", "dashboard-pois-circle", "dashboard-context-pois-circle", "dashboard-address-points-circle", "dashboard-municipal-pois-circle"]) {
              realGisMap.on("click", layerId, (event) => {
                const feature = event.features?.[0];
                if (!feature) {
                  return;
                }
                new window.maplibregl.Popup({ closeButton: true, closeOnClick: true })
                  .setLngLat(event.lngLat)
                  .setHTML(mapPopupHtml(feature.properties.title, feature.properties.sourceName, feature.properties.sourceId, feature.properties.provenanceId))
                  .addTo(realGisMap);
              });
              realGisMap.on("mousemove", layerId, (event) => {
                const feature = event.features?.[0];
                if (!feature) {
                  return;
                }
                realGisHoverPopup?.remove();
                realGisHoverPopup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 })
                  .setLngLat(event.lngLat)
                  .setHTML(mapPopupHtml(feature.properties.title, feature.properties.sourceName, feature.properties.sourceId, feature.properties.provenanceId))
                  .addTo(realGisMap);
              });
              realGisMap.on("mouseenter", layerId, () => { realGisMap.getCanvas().style.cursor = "pointer"; });
              realGisMap.on("mouseleave", layerId, () => {
                realGisMap.getCanvas().style.cursor = "";
                realGisHoverPopup?.remove();
                realGisHoverPopup = null;
              });
            }
          }
        };
        if (realGisMap.loaded()) {
          drawLayers();
        } else {
          realGisMap.once("load", drawLayers);
        }
        window.setTimeout(() => realGisMap.resize(), 50);
        window.__municipalDashboardGisMap = payload;
          if (dashboardRoot) {
            dashboardRoot.dataset.realGisAvailable = "true";
            dashboardRoot.dataset.gisMapStatus = "maplibre-rendered";
            dashboardRoot.dataset.mapExample = payload.selected_example || currentMapExample;
          }
        return true;
      };

      const loadDashboardGisMap = async (profile = "overview", options = {}) => {
        if (gisMapProfileRequest && profile === "overview") {
          return gisMapProfileRequest;
        }
        const run = (async () => {
        try {
          const response = await fetch(`${DASHBOARD_GIS_MAP_ENDPOINT}?example=${encodeURIComponent(currentMapExample)}&profile=${encodeURIComponent(profile)}`, { headers: { Accept: "application/json" } });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const payload = await response.json();
          if (mapExampleSelect && payload.selected_example) {
            mapExampleSelect.value = payload.selected_example;
            currentMapExample = payload.selected_example;
          }
          const rendered = renderRealGisMap(payload, options);
          if (dashboardRoot) {
            dashboardRoot.dataset.gisMapStatus = rendered ? "rendered" : payload.status || "fallback";
            dashboardRoot.dataset.gisMapProfile = payload.query?.profile || profile;
          }
          console.info("municipal_rag_dashboard_gis_map", {
            status: payload.status,
            example: payload.selected_example,
            profile: payload.query?.profile,
            rendered,
            poi_count: payload.nearby_pois?.count || 0,
            parcel_source: payload.parcel?.source_id
          });
        } catch (error) {
          if (dashboardRoot) {
            dashboardRoot.dataset.gisMapStatus = "fallback";
          }
          console.warn("municipal_rag_dashboard_gis_map_failed", error);
        }
        })();
        if (profile === "overview") {
          gisMapProfileRequest = run.finally(() => {
            gisMapProfileRequest = null;
          });
          return gisMapProfileRequest;
        }
        return run;
      };
      window.__loadMunicipalGisMapProfile = (profile, options = {}) => loadDashboardGisMap(profile, options);

      const renderDashboardFromEndpoint = (data) => {
        if (!data || typeof data !== "object") {
          return;
        }
        const copy = data.ui_copy || {};
        const header = copy.header || {};
        const drawerCopy = copy.answer_drawer || {};
        const mapCopy = copy.map || {};
        const timelineCopy = copy.timeline || {};
        const discoveryCopy = copy.start_discovery_panel || {};
        const filterCopy = copy.filter_modal || {};
        const popularCopy = copy.popular_searches || {};
        const drawer = data.end_detail_drawer || {};
        const discovery = data.start_discovery_panel || {};
        const workspace = data.main_civic_workspace || {};
        const map = workspace.map || {};
        const mapContext = workspace.map_context || data.state?.intent_resolution?.geo?.map_context || null;
        const mapProvenance = map.provenance || {};

        document.title = copy.document_title || document.title;
        setText(".brandWord", copy.municipality_brand);
        setAttr(".municipalityBrand", "aria-label", copy.municipality_brand);
        setAttr("#ask-playground-form", "aria-label", header.search_aria_label);
        setAttr(".searchSubmit", "aria-label", header.search_submit_label);
        if (questionInput) {
          questionInput.value = data.state?.current_question || drawer.question || questionInput.value;
          questionInput.setAttribute("aria-label", header.search_aria_label || "שאלה");
        }
        setButtonLabel("#popular-searches-button", header.popular_searches_button);
        setButtonLabel("#filters-button", header.filters_button);
        setButtonLabel(".adminButton", header.admin_button);
        setAttr(".adminButton", "aria-label", header.admin_aria_label);

        setAttr(".drawerClose", "aria-label", drawerCopy.close_label);
        setText(".detailTitle h1", drawer.title || drawerCopy.title);
        setText(".questionLine", `${drawerCopy.question_prefix || "שאלה:"} ${drawer.question || data.state?.current_question || ""}`);
        setText("#brief-title", drawerCopy.brief_title);
        setText(".answerSection[aria-labelledby='brief-title'] .sectionText", drawer.brief);
        setText(".confidenceRow > span:nth-child(2)", drawerCopy.confidence_label);
        setConfidenceBadgeText(drawer.confidence_label || "");
        setIconHeadingText("#decisions-title", drawerCopy.decisions_title);
        setText("#related-title", drawerCopy.related_topics_title);
        setIconHeadingText("#limitations-title", drawerCopy.limitations_title);
        setText(".answerSection[aria-labelledby='limitations-title'] .sectionText", (drawer.limitations || [])[0]);
        updateDecisionRows(drawer.decisions || [], drawerCopy.source_link_label);

        const relatedChips = document.querySelector(".relatedChips");
        if (relatedChips && Array.isArray(drawer.related_topics)) {
          relatedChips.replaceChildren(...drawer.related_topics.map(createTopicChip));
        }

        setText("#map-title", mapCopy.title);
        setText("#map-desc", mapCopy.description);
        const areaLabels = Array.isArray(mapCopy.area_labels) ? mapCopy.area_labels : [];
        const selectedEntity = (map.entities || []).find((entity) => entity.id === data.state?.selected_map_entity_id) || map.entities?.[0];
        setText("#map-selected-label", selectedEntity?.label);
        setText(".mapProvenanceBadgeTitle", mapProvenance.label_he || mapCopy.provenance_label);
        setText(".mapProvenanceBadgeText", mapProvenance.description_he || mapCopy.provenance_description);
        setAttr(".mapProvenanceBadge", "data-spatial-representation", map.spatial_representation || "schematic");
        setText("#map-area-north", areaLabels[0]);
        setText("#map-area-center", areaLabels[1]);
        setText("#map-area-west", areaLabels[2]);
        setText("#map-area-east", areaLabels[3]);
        setText("#map-area-south", areaLabels[4]);
        setText("#map-sea-label", mapCopy.sea_label);
        setText(".legendTitle", document.querySelector(".mapFrame.hasRealGis") ? "שכבות" : mapCopy.legend_title);
        const legendLabels = Array.from(document.querySelectorAll(".legendItem span:last-child"));
        const legend = workspace.map?.legend || [];
        if (document.querySelector(".mapFrame.hasRealGis")) {
          applyRealGisLegendLabels();
        } else {
          for (let idx = 0; idx < legendLabels.length; idx += 1) {
            if (legend[idx]) {
              legendLabels[idx].textContent = legend[idx].label || "";
            }
          }
        }
        renderDashboardMapContext(mapContext);

        setText("#timeline-title", timelineCopy.title);
        setAttr(".timelineArrow.prev", "aria-label", timelineCopy.previous_label);
        setAttr(".timelineArrow.next", "aria-label", timelineCopy.next_label);
        updateTimeline(workspace.timeline?.events || []);

        setText("#categories-title", discoveryCopy.categories_title);
        setText("#hot-title", discoveryCopy.hot_topics_title);
        setText("#tree-title", discoveryCopy.topic_tree_title);
        for (const node of document.querySelectorAll(".showMoreButton.flat")) {
          node.firstChild.textContent = `${discoveryCopy.show_more_label || "הצג עוד"} `;
        }
        const showTree = document.querySelector(".showTreeButton");
        if (showTree) {
          showTree.firstChild.textContent = `${discoveryCopy.show_full_tree_label || "הצג כל העץ"} `;
        }
        updateRowList(".startDiscoveryPanel .discoverySection:nth-of-type(1)", discovery.categories || []);
        updateRowList(".startDiscoveryPanel .discoverySection:nth-of-type(2)", discovery.hot_topics || []);
        const tree = discovery.focused_topic_tree_context || {};
        setText(".treeHeader span", tree.root?.label);
        updateRowList(".treeChildren", tree.children || []);
        const collapsed = document.querySelector(".treeRow.collapsed span");
        if (collapsed && tree.collapsed_categories?.[0]) {
          collapsed.textContent = tree.collapsed_categories[0].label || "";
        }

        setText("#filter-title", filterCopy.title);
        setAttr(".dialogClose", "aria-label", filterCopy.close_label);
        const filterLabels = Array.from(document.querySelectorAll(".dialogFilters label"));
        const filterSections = Array.isArray(filterCopy.sections) ? filterCopy.sections : [];
        for (let idx = 0; idx < filterLabels.length; idx += 1) {
          const select = filterLabels[idx].querySelector("select");
          const label = document.createTextNode(filterSections[idx] || "");
          filterLabels[idx].replaceChildren(label, select);
        }
        const dialogActions = Array.from(document.querySelectorAll(".dialogAction"));
        if (dialogActions[0]) {
          dialogActions[0].textContent = filterCopy.reset_label || "";
        }
        if (dialogActions[1]) {
          dialogActions[1].textContent = filterCopy.apply_label || "";
        }

        setText("#popular-title", popularCopy.title);
        const choices = Array.from(document.querySelectorAll(".popularChoice"));
        const choiceValues = Array.isArray(popularCopy.choices) ? popularCopy.choices : [];
        for (let idx = 0; idx < choices.length; idx += 1) {
          if (choiceValues[idx]) {
            choices[idx].textContent = choiceValues[idx];
          }
        }

        currentDashboardData = data;
        dashboardState = structuredClone(data.state || {});
        activeFilters = normalizeFilters(dashboardState.active_filter_summary || {});
        syncFilterModal(activeFilters);
        updateFilterBadge(dashboardState.active_filter_count || Object.keys(activeFilters).length);
        window.__municipalDashboardData = currentDashboardData;
        window.__municipalDashboardState = dashboardState;
        if (dashboardRoot) {
          dashboardRoot.dataset.sourceStatus = "loaded";
          dashboardRoot.dataset.sourceUrl = DASHBOARD_DATA_ENDPOINT;
          dashboardRoot.dataset.selectedCategoryId = dashboardState.selected_category_id || "";
          dashboardRoot.dataset.selectedTopicNodeId = dashboardState.selected_topic_node_id || "";
          dashboardRoot.dataset.selectedTimelineEventId = dashboardState.selected_timeline_event_id || "";
          dashboardRoot.dataset.selectedMapEntityId = dashboardState.selected_map_entity_id || "";
          dashboardRoot.dataset.detailDrawerMode = dashboardState.active_detail_drawer_mode || "";
          dashboardRoot.dataset.mapSpatialRepresentation = currentDashboardData?.main_civic_workspace?.map?.spatial_representation || "";
          dashboardRoot.dataset.realGisAvailable = String(Boolean(currentDashboardData?.main_civic_workspace?.map?.real_gis_available || window.__municipalDashboardGisMap?.real_gis_available));
          dashboardRoot.dataset.mapContextAvailable = String(Boolean(mapContext));
        }
        if (window.__municipalDashboardGisMap?.status === "found" && !window.__municipalDashboardGisMapFromServer) {
          renderRealGisMap(window.__municipalDashboardGisMap);
        }
        if (statusNode) {
          statusNode.textContent = "נתוני לוח המחוונים נטענו מהשרת.";
        }
        console.info("municipal_rag_dashboard_data_loaded", {
          endpoint: DASHBOARD_DATA_ENDPOINT,
          question: data.state?.current_question,
          decision_count: drawer.decisions?.length || 0,
        });
      };

      const renderEvidencePreview = (evidence) => {
        if (!evidenceDialog || !evidence) {
          return;
        }
        setText("#evidence-preview-title", evidence.source_title || "מקור");
        setText("#evidence-artifact-kind", evidence.artifact_kind || "-");
        const pageSpan = evidence.page_span || {};
        setText("#evidence-page-span", pageSpan.start ? `עמודים ${pageSpan.start}-${pageSpan.end || pageSpan.start}` : "עמוד לא ידוע");
        setText("#evidence-confidence", evidence.confidence_label || "-");
        setText("#evidence-header-path", Array.isArray(evidence.header_path) ? evidence.header_path.join(" › ") : "");
        setText("#evidence-snippet", evidence.text || "");
        const sourceStatus = document.getElementById("evidence-source-status");
        const sourceUrl = document.getElementById("evidence-source-url");
        if (evidence.source_url) {
          const source = String(evidence.source_url);
          if (sourceStatus) {
            sourceStatus.textContent = "אין כפתור פתיחה: קישור מקור מוצג כטקסט בלבד";
          }
          if (sourceUrl) {
            sourceUrl.textContent = source.startsWith("https://example.local/") ? `${source} · זהו קישור mock ואינו ניתן לפתיחה` : source;
          }
        } else {
          if (sourceStatus) {
            sourceStatus.textContent = "מקור לא זמין לפתיחה";
          }
          if (sourceUrl) {
            sourceUrl.textContent = "קישור מקור אינו זמין";
          }
        }
        evidenceDialog.hidden = false;
        if (typeof evidenceDialog.showModal === "function") {
          evidenceDialog.showModal();
        } else {
          evidenceDialog.setAttribute("open", "");
        }
      };

      const applyDashboardInteraction = async (type, id, extra = {}) => {
        if (type === "open_evidence") {
          return openEvidencePreview(id);
        }
        const seq = dashboardRequestSeq + 1;
        dashboardRequestSeq = seq;
        if (statusNode) {
          statusNode.textContent = "מעדכן את הקשר התצוגה.";
        }
        try {
          const response = await fetch(DASHBOARD_INTERACTION_ENDPOINT, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              state: dashboardState || currentDashboardData?.state || {},
              interaction: { type, id, ...extra }
            })
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const data = await response.json();
          if (seq !== dashboardRequestSeq) {
            return;
          }
          window.__lastDashboardInteraction = { type, id, status: "applied" };
          renderDashboardFromEndpoint(data);
          if (data.evidence_preview) {
            renderEvidencePreview(data.evidence_preview);
          }
        } catch (error) {
          window.__lastDashboardInteraction = { type, id, status: "failed", error: String(error?.message || error) };
          if (statusNode) {
            statusNode.textContent = "לא ניתן לעדכן את הבחירה כרגע.";
          }
          console.warn("municipal_rag_dashboard_interaction_failed", { type, id, error });
        }
      };
      window.applyDashboardInteraction = applyDashboardInteraction;

      const openEvidencePreview = async (evidenceId) => {
        if (!evidenceId) {
          return;
        }
        if (statusNode) {
          statusNode.textContent = "טוען מקור.";
        }
        try {
          const response = await fetch(`${DASHBOARD_EVIDENCE_ENDPOINT}/${encodeURIComponent(evidenceId)}`, {
            headers: { Accept: "application/json" }
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const evidence = await response.json();
          window.__lastDashboardInteraction = { type: "open_evidence", id: evidenceId, status: "applied" };
          if (dashboardState) {
            dashboardState.active_detail_drawer_mode = "evidencePreview";
            dashboardState.selected_evidence_id = evidenceId;
            window.__municipalDashboardState = dashboardState;
          }
          renderEvidencePreview(evidence);
          if (statusNode) {
            statusNode.textContent = "המקור נפתח.";
          }
        } catch (error) {
          window.__lastDashboardInteraction = { type: "open_evidence", id: evidenceId, status: "failed", error: String(error?.message || error) };
          if (statusNode) {
            statusNode.textContent = "לא ניתן לפתוח את המקור כרגע.";
          }
          console.warn("municipal_rag_dashboard_evidence_failed", { evidenceId, error });
        }
      };

      const loadDashboardData = async () => {
        try {
          const response = await fetch(DASHBOARD_DATA_ENDPOINT, { headers: { Accept: "application/json" } });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const data = await response.json();
          renderDashboardFromEndpoint(data);
        } catch (error) {
          if (dashboardRoot) {
            dashboardRoot.dataset.sourceStatus = "fallback";
          }
          console.warn("municipal_rag_dashboard_data_failed", error);
        }
      };
      window.renderDashboardFromEndpoint = renderDashboardFromEndpoint;
      window.loadDashboardData = loadDashboardData;

      const dashboardDebugSnapshot = {
        search_intent: "decision_about_topic",
        selected_category_id: "planning_building",
        selected_topic_node_id: "topic_rova_tet_vav_plan",
        selected_map_entity_id: "entity_rova_tet_vav",
        selected_time_range: "2024-05-10/2024-07-15",
        active_detail_drawer_mode: "answer",
        visible_hot_topic_ids: ["topic_rova_tet_vav_plan", "topic_light_rail", "topic_lachish_park", "topic_master_plan"],
        visible_map_entity_count: 13,
        generation_status: "mock_answer_ready",
        active_filter_summary: "closed",
        inline_source_link_id: "decision_1",
        evidence_reference_id: "evidence_rova_tet_vav_protocol",
        retrieval_artifact_id: "artifact_protocol_decision_unit_478",
        header_path: ["מועצת העיר", "תכנון ובנייה", "תכנית רובע טו"],
        page_span: { start: 7, end: 7 }
      };
      console.info("municipal_rag_dashboard_state", dashboardDebugSnapshot);
      loadDashboardData();
      if (window.__municipalDashboardGisMapFromServer && window.__municipalDashboardGisMap?.status === "found") {
        renderMapLayerCounts(window.__municipalDashboardGisMap);
        renderMapLayerSummary(window.__municipalDashboardGisMap);
        applyRealGisLegendLabels();
        if (dashboardRoot) {
          dashboardRoot.dataset.realGisAvailable = "true";
          dashboardRoot.dataset.gisMapProfile = window.__municipalDashboardGisMap.query?.profile || "initial";
          dashboardRoot.dataset.gisMapStatus = "server-svg-rendered";
        }
      } else {
        loadDashboardGisMap();
      }

      const categorySection = document.querySelector(".startDiscoveryPanel .discoverySection:nth-of-type(1)");
      const hotTopicSection = document.querySelector(".startDiscoveryPanel .discoverySection:nth-of-type(2)");
      const treeChildren = document.querySelector(".treeChildren");
      const timelineCards = document.querySelector(".timelineCards");
      const mapFrame = document.querySelector(".mapFrame");
      const mapControls = document.querySelector(".mapControls");
      const relatedChips = document.querySelector(".relatedChips");
      const coverageButton = document.getElementById("coverage-matrix-button");
      const mapViewResetButton = document.getElementById("map-view-reset");
      const mapViewZoomInButton = document.getElementById("map-view-zoom-in");
      const mapViewZoomOutButton = document.getElementById("map-view-zoom-out");
      const mapViewLayersButton = document.getElementById("map-view-layers");
      const mapLayerPopover = document.getElementById("map-layer-popover");
      const mapViewZoomLabel = document.getElementById("map-view-zoom-label");
      const zoomInButton = document.getElementById("map-control-zoom-in");
      const zoomOutButton = document.getElementById("map-control-zoom-out");
      const recenterButton = document.getElementById("map-control-center");
      const staticSvgMapObjectSelector = '#real-gis-static-map [data-map-icon], #real-gis-static-map path[data-map-layer]:not([data-map-layer="basemap"]), #real-gis-static-map g[data-map-layer]:not([data-map-layer="basemap"]), .schematicMapSvg [data-map-tooltip-title], .schematicMapSvg [role="button"][aria-label]';

      const directSvgTitle = (node) => Array.from(node?.children || []).find((child) => child.tagName?.toLowerCase() === "title")?.textContent || "";

      const showStaticSvgNodeTooltip = (event, node) => {
        const rawTitle = directSvgTitle(node) || node?.dataset?.mapTooltipTitle || node?.getAttribute?.("aria-label") || "פריט מפה";
        const [label, detail] = String(rawTitle).split(" · ");
        showStaticFeatureTooltip(event, {
          label,
          source_id: node?.getAttribute?.("data-map-layer") || "map",
          provenance_id: detail || node?.getAttribute?.("data-map-object") || "",
          source: { name_he: node?.getAttribute?.("data-map-layer") || "מפה", display_status: "context_only" }
        });
      };

      let activeMapPan = null;
      let suppressNextMapClick = false;

      const parseSvgViewBox = (svg) => {
        const raw = svg?.getAttribute("viewBox") || "0 0 900 620";
        const values = raw.split(/\\s+/).map(Number);
        if (values.length !== 4 || values.some((value) => !Number.isFinite(value))) {
          return { x: 0, y: 0, width: 900, height: 620 };
        }
        return { x: values[0], y: values[1], width: values[2], height: values[3] };
      };

      const serializeSvgViewBox = (box) => `${box.x.toFixed(2)} ${box.y.toFixed(2)} ${box.width.toFixed(2)} ${box.height.toFixed(2)}`;

      const activeViewportSvg = () => {
        const staticMap = document.getElementById("real-gis-static-map");
        if (staticMap && !staticMap.hidden && getComputedStyle(staticMap).display !== "none") {
          return staticMap;
        }
        const schematicMap = document.querySelector(".schematicMapSvg");
        return schematicMap && getComputedStyle(schematicMap).display !== "none" ? schematicMap : null;
      };

      const baseViewBoxForSvg = (svg) => {
        if (!svg) {
          return { x: 0, y: 0, width: 900, height: 620 };
        }
        if (svg.id === "real-gis-static-map" && svg.dataset.worldViewBox) {
          const worldValues = svg.dataset.worldViewBox.split(/\s+/).map(Number);
          if (worldValues.length === 4 && worldValues.every(Number.isFinite)) {
            return { x: worldValues[0], y: worldValues[1], width: worldValues[2], height: worldValues[3] };
          }
        }
        if (!svg.dataset.baseViewBox) {
          svg.dataset.baseViewBox = serializeSvgViewBox(parseSvgViewBox(svg));
        }
        const values = svg.dataset.baseViewBox.split(/\\s+/).map(Number);
        return { x: values[0], y: values[1], width: values[2], height: values[3] };
      };

      const focusViewBoxForSvg = (svg) => {
        if (svg?.id === "real-gis-static-map" && svg.dataset.focusViewBox) {
          const values = svg.dataset.focusViewBox.split(/\s+/).map(Number);
          if (values.length === 4 && values.every(Number.isFinite)) {
            return { x: values[0], y: values[1], width: values[2], height: values[3] };
          }
        }
        return baseViewBoxForSvg(svg);
      };

      const clampViewBox = (box, base) => {
        const width = Math.max(base.width / 8, Math.min(base.width, box.width));
        const height = Math.max(base.height / 8, Math.min(base.height, box.height));
        const panPaddingX = base.width * 0.18;
        const panPaddingY = base.height * 0.18;
        const minX = base.x - panPaddingX;
        const minY = base.y - panPaddingY;
        const maxX = base.x + base.width - width + panPaddingX;
        const maxY = base.y + base.height - height + panPaddingY;
        return {
          x: Math.max(minX, Math.min(maxX, box.x)),
          y: Math.max(minY, Math.min(maxY, box.y)),
          width,
          height
        };
      };

      const updateViewportZoomLabel = (svg, box = null) => {
        if (!mapViewZoomLabel || !svg) {
          return;
        }
        const base = baseViewBoxForSvg(svg);
        const current = box || parseSvgViewBox(svg);
        const zoom = Math.round((base.width / current.width) * 100);
        mapViewZoomLabel.textContent = `${zoom}%`;
      };

      let visibleBuildingLoadTimer = null;
      let visibleBuildingAbort = null;
      let visibleBuildingCacheKey = "";
      let visibleParcelAbort = null;
      let visibleParcelCacheKey = "";
      let visibleContextPoiAbort = null;
      let visibleContextPoiCacheKey = "";

      const visibleBuildingsEnabled = () => {
        const buildingInput = document.querySelector('[data-map-object-toggle="buildings"]');
        return Boolean(buildingInput?.checked);
      };

      const resetDynamicMapToggleDefaults = () => {
        const basemapInput = document.querySelector('[data-map-layer-toggle="basemap"]');
        const parcelLayerInput = document.querySelector('[data-map-layer-toggle="nearby_parcels"]');
        const parcelObjectInput = document.querySelector('[data-map-object-toggle="parcels"]');
        const buildingInput = document.querySelector('[data-map-object-toggle="buildings"]');
        const waterInput = document.querySelector('[data-map-object-toggle="osm_water"]');
        const osmParksInput = document.querySelector('[data-map-object-toggle="osm_parks"]');
        if (basemapInput) {
          basemapInput.checked = false;
        }
        if (parcelLayerInput) {
          parcelLayerInput.checked = false;
        }
        if (parcelObjectInput) {
          parcelObjectInput.checked = false;
        }
        if (buildingInput) {
          buildingInput.checked = false;
        }
        if (waterInput) {
          waterInput.checked = false;
        }
        if (osmParksInput) {
          osmParksInput.checked = false;
        }
      };

      const dynamicMapItemVisible = (layer, objectType) => {
        const layerInput = document.querySelector(`[data-map-layer-toggle="${layer}"]`);
        const objectInput = document.querySelector(`[data-map-object-toggle="${objectType}"]`);
        return (!layerInput || layerInput.checked) && (!objectInput || objectInput.checked);
      };

      const removeDynamicBuildingLayer = () => {
        document.getElementById("real-gis-dynamic-buildings-layer")?.remove();
      };

      const removeDynamicParcelLayer = () => {
        document.getElementById("real-gis-dynamic-parcels-layer")?.remove();
      };

      const removeDynamicContextPoiLayer = () => {
        document.getElementById("real-gis-dynamic-context-pois-layer")?.remove();
      };

      const geoBoundsForSvg = (svg) => {
        const values = String(svg?.dataset?.geoBounds || "").split(",").map(Number);
        if (values.length !== 4 || values.some((value) => !Number.isFinite(value))) {
          return null;
        }
        return { minLon: values[0], minLat: values[1], maxLon: values[2], maxLat: values[3] };
      };

      const visibleGeoBboxForSvg = (svg) => {
        const geo = geoBoundsForSvg(svg);
        if (!geo) {
          return null;
        }
        const base = baseViewBoxForSvg(svg);
        const current = parseSvgViewBox(svg);
        const left = Math.max(base.x, current.x);
        const top = Math.max(base.y, current.y);
        const right = Math.min(base.x + base.width, current.x + current.width);
        const bottom = Math.min(base.y + base.height, current.y + current.height);
        if (right <= left || bottom <= top) {
          return null;
        }
        const lonAt = (x) => geo.minLon + ((x - base.x) / base.width) * (geo.maxLon - geo.minLon);
        const latAt = (y) => geo.maxLat - ((y - base.y) / base.height) * (geo.maxLat - geo.minLat);
        return {
          minLon: lonAt(left),
          maxLon: lonAt(right),
          minLat: latAt(bottom),
          maxLat: latAt(top),
        };
      };

      const expandedVisibleGeoBboxForSvg = (svg) => {
        const bbox = visibleGeoBboxForSvg(svg);
        const geo = geoBoundsForSvg(svg);
        if (!bbox || !geo) {
          return null;
        }
        const lonPad = (bbox.maxLon - bbox.minLon) * 0.2;
        const latPad = (bbox.maxLat - bbox.minLat) * 0.2;
        return {
          minLon: Math.max(geo.minLon, bbox.minLon - lonPad),
          minLat: Math.max(geo.minLat, bbox.minLat - latPad),
          maxLon: Math.min(geo.maxLon, bbox.maxLon + lonPad),
          maxLat: Math.min(geo.maxLat, bbox.maxLat + latPad),
        };
      };

      const projectFromGeoBounds = (svg) => {
        const geo = geoBoundsForSvg(svg);
        if (!geo) {
          return null;
        }
        return ([lon, lat]) => [
          ((lon - geo.minLon) / (geo.maxLon - geo.minLon || 1)) * 900,
          620 - ((lat - geo.minLat) / (geo.maxLat - geo.minLat || 1)) * 620,
        ];
      };

      const ensureDynamicBuildingLayer = (svg) => {
        let group = document.getElementById("real-gis-dynamic-buildings-layer");
        if (!group) {
          group = document.createElementNS("http://www.w3.org/2000/svg", "g");
          group.id = "real-gis-dynamic-buildings-layer";
          group.setAttribute("data-map-layer", "municipal");
          group.setAttribute("data-map-object", "buildings");
          const before = svg.querySelector('[data-map-layer="nearby_parcels"], [data-map-layer="parcel"], [data-map-object="neighborhoods"], [data-map-icon]');
          svg.insertBefore(group, before || null);
        }
        group.style.display = dynamicMapItemVisible("municipal", "buildings") ? "" : "none";
        return group;
      };

      const ensureDynamicParcelLayer = (svg) => {
        let group = document.getElementById("real-gis-dynamic-parcels-layer");
        if (!group) {
          group = document.createElementNS("http://www.w3.org/2000/svg", "g");
          group.id = "real-gis-dynamic-parcels-layer";
          group.setAttribute("data-map-layer", "nearby_parcels");
          group.setAttribute("data-map-object", "parcels");
          const before = svg.querySelector('[data-map-layer="parcel"], [data-map-object="neighborhoods"], [data-map-icon]');
          svg.insertBefore(group, before || null);
        }
        group.style.display = dynamicMapItemVisible("nearby_parcels", "parcels") ? "" : "none";
        return group;
      };

      const ensureDynamicContextPoiLayer = (svg) => {
        let group = document.getElementById("real-gis-dynamic-context-pois-layer");
        if (!group) {
          group = document.createElementNS("http://www.w3.org/2000/svg", "g");
          group.id = "real-gis-dynamic-context-pois-layer";
          group.setAttribute("data-map-layer", "osm");
          group.setAttribute("data-map-object", "osm_interest");
          const before = svg.querySelector('[data-map-object="neighborhoods"]');
          svg.insertBefore(group, before || null);
        }
        group.style.display = dynamicMapItemVisible("osm", "osm_interest") ? "" : "none";
        return group;
      };

      const renderDynamicBuildings = (svg, payload) => {
        const project = projectFromGeoBounds(svg);
        if (!project) {
          return;
        }
        const pathData = (payload?.items || []).map((item) => polygonPaths(item.geometry, project)).filter(Boolean).join(" ");
        const group = ensureDynamicBuildingLayer(svg);
        group.replaceChildren();
        if (!pathData) {
          return;
        }
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("data-map-layer", "municipal");
        path.setAttribute("data-map-object", "buildings");
        path.setAttribute("d", pathData);
        path.setAttribute("fill", "rgba(68, 76, 86, 0.25)");
        path.setAttribute("stroke", "rgba(30, 41, 59, 0.42)");
        path.setAttribute("stroke-width", "0.36");
        path.setAttribute("stroke-linejoin", "round");
        path.style.pointerEvents = "visiblePainted";
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `מבנים בתחום הנראה · ${payload.displayed_count || 0}/${payload.total_count || 0}`;
        path.appendChild(title);
        group.appendChild(path);
        window.__applyMunicipalSvgFilters?.();
      };

      const renderDynamicParcels = (svg, payload) => {
        const project = projectFromGeoBounds(svg);
        if (!project) {
          return;
        }
        const pathData = (payload?.items || []).map((item) => polygonPaths(item.geometry, project)).filter(Boolean).join(" ");
        const group = ensureDynamicParcelLayer(svg);
        group.replaceChildren();
        if (!pathData) {
          return;
        }
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("data-map-layer", "nearby_parcels");
        path.setAttribute("data-map-object", "parcels");
        path.setAttribute("d", pathData);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", "rgba(101, 84, 52, 0.55)");
        path.setAttribute("stroke-width", "0.55");
        path.setAttribute("stroke-linejoin", "round");
        path.style.pointerEvents = "visiblePainted";
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `חלקות בתחום הנראה · ${payload.displayed_count || 0}/${payload.total_count || 0}`;
        path.appendChild(title);
        group.appendChild(path);
        window.__applyMunicipalSvgFilters?.();
      };

      const renderDynamicContextPois = (svg, payload) => {
        const project = projectFromGeoBounds(svg);
        if (!project) {
          return;
        }
        const group = ensureDynamicContextPoiLayer(svg);
        group.replaceChildren();
        for (const poi of payload?.items || []) {
          const coordinates = poi?.geometry?.coordinates;
          if (!Array.isArray(coordinates) || coordinates.length < 2) {
            continue;
          }
          const [x, y] = project([Number(coordinates[0]), Number(coordinates[1])]);
          appendStaticIconNode(group, { ...poi, poi_category: poi.poi_category || "interest" }, x, y, poi.poi_category || "interest", "osm", `${cleanMapLabel(poi.name_he || poi.name_en) || poi.poi_category || "מוקד עניין OSM"} · ${poi.provenance_id}`, "osm_interest");
        }
        window.__applyMunicipalSvgFilters?.();
      };

      const loadVisibleBuildings = async () => {
        const svg = document.getElementById("real-gis-static-map");
        if (!svg || svg.hidden) {
          removeDynamicBuildingLayer();
          return;
        }
        const bbox = expandedVisibleGeoBboxForSvg(svg);
        if (!bbox) {
          removeDynamicBuildingLayer();
          return;
        }
        const cacheKey = [bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat].map((value) => Number(value).toFixed(5)).join(",");
        if (cacheKey === visibleBuildingCacheKey && document.getElementById("real-gis-dynamic-buildings-layer")) {
          return;
        }
        visibleBuildingCacheKey = cacheKey;
        visibleBuildingAbort?.abort();
        visibleBuildingAbort = new AbortController();
        const params = new URLSearchParams({
          municipality_code: window.__municipalDashboardGisMap?.query?.municipality_code || "5000",
          min_lon: String(bbox.minLon),
          min_lat: String(bbox.minLat),
          max_lon: String(bbox.maxLon),
          max_lat: String(bbox.maxLat),
        });
        try {
          const response = await fetch(`/api/ui/rag-dashboard/gis-buildings?${params.toString()}`, { signal: visibleBuildingAbort.signal });
          if (!response.ok) {
            throw new Error(`buildings HTTP ${response.status}`);
          }
          renderDynamicBuildings(svg, await response.json());
        } catch (error) {
          if (error?.name !== "AbortError") {
            console.warn("municipal_visible_buildings_failed", error);
          }
        }
      };

      const loadVisibleParcels = async () => {
        const svg = document.getElementById("real-gis-static-map");
        if (!svg || svg.hidden) {
          removeDynamicParcelLayer();
          return;
        }
        const bbox = expandedVisibleGeoBboxForSvg(svg);
        if (!bbox) {
          removeDynamicParcelLayer();
          return;
        }
        const cacheKey = [bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat].map((value) => Number(value).toFixed(5)).join(",");
        if (cacheKey === visibleParcelCacheKey && document.getElementById("real-gis-dynamic-parcels-layer")) {
          return;
        }
        visibleParcelCacheKey = cacheKey;
        visibleParcelAbort?.abort();
        visibleParcelAbort = new AbortController();
        const params = new URLSearchParams({
          municipality_code: window.__municipalDashboardGisMap?.query?.municipality_code || "5000",
          min_lon: String(bbox.minLon),
          min_lat: String(bbox.minLat),
          max_lon: String(bbox.maxLon),
          max_lat: String(bbox.maxLat),
        });
        try {
          const response = await fetch(`/api/ui/rag-dashboard/gis-parcels?${params.toString()}`, { signal: visibleParcelAbort.signal });
          if (!response.ok) {
            throw new Error(`parcels HTTP ${response.status}`);
          }
          renderDynamicParcels(svg, await response.json());
        } catch (error) {
          if (error?.name !== "AbortError") {
            console.warn("municipal_visible_parcels_failed", error);
          }
        }
      };

      const loadVisibleContextPois = async () => {
        const svg = document.getElementById("real-gis-static-map");
        if (!svg || svg.hidden) {
          removeDynamicContextPoiLayer();
          return;
        }
        if (!dynamicMapItemVisible("osm", "osm_interest")) {
          removeDynamicContextPoiLayer();
          return;
        }
        const bbox = expandedVisibleGeoBboxForSvg(svg);
        if (!bbox) {
          removeDynamicContextPoiLayer();
          return;
        }
        const cacheKey = [bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat].map((value) => Number(value).toFixed(5)).join(",");
        if (cacheKey === visibleContextPoiCacheKey && document.getElementById("real-gis-dynamic-context-pois-layer")) {
          return;
        }
        visibleContextPoiCacheKey = cacheKey;
        visibleContextPoiAbort?.abort();
        visibleContextPoiAbort = new AbortController();
        const params = new URLSearchParams({
          municipality_code: window.__municipalDashboardGisMap?.query?.municipality_code || "5000",
          min_lon: String(bbox.minLon),
          min_lat: String(bbox.minLat),
          max_lon: String(bbox.maxLon),
          max_lat: String(bbox.maxLat),
        });
        try {
          const response = await fetch(`/api/ui/rag-dashboard/gis-context-pois?${params.toString()}`, { signal: visibleContextPoiAbort.signal });
          if (!response.ok) {
            throw new Error(`context POIs HTTP ${response.status}`);
          }
          renderDynamicContextPois(svg, await response.json());
        } catch (error) {
          if (error?.name !== "AbortError") {
            console.warn("municipal_visible_context_pois_failed", error);
          }
        }
      };

      const scheduleVisibleBuildingLoad = () => {
        clearTimeout(visibleBuildingLoadTimer);
        visibleBuildingLoadTimer = setTimeout(() => {
          loadVisibleBuildings();
          loadVisibleParcels();
          loadVisibleContextPois();
        }, 260);
      };

      window.__scheduleMunicipalVisibleBuildingLoad = scheduleVisibleBuildingLoad;
      resetDynamicMapToggleDefaults();
      scheduleVisibleBuildingLoad();

      const setViewportBox = (svg, box) => {
        if (!svg) {
          return;
        }
        const base = baseViewBoxForSvg(svg);
        const next = clampViewBox(box, base);
        svg.style.transform = "";
        svg.setAttribute("viewBox", serializeSvgViewBox(next));
        updateViewportZoomLabel(svg, next);
        scheduleVisibleBuildingLoad();
      };

      const zoomViewport = (factor, anchor = null) => {
        const svg = activeViewportSvg();
        if (!svg) {
          return;
        }
        const current = parseSvgViewBox(svg);
        const rect = svg.getBoundingClientRect();
        const anchorX = anchor ? current.x + ((anchor.clientX - rect.left) / rect.width) * current.width : current.x + current.width / 2;
        const anchorY = anchor ? current.y + ((anchor.clientY - rect.top) / rect.height) * current.height : current.y + current.height / 2;
        const width = current.width * factor;
        const height = current.height * factor;
        setViewportBox(svg, {
          x: anchorX - ((anchorX - current.x) / current.width) * width,
          y: anchorY - ((anchorY - current.y) / current.height) * height,
          width,
          height
        });
      };

      const resetViewport = () => {
        const svg = activeViewportSvg();
        if (!svg) {
          return;
        }
        setViewportBox(svg, focusViewBoxForSvg(svg));
      };

      const startViewportPan = (event) => {
        if (realGisMap) {
          return;
        }
        if (event.button !== undefined && event.button !== 0) {
          return;
        }
        if (event.target.closest(".mapZoomControls, .mapLayerPopover, .mapInfoPanel, .coveragePanel, .legendCard, .mapControls")) {
          return;
        }
        if (event.target.closest(staticSvgMapObjectSelector)) {
          return;
        }
        const svg = activeViewportSvg();
        if (!svg) {
          return;
        }
        event.preventDefault();
        activeMapPan = {
          svg,
          startX: event.clientX,
          startY: event.clientY,
          startBox: parseSvgViewBox(svg),
          moved: false
        };
        mapFrame?.classList.add("isPanning");
        mapFrame?.setPointerCapture?.(event.pointerId);
      };

      const moveViewportPan = (event) => {
        if (!activeMapPan) {
          return;
        }
        event.preventDefault();
        const rect = activeMapPan.svg.getBoundingClientRect();
        const dx = ((event.clientX - activeMapPan.startX) / rect.width) * activeMapPan.startBox.width;
        const dy = ((event.clientY - activeMapPan.startY) / rect.height) * activeMapPan.startBox.height;
        if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
          activeMapPan.moved = true;
        }
        setViewportBox(activeMapPan.svg, {
          ...activeMapPan.startBox,
          x: activeMapPan.startBox.x - dx,
          y: activeMapPan.startBox.y - dy
        });
      };

      const endViewportPan = () => {
        if (activeMapPan?.moved) {
          suppressNextMapClick = true;
        }
        activeMapPan = null;
        mapFrame?.classList.remove("isPanning");
      };

      const toggleMapLayers = () => {
        if (!mapLayerPopover) {
          return;
        }
        const shouldHide = !mapLayerPopover.hidden;
        mapLayerPopover.hidden = shouldHide;
        mapViewLayersButton?.setAttribute("aria-expanded", String(!shouldHide));
      };

      const mapLayerIdsByToggle = {
        parcel: ["dashboard-parcel-fill", "dashboard-parcel-outline"],
        nearby_parcels: ["dashboard-nearby-parcels-fill", "dashboard-nearby-parcels-outline"],
        pois: ["dashboard-pois-circle"],
        municipal: ["dashboard-neighborhoods-fill", "dashboard-neighborhoods-outline", "dashboard-neighborhoods-label", "dashboard-address-points-circle", "dashboard-address-points-label", "dashboard-municipal-pois-circle", "dashboard-municipal-pois-label"],
        osm: ["dashboard-buildings-fill", "dashboard-buildings-outline", "dashboard-context-pois-circle"]
      };

      const setMapLibreLayerGroupVisible = (group, visible) => {
        if (!realGisMap) {
          return;
        }
        for (const layerId of mapLayerIdsByToggle[group] || []) {
          if (realGisMap.getLayer(layerId)) {
            realGisMap.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
          }
        }
      };

      const syncMapLayerToggles = () => {
        for (const input of document.querySelectorAll("[data-map-layer-toggle]")) {
          setMapLibreLayerGroupVisible(input.dataset.mapLayerToggle, input.checked);
        }
      };

      const stopMapControlEvent = (event) => {
        event.stopPropagation();
      };

      const bindMapControlButton = (button, action) => {
        button?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          action();
        });
      };

      mapControls?.addEventListener("pointerdown", stopMapControlEvent, { capture: true });
      mapControls?.addEventListener("dblclick", stopMapControlEvent, { capture: true });
      bindMapControlButton(zoomInButton, () => zoomDashboardMap(1));
      bindMapControlButton(zoomOutButton, () => zoomDashboardMap(-1));
      bindMapControlButton(recenterButton, () => recenterDashboardMap());
      bindMapControlButton(mapViewZoomInButton, () => (realGisMap ? zoomDashboardMap(1) : zoomViewport(0.72)));
      bindMapControlButton(mapViewZoomOutButton, () => (realGisMap ? zoomDashboardMap(-1) : zoomViewport(1.38)));
      bindMapControlButton(mapViewResetButton, () => (realGisMap ? recenterDashboardMap() : resetViewport()));
      bindMapControlButton(mapViewLayersButton, toggleMapLayers);
      for (const input of document.querySelectorAll("[data-map-layer-toggle]")) {
        input.addEventListener("change", () => setMapLibreLayerGroupVisible(input.dataset.mapLayerToggle, input.checked));
      }
      mapFrame?.addEventListener("pointerdown", startViewportPan);
      mapFrame?.addEventListener("mousemove", (event) => {
        const node = event.target.closest(staticSvgMapObjectSelector);
        if (node) {
          showStaticSvgNodeTooltip(event, node);
        }
      });
      mapFrame?.addEventListener("mouseleave", hideStaticFeatureTooltip);
      mapFrame?.addEventListener("click", (event) => {
        const node = event.target.closest(staticSvgMapObjectSelector);
        if (!node) {
          return;
        }
        event.preventDefault();
        showStaticSvgNodeTooltip(event, node);
      }, true);
      document.addEventListener("pointermove", moveViewportPan);
      document.addEventListener("pointerup", endViewportPan);
      document.addEventListener("pointercancel", endViewportPan);
      mapFrame?.addEventListener("wheel", (event) => {
        event.preventDefault();
        zoomViewport(event.deltaY < 0 ? 0.82 : 1.22, event);
      }, { passive: false });

      categorySection?.addEventListener("click", (event) => {
        const button = event.target.closest(".discoveryRow");
        if (button?.dataset.itemId) {
          applyDashboardInteraction("select_category", button.dataset.itemId);
        }
      });
      hotTopicSection?.addEventListener("click", (event) => {
        const button = event.target.closest(".discoveryRow");
        if (button?.dataset.itemId) {
          applyDashboardInteraction("select_hot_topic", button.dataset.itemId);
        }
      });
      treeChildren?.addEventListener("click", (event) => {
        const button = event.target.closest(".treeRow");
        if (button?.dataset.itemId) {
          applyDashboardInteraction("select_topic_tree_node", button.dataset.itemId);
        }
      });
      timelineCards?.addEventListener("click", (event) => {
        const card = event.target.closest(".timelineCard");
        if (card?.dataset.eventId) {
          applyDashboardInteraction("select_timeline_event", card.dataset.eventId);
        }
      });
      mapFrame?.addEventListener("click", (event) => {
        if (suppressNextMapClick) {
          suppressNextMapClick = false;
          event.preventDefault();
          return;
        }
        if (event.target.closest(".mapZoomControls, .mapLayerPopover, .mapInfoPanel, .coveragePanel, .legendCard, .mapControls")) {
          return;
        }
        const entity = event.target.closest("[data-map-entity-id]");
        if (entity?.dataset.mapEntityId) {
          showSchematicFeatureTooltip(event, entity);
          applyDashboardInteraction("select_map_entity", entity.dataset.mapEntityId);
          return;
        }
        const schematicFeature = event.target.closest(".schematicMapSvg [data-map-tooltip-title], .schematicMapSvg [role='button'][aria-label]");
        if (schematicFeature) {
          event.preventDefault();
          showSchematicFeatureTooltip(event, schematicFeature);
          return;
        }
      });
      mapFrame?.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        const entity = event.target.closest("[data-map-entity-id]");
        if (entity?.dataset.mapEntityId) {
          event.preventDefault();
          showSchematicFeatureTooltip(event, entity);
          applyDashboardInteraction("select_map_entity", entity.dataset.mapEntityId);
          return;
        }
        const schematicFeature = event.target.closest(".schematicMapSvg [data-map-tooltip-title], .schematicMapSvg [role='button'][aria-label]");
        if (schematicFeature) {
          event.preventDefault();
          showSchematicFeatureTooltip(event, schematicFeature);
        }
      });
      relatedChips?.addEventListener("click", (event) => {
        const chip = event.target.closest(".topicChip");
        if (chip?.dataset.relatedId) {
          event.preventDefault();
          applyDashboardInteraction("select_related_topic", chip.dataset.relatedId);
        }
      });

      coverageButton?.addEventListener("click", (event) => {
        event.preventDefault();
        loadCoverage().catch((error) => {
          const body = document.getElementById("coverage-panel-body");
          if (body) {
            body.innerHTML = `<p class="pointReportMessage">לא ניתן לטעון את מטריצת הכיסוי כרגע.</p>`;
          }
          console.warn("coverage_matrix_failed", error);
        });
      });

      mapExampleSelect?.addEventListener("change", () => {
        currentMapExample = mapExampleSelect.value || "tel_aviv_parcel";
        loadDashboardGisMap();
        if (currentMapExample === "coverage") {
          loadCoverage().catch((error) => console.warn("coverage_matrix_failed", error));
        }
        if (currentMapExample === "point_report") {
          const center = window.__municipalDashboardGisMap?.center;
          if (center?.lon !== undefined && center?.lat !== undefined) {
            loadPointReport(center.lon, center.lat).catch((error) => console.warn("point_report_failed", error));
          }
        }
      });

      document.addEventListener("click", (event) => {
        const closeButton = event.target.closest("[data-close-panel]");
        if (!closeButton) {
          return;
        }
        const panelId = closeButton.dataset.closePanel;
        setPanelHidden(panelId, true);
        if (panelId === "coverage-panel" && coverageButton) {
          coverageButton.setAttribute("aria-expanded", "false");
        }
        if (panelId === "coverage-panel") {
          document.getElementById("map-view-layers")?.setAttribute("aria-expanded", "false");
        }
      });
      document.addEventListener("click", (event) => {
        const link = event.target.closest(".sourceLink");
        if (link?.dataset.evidenceRef) {
          event.preventDefault();
          lastFocusedEvidenceLink = link;
          applyDashboardInteraction("open_evidence", link.dataset.evidenceRef);
        }
      });

      const closeEvidencePreview = () => {
        if (!evidenceDialog) {
          return;
        }
        if (typeof evidenceDialog.close === "function" && evidenceDialog.open) {
          evidenceDialog.close();
        }
        evidenceDialog.hidden = true;
        if (lastFocusedEvidenceLink) {
          lastFocusedEvidenceLink.focus();
        }
      };
      evidenceDialog?.querySelector(".evidenceClose")?.addEventListener("click", closeEvidencePreview);
      evidenceDialog?.addEventListener("cancel", (event) => {
        event.preventDefault();
        closeEvidencePreview();
      });

      if (form) {
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const question = String(questionInput && questionInput.value || "").trim() || "מה הוחלט לגבי תכנית רובע טו?";
          if (questionInput) {
            questionInput.value = question;
          }
          const topKRaw = parseInt(topKInput && topKInput.value || "8", 10);
          const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(50, topKRaw)) : 8;
          const selectedMuni = selectedMunicipalityForQuery();
          const payload = {
            question,
            top_k: topK,
            muni: selectedMuni,
            filters: activeFilters,
            debug_mode: Boolean(debugModeInput && debugModeInput.checked)
          };
          if (statusNode) {
            statusNode.textContent = "מריץ חיפוש במסמכי העירייה.";
          }
          const controller = new AbortController();
          const timeoutId = window.setTimeout(() => controller.abort(), DASHBOARD_QUERY_TIMEOUT_MS);
          try {
            const response = await fetch(DASHBOARD_QUERY_ENDPOINT, {
              method: "POST",
              headers: { "Content-Type": "application/json", Accept: "application/json" },
              body: JSON.stringify(payload),
              signal: controller.signal
            });
            window.clearTimeout(timeoutId);
            const data = await response.json().catch(() => ({}));
            if (response.ok && data && typeof data === "object") {
              renderDashboardFromEndpoint(data);
            }
            if (statusNode) {
              statusNode.textContent = response.ok ? "התשובה מוצגת בלוח המחוונים." : "החיפוש נשמר, אך שירות התשובות לא זמין כרגע.";
            }
            console.info("municipal_rag_dashboard_search", {
              question,
              muni: selectedMuni,
              ok: response.ok,
              status: data.state?.generation_status || response.status,
              evidence_count: Array.isArray(data.evidence) ? data.evidence.length : 0,
              spatial_representation: data.main_civic_workspace?.map?.spatial_representation,
              municipality_scope: data.state?.intent_resolution?.geo?.municipality_scope || null
            });
          } catch (error) {
            window.clearTimeout(timeoutId);
            if (statusNode) {
              statusNode.textContent = error?.name === "AbortError" ? "החיפוש לוקח יותר מדי זמן. נסו שוב עם שאלה ממוקדת יותר." : "החיפוש נשמר, אך שירות התשובות לא זמין כרגע.";
            }
            console.warn("municipal_rag_dashboard_search_failed", error);
          }
        });
      }

      if (filtersButton && filterModal) {
        const closeButton = filterModal.querySelector(".dialogClose");
        filtersButton.addEventListener("click", () => {
          filterModal.hidden = false;
          filtersButton.setAttribute("aria-expanded", "true");
          if (typeof filterModal.showModal === "function") {
            filterModal.showModal();
          } else {
            filterModal.setAttribute("open", "");
          }
        });
        const closeFilters = () => {
          if (typeof filterModal.close === "function" && filterModal.open) {
            filterModal.close();
          }
          filterModal.hidden = true;
          filtersButton.setAttribute("aria-expanded", "false");
          filtersButton.focus();
        };
        if (closeButton) {
          closeButton.addEventListener("click", closeFilters);
        }
        filterModal.addEventListener("cancel", (event) => {
          event.preventDefault();
          closeFilters();
        });
        const dialogActions = filterModal.querySelectorAll(".dialogAction");
        dialogActions[0]?.addEventListener("click", () => {
          activeFilters = {};
          syncFilterModal(activeFilters);
          updateFilterBadge(0);
          closeFilters();
          applyDashboardInteraction("reset_filters", "filters");
        });
        dialogActions[1]?.addEventListener("click", () => {
          const filters = collectFilterValues();
          activeFilters = filters;
          updateFilterBadge(Object.keys(activeFilters).length);
          const selectedCategoryId = categoryFilterToId(activeFilters.category);
          if (selectedCategoryId) {
            if (dashboardState) {
              dashboardState.selected_category_id = selectedCategoryId;
              window.__municipalDashboardState = dashboardState;
            }
            applySelectedCategoryToRows(selectedCategoryId);
          }
          closeFilters();
          applyDashboardInteraction("apply_filters", "filters", { filters });
        });
      }

      if (popularButton && popularPopover) {
        popularButton.addEventListener("click", () => {
          const isHidden = popularPopover.hidden;
          popularPopover.hidden = !isHidden;
          popularButton.setAttribute("aria-expanded", String(isHidden));
        });
        for (const choice of popularPopover.querySelectorAll("button")) {
          choice.addEventListener("click", () => {
            const selectedQuestion = choice.textContent.trim();
            if (questionInput) {
              questionInput.value = selectedQuestion;
              questionInput.focus();
            }
            popularPopover.hidden = true;
            popularButton.setAttribute("aria-expanded", "false");
            applyDashboardInteraction("select_popular_search", selectedQuestion);
          });
        }
      }
    })();
  </script>
  <script>
    (() => {
      const frame = document.querySelector(".mapFrame");
      const staticMap = document.getElementById("real-gis-static-map");
      const realMap = document.getElementById("real-gis-map");
      const status = document.getElementById("map-renderer-status");
      const controls = {
        reset: document.getElementById("map-view-reset"),
        zoomIn: document.getElementById("map-view-zoom-in"),
        zoomOut: document.getElementById("map-view-zoom-out"),
        layers: document.getElementById("map-view-layers"),
        popover: document.getElementById("map-layer-popover"),
        iconSize: document.getElementById("map-icon-size"),
        iconSizeLabel: document.getElementById("map-icon-size-label"),
        labelSize: document.getElementById("map-label-size"),
        labelSizeLabel: document.getElementById("map-label-size-label"),
        toggleAll: document.getElementById("map-toggle-all-filters")
      };
      if (!frame || !staticMap) {
        return;
      }

      const hasStaticMapContent = () => Boolean(staticMap.querySelector("path, image, text, [data-map-icon]"));
      const isStaticMapActive = () => !staticMap.hidden && frame.classList.contains("hasRealGis");
      const staticMapObjectSelector = '#real-gis-static-map [data-map-icon], #real-gis-static-map path[data-map-layer]:not([data-map-layer="basemap"]), #real-gis-static-map g[data-map-layer]:not([data-map-layer="basemap"])';
      if (hasStaticMapContent()) {
        realMap && (realMap.hidden = true);
        staticMap.hidden = false;
        staticMap.style.transform = "";
        frame.classList.add("hasRealGis");
        frame.classList.remove("maplibreActive");
        status && (status.textContent = "SVG GIS פעיל");
      }

      const parseViewBoxValue = (raw, fallback = { x: 0, y: 0, width: 900, height: 620 }) => {
        const parts = String(raw || "").trim().split(/\\s+/).map(Number);
        return parts.length === 4 && parts.every(Number.isFinite)
          ? { x: parts[0], y: parts[1], width: parts[2], height: parts[3] }
          : fallback;
      };
      const parseBox = () => parseViewBoxValue(staticMap.getAttribute("viewBox"));
      const worldBox = () => parseViewBoxValue(staticMap.dataset.worldViewBox, { x: 0, y: 0, width: 900, height: 620 });
      const focusBox = () => parseViewBoxValue(staticMap.dataset.focusViewBox, parseBox());
      let overviewRequested = window.__municipalDashboardGisMap?.query?.profile === "overview" || window.__municipalDashboardGisMap?.query?.profile === "full";
      const ensureOverviewForZoomOut = () => {
        const current = parseBox();
        const focus = focusBox();
        const profile = window.__municipalDashboardGisMap?.query?.profile || "initial";
        if (overviewRequested || profile === "overview" || profile === "full" || current.width < focus.width * 1.16) {
          return;
        }
        overviewRequested = true;
        window.__loadMunicipalGisMapProfile?.("overview", { preserveViewBox: true }).catch?.((error) => {
          overviewRequested = false;
          console.warn("municipal_gis_overview_failed", error);
        });
      };
      const setBox = (box) => {
        const focus = focusBox();
        const world = worldBox();
        const minWidth = focus.width / 8;
        const minHeight = focus.height / 8;
        const width = Math.max(minWidth, Math.min(world.width, box.width));
        const height = Math.max(minHeight, Math.min(world.height, box.height));
        const x = Math.max(world.x, Math.min(world.x + world.width - width, box.x));
        const y = Math.max(world.y, Math.min(world.y + world.height - height, box.y));
        staticMap.setAttribute("viewBox", `${x.toFixed(2)} ${y.toFixed(2)} ${width.toFixed(2)} ${height.toFixed(2)}`);
        ensureOverviewForZoomOut();
        window.__scheduleMunicipalVisibleBuildingLoad?.();
      };
      const zoom = (factor, event = null) => {
        const current = parseBox();
        const rect = staticMap.getBoundingClientRect();
        const anchorX = event ? current.x + ((event.clientX - rect.left) / rect.width) * current.width : current.x + current.width / 2;
        const anchorY = event ? current.y + ((event.clientY - rect.top) / rect.height) * current.height : current.y + current.height / 2;
        const width = current.width * factor;
        const height = current.height * factor;
        setBox({
          x: anchorX - ((anchorX - current.x) / current.width) * width,
          y: anchorY - ((anchorY - current.y) / current.height) * height,
          width,
          height
        });
      };
      const stop = (event) => {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation?.();
      };
      const bindButton = (button, action) => {
        button?.addEventListener("click", (event) => {
          if (!isStaticMapActive()) {
            return;
          }
          stop(event);
          action(event);
        }, true);
      };

      bindButton(controls.zoomIn, () => zoom(0.72));
      bindButton(controls.zoomOut, () => zoom(1.38));
      bindButton(controls.reset, () => setBox(focusBox()));
      bindButton(controls.layers, () => {
        if (!controls.popover) {
          return;
        }
        controls.popover.hidden = !controls.popover.hidden;
        controls.layers.setAttribute("aria-expanded", String(!controls.popover.hidden));
      });
      const closeLayerPopover = () => {
        if (!controls.popover) {
          return;
        }
        controls.popover.hidden = true;
        controls.layers?.setAttribute("aria-expanded", "false");
      };
      controls.popover?.querySelector("[data-map-layer-close]")?.addEventListener("click", (event) => {
        stop(event);
        closeLayerPopover();
        controls.layers?.focus();
      }, true);
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && controls.popover && !controls.popover.hidden) {
          closeLayerPopover();
          controls.layers?.focus();
        }
      });
      document.addEventListener("click", (event) => {
        if (!controls.popover || controls.popover.hidden) {
          return;
        }
        if (event.target.closest("#map-layer-popover, #map-view-layers")) {
          return;
        }
        closeLayerPopover();
      });

      const isChecked = (selector, value) => {
        const input = document.querySelector(`${selector}="${value}"]`);
        return !input || input.checked;
      };
      const mapFilterInputs = () => Array.from(document.querySelectorAll("[data-map-layer-toggle], [data-map-object-toggle]"));
      const updateToggleAllFiltersLabel = () => {
        if (!controls.toggleAll) {
          return;
        }
        const inputs = mapFilterInputs();
        const allChecked = inputs.length > 0 && inputs.every((input) => input.checked);
        controls.toggleAll.textContent = allChecked ? "כיבוי כל המסננים" : "הפעלת כל המסננים";
      };
      const iconSizeText = (scale) => {
        if (scale <= 0.2) return "קטן";
        if (scale <= 0.38) return "בינוני";
        return "גדול";
      };
      const labelSizeText = (scale) => {
        if (scale <= 0.32) return "קטן";
        if (scale <= 0.6) return "בינוני";
        return "גדול";
      };
      const applyMapIconScale = (scale) => {
        const normalized = Math.max(0.05, Math.min(0.6, Number(scale) || 0.05));
        window.__municipalMapIconScale = normalized;
        if (controls.iconSizeLabel) {
          controls.iconSizeLabel.textContent = iconSizeText(normalized);
        }
        for (const icon of staticMap.querySelectorAll("[data-map-icon]")) {
          const baseTransform = icon.getAttribute("data-base-transform") || "";
          icon.setAttribute("transform", `${baseTransform} scale(${normalized})`);
        }
      };
      const applyMapLabelScale = (scale) => {
        const normalized = Math.max(0.08, Math.min(0.9, Number(scale) || 0.08));
        window.__municipalMapLabelScale = normalized;
        if (controls.labelSizeLabel) {
          controls.labelSizeLabel.textContent = labelSizeText(normalized);
        }
        for (const label of document.querySelectorAll(".schematicMapSvg [data-map-label], #real-gis-static-map [data-map-label]")) {
          const baseFontSize = Number(label.getAttribute("data-base-font-size") || 13);
          const fontSize = baseFontSize * normalized;
          label.setAttribute("font-size", String(fontSize));
          label.style.fontSize = `${fontSize}px`;
        }
        applyMapLibreLabelScale();
      };
      const applyStaticMapFilters = () => {
        for (const node of staticMap.querySelectorAll("[data-map-layer], [data-map-object]")) {
          const layer = node.getAttribute("data-map-layer");
          const objectType = node.getAttribute("data-map-object");
          const layerVisible = !layer || isChecked("[data-map-layer-toggle", layer);
          const objectVisible = !objectType || isChecked("[data-map-object-toggle", objectType);
          node.style.display = layerVisible && objectVisible ? "" : "none";
        }
        updateToggleAllFiltersLabel();
      };
      const syncParcelFilterInputs = (changedInput = null) => {
        const layerInput = document.querySelector('[data-map-layer-toggle="nearby_parcels"]');
        const objectInput = document.querySelector('[data-map-object-toggle="parcels"]');
        if (!layerInput || !objectInput) {
          return;
        }
        if (changedInput === layerInput || changedInput === objectInput) {
          layerInput.checked = changedInput.checked;
          objectInput.checked = changedInput.checked;
        }
      };
      const rerenderStaticMapWithCurrentFilters = () => {
        syncParcelFilterInputs();
        if (window.__municipalDashboardGisMapServerSvgOnly) {
          applyStaticMapFilters();
          window.__scheduleMunicipalVisibleBuildingLoad?.();
          return;
        }
        if (window.__municipalDashboardGisMap && typeof window.__renderMunicipalSvgGisMap === "function") {
          window.__renderMunicipalSvgGisMap(window.__municipalDashboardGisMap, { preserveViewBox: true });
          applyMapIconScale(controls.iconSize?.value || window.__municipalMapIconScale || 0.05);
          applyMapLabelScale(controls.labelSize?.value || window.__municipalMapLabelScale || 0.08);
          return;
        }
        applyStaticMapFilters();
      };
      for (const input of mapFilterInputs()) {
        input.addEventListener("change", () => {
          syncParcelFilterInputs(input);
          rerenderStaticMapWithCurrentFilters();
        });
      }
      controls.toggleAll?.addEventListener("click", (event) => {
        stop(event);
        const inputs = mapFilterInputs();
        const shouldCheck = inputs.some((input) => !input.checked);
        for (const input of inputs) {
          input.checked = shouldCheck;
        }
        rerenderStaticMapWithCurrentFilters();
      }, true);
      controls.iconSize?.addEventListener("input", (event) => {
        if (!isStaticMapActive()) {
          return;
        }
        applyMapIconScale(event.target.value);
      });
      controls.labelSize?.addEventListener("input", (event) => {
        if (!isStaticMapActive()) {
          return;
        }
        applyMapLabelScale(event.target.value);
      });
      window.__applyMunicipalSvgFilters = applyStaticMapFilters;
      applyMapIconScale(controls.iconSize?.value || window.__municipalMapIconScale || 0.05);
      applyMapLabelScale(controls.labelSize?.value || window.__municipalMapLabelScale || 0.08);
      applyStaticMapFilters();

      let pan = null;
      const suppressFeatureClickAfterDrag = () => {
        window.__municipalSvgSuppressFeatureClick = true;
        window.setTimeout(() => {
          window.__municipalSvgSuppressFeatureClick = false;
        }, 180);
      };
      frame.addEventListener("pointerdown", (event) => {
        if (!isStaticMapActive()) {
          return;
        }
        if (event.button !== undefined && event.button !== 0) {
          return;
        }
        if (event.target.closest(".mapZoomControls, .mapLayerPopover, .mapInfoPanel, .coveragePanel")) {
          return;
        }
        if (event.target.closest(staticMapObjectSelector)) {
          return;
        }
        stop(event);
        document.querySelector(".realGisHoverCard")?.remove();
        pan = { startX: event.clientX, startY: event.clientY, box: parseBox(), moved: false };
        frame.classList.add("isPanning");
      }, true);
      document.addEventListener("pointermove", (event) => {
        if (!pan) {
          return;
        }
        stop(event);
        const rect = staticMap.getBoundingClientRect();
        const dx = ((event.clientX - pan.startX) / rect.width) * pan.box.width;
        const dy = ((event.clientY - pan.startY) / rect.height) * pan.box.height;
        if (Math.abs(event.clientX - pan.startX) > 4 || Math.abs(event.clientY - pan.startY) > 4) {
          pan.moved = true;
          window.__municipalSvgSuppressFeatureClick = true;
        }
        setBox({ ...pan.box, x: pan.box.x - dx, y: pan.box.y - dy });
      }, true);
      document.addEventListener("pointerup", () => {
        if (pan?.moved) {
          suppressFeatureClickAfterDrag();
        }
        pan = null;
        frame.classList.remove("isPanning");
      }, true);
      document.addEventListener("pointercancel", () => {
        if (pan?.moved) {
          suppressFeatureClickAfterDrag();
        }
        pan = null;
        frame.classList.remove("isPanning");
      }, true);
      frame.addEventListener("wheel", (event) => {
        if (!isStaticMapActive()) {
          return;
        }
        stop(event);
        zoom(event.deltaY < 0 ? 0.82 : 1.22, event);
      }, { capture: true, passive: false });

      window.__municipalSvgGisController = { zoom, reset: () => setBox(focusBox()), renderer: "svg" };
      window.__municipalMapDebug = () => ({
        renderer: "svg",
        frameClasses: frame.className,
        svgHidden: staticMap.hidden,
        svgDisplay: getComputedStyle(staticMap).display,
        pathCount: staticMap.querySelectorAll("path").length,
        iconCount: staticMap.querySelectorAll("[data-map-icon]").length,
        textCount: staticMap.querySelectorAll("text").length,
        viewBox: staticMap.getAttribute("viewBox"),
        focusViewBox: staticMap.dataset.focusViewBox || null,
        worldViewBox: staticMap.dataset.worldViewBox || null,
        active: isStaticMapActive(),
        payloadProfile: window.__municipalDashboardGisMap?.query?.profile || null,
        lastError: window.__municipalMapLastError ? String(window.__municipalMapLastError?.stack || window.__municipalMapLastError) : null
      });
    })();
  </script>
</body>
</html>
"""
    if initial_gis_map_payload and initial_gis_map_payload.get("status") == "found":
        return _inject_initial_gis_map(html, initial_gis_map_payload)
    return html


def _inject_initial_gis_map(html: str, payload: dict[str, Any]) -> str:
    static_svg = _server_rendered_gis_svg(payload)
    if not static_svg:
        return html
    payload_json = json.dumps(_client_bootstrap_gis_payload(payload), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    payload_script = (
        "<script>"
        f"window.__municipalDashboardGisMap={payload_json};"
        "window.__municipalDashboardGisMapFromServer=true;"
        "window.__municipalDashboardGisMapServerSvgOnly=true;"
        "</script>"
    )
    html = html.replace("</head>", f"{payload_script}\n</head>", 1)
    html = html.replace('<div class="mapFrame">', '<div class="mapFrame hasRealGis" data-server-rendered-gis="true">', 1)
    html = html.replace(
        '<svg id="real-gis-static-map" class="realGisStaticMap" viewBox="0 0 900 620" role="img" aria-label="מפת GIS אמיתית ללא ספריית מפה חיצונית" hidden></svg>',
        static_svg,
        1,
    )
    html = html.replace('<p class="mapProvenanceBadgeTitle">מפה סכמטית בלבד</p>', '<p class="mapProvenanceBadgeTitle">מפת GIS אמיתית</p>', 1)
    html = html.replace(
        '<p class="mapProvenanceBadgeText">אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.</p>',
        '<p class="mapProvenanceBadgeText">החלקה ונקודות העניין נטענו ממקורות GIS עם provenance. הרקע המפורט אופציונלי כדי שכל אובייקט גלוי יהיה ניתן לסינון.</p>',
        1,
    )
    html = html.replace('data-spatial-representation="schematic"', 'data-spatial-representation="real_gis_static"', 1)
    return html


def _client_bootstrap_gis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def trim_layer(layer: Any) -> Any:
        if not isinstance(layer, dict):
            return layer
        return {key: value for key, value in layer.items() if key != "items"} | {"items": []}

    parcel = payload.get("parcel") if isinstance(payload.get("parcel"), dict) else {}
    # The server already sent the detailed SVG paths; the bootstrap object only
    # keeps metadata needed for counts, selected municipality, and lazy loading.
    return {
        "status": payload.get("status"),
        "real_gis_available": payload.get("real_gis_available"),
        "selected_example": payload.get("selected_example"),
        "title_he": payload.get("title_he"),
        "center": payload.get("center"),
        "zoom": payload.get("zoom"),
        "query": payload.get("query"),
        "basemap": payload.get("basemap"),
        "visual_context": payload.get("visual_context"),
        "parcel": parcel,
        "layers": {key: trim_layer(layer) for key, layer in ((payload.get("layers") or {}) if isinstance(payload.get("layers"), dict) else {}).items()},
        "nearby_pois": trim_layer(payload.get("nearby_pois")),
    }


def _server_rendered_gis_svg(payload: dict[str, Any]) -> str:
    parcel = payload.get("parcel") if isinstance(payload.get("parcel"), dict) else {}
    geometry = parcel.get("geometry") if isinstance(parcel.get("geometry"), dict) else None
    if not geometry:
        return ""
    project = _server_projection(payload, local_only=True)
    if project is None:
        return ""
    selected_area = _selected_area_feature(payload)
    selected_area_path = _polygon_paths(selected_area.get("geometry"), project) if selected_area else ""
    parcel_path = _polygon_paths(geometry, project)
    label_point = _geometry_label_point(selected_area.get("geometry"), project) if selected_area else _geometry_label_point(geometry, project)
    label_node = ""
    if label_point is not None:
        label_text = "אזור נבחר"
        label_width = max(20, min(42, len(label_text) * 2.7 + 7))
        label_node = (
            f'<g data-map-layer="parcel" data-map-object="selected_area">'
            f'<rect x="{label_point[0] - label_width / 2:.1f}" y="{label_point[1] - 2.7:.1f}" width="{label_width:.1f}" height="5.4" rx="2.4" fill="#7c4dce" opacity="0.94" />'
            f'<text x="{label_point[0]:.1f}" y="{label_point[1] + 1.05:.1f}" text-anchor="middle" direction="rtl" font-size="2.8" font-weight="900" fill="#ffffff">{label_text}</text>'
            f'</g>'
        )
    title = _escape_text(str(payload.get("title_he") or "מפת GIS אמיתית"))
    parcel_label = _escape_text(str(parcel.get("label") or "חלקה"))
    world_view_box = _server_view_box_string(getattr(project, "world_view_box", {"x": 0, "y": 0, "width": 900, "height": 620}))
    focus_view_box = _server_view_box_string(getattr(project, "focus_view_box", {"x": 0, "y": 0, "width": 900, "height": 620}))
    bounds = getattr(project, "bounds", {}) if isinstance(getattr(project, "bounds", {}), dict) else {}
    geo_bounds = _escape_text(
        ",".join(
            f"{float(bounds.get(key, 0.0)):.8f}"
            for key in ("min_lon", "min_lat", "max_lon", "max_lat")
        )
    )
    initial_layers = _server_initial_layer_nodes(payload, project)
    neighborhood_labels = _layer_text_labels(payload, "neighborhoods", project, "neighborhoods")
    overlay_icons = _server_overlay_icon_nodes(payload, project)
    return f'''
            <svg id="real-gis-static-map" class="realGisStaticMap" viewBox="{focus_view_box}" data-world-view-box="{world_view_box}" data-focus-view-box="{focus_view_box}" data-geo-bounds="{geo_bounds}" role="img" aria-label="מפת GIS אמיתית ללא ספריית מפה חיצונית">
              <title>{title}</title>
              <rect x="0" y="0" width="900" height="620" fill="#bde7f7" />
              {initial_layers}
              <path data-map-layer="parcel" data-map-object="selected_area" d="{selected_area_path}" fill="rgba(124,77,206,0.20)" stroke="rgba(255,255,255,0.96)" stroke-width="7" stroke-linejoin="round"><title>אזור נבחר</title></path>
              <path data-map-layer="parcel" data-map-object="selected_area" d="{selected_area_path}" fill="rgba(124,77,206,0.22)" stroke="#7c4dce" stroke-width="2.6" stroke-linejoin="round"><title>אזור נבחר</title></path>
              <path data-map-layer="parcel" data-map-object="selected_area" d="{parcel_path}" fill="rgba(255,255,255,0.52)" stroke="#5b21b6" stroke-width="2.1" stroke-linejoin="round"><title>{parcel_label}</title></path>
              {neighborhood_labels}
              {overlay_icons}
            </svg>'''


def _server_initial_layer_nodes(payload: dict[str, Any], project) -> str:
    nodes: list[str] = []
    nodes.append(_server_basemap_node(project))
    nodes.append(_server_layer_path_node(payload, "context_water", project, kind="polygon", data_layer="osm", data_object="osm_water", fill="rgba(164, 216, 235, 0.44)", stroke="rgba(87, 167, 199, 0.22)", stroke_width="0.45", title="מים OSM", extra=' style="display:none"'))
    nodes.append(_server_layer_path_node(payload, "context_waterways", project, kind="line", data_layer="osm", data_object="osm_water", stroke="rgba(87, 167, 199, 0.45)", stroke_width="1.1", title="ערוצי מים OSM", extra=' style="display:none"'))
    nodes.append(_server_land_mask_node(payload, project))
    nodes.append(_server_layer_path_node(payload, "municipal_beaches", project, kind="polygon", data_layer="municipal", data_object="municipal_beaches", fill="rgba(164, 216, 235, 0.34)", stroke="rgba(87, 167, 199, 0.30)", stroke_width="0.55", title="חופים עירוניים בבדיקת רישיון"))
    nodes.append(_server_layer_path_node(payload, "context_landuse", project, kind="polygon", data_layer="osm", data_object="osm_parks", fill="rgba(190, 233, 203, 0.54)", stroke="rgba(93, 173, 120, 0.16)", stroke_width="0.45", title="שימושי קרקע OSM", extra=' style="display:none"'))
    nodes.append(_server_layer_path_node(payload, "municipal_parks", project, kind="polygon", data_layer="municipal", data_object="municipal_parks", fill="rgba(190, 233, 203, 0.42)", stroke="rgba(74, 160, 104, 0.18)", stroke_width="0.45", title="גנים ופארקים עירוניים בבדיקת רישיון"))
    nodes.append(_server_layer_path_node(payload, "context_roads", project, kind="line", data_layer="osm", data_object="osm_roads", stroke="rgba(148, 163, 184, 0.42)", stroke_width="2.6", title="דרכים OSM"))
    nodes.append(_server_layer_path_node(payload, "context_roads", project, kind="line", data_layer="osm", data_object="osm_roads", stroke="rgba(255, 255, 255, 0.72)", stroke_width="1.25", title="דרכים OSM"))
    nodes.append(_server_layer_path_node(payload, "context_railways", project, kind="line", data_layer="osm", data_object="osm_railways", stroke="rgba(11, 104, 209, 0.58)", stroke_width="1.6", title="תוואי תחבורה OSM", extra=' stroke-dasharray="7 7"'))
    nodes.append(_server_layer_path_node(payload, "municipal_bike_paths", project, kind="line", data_layer="municipal", data_object="municipal_bike_paths", stroke="rgba(6, 182, 212, 0.46)", stroke_width="1", title="שבילי אופניים עירוניים", extra=' stroke-dasharray="5 5"'))
    nodes.append(_server_layer_path_node(payload, "nearby_parcels", project, kind="polygon", data_layer="nearby_parcels", data_object="parcels", fill="none", stroke="rgba(101, 84, 52, 0.55)", stroke_width="0.55", title="חלקות MAPI בעיר"))
    nodes.append(_server_layer_path_node(payload, "buildings", project, kind="polygon", data_layer="municipal", data_object="buildings", fill="rgba(68, 76, 86, 0.26)", stroke="rgba(30, 41, 59, 0.48)", stroke_width="0.42", title="מבנים עירוניים בבדיקת רישיון"))
    return "".join(node for node in nodes if node)


def _server_land_mask_node(payload: dict[str, Any], project) -> str:
    nodes: list[str] = []
    neighbor_path_data = _layer_polygon_paths(payload, "neighboring_municipal_boundaries", project)
    if neighbor_path_data:
        nodes.append(
            f'<path data-map-layer="land" data-map-object="neighboring_municipality" d="{_escape_text(neighbor_path_data)}" '
            'fill="#edf2ee" fill-opacity="0.88" stroke="rgba(100, 116, 139, 0.34)" stroke-width="0.85" stroke-linejoin="round" style="pointer-events:none">'
            '<title>רשויות סמוכות מעל רקע ים וקטורי</title></path>'
        )
    tel_aviv_path_data = _layer_polygon_paths(payload, "municipal_boundaries", project)
    if tel_aviv_path_data:
        nodes.append(
            f'<path data-map-layer="land" data-map-object="land" d="{_escape_text(tel_aviv_path_data)}" '
            'fill="#edf2ee" fill-opacity="0.96" stroke="rgba(15, 118, 110, 0.74)" stroke-width="1.7" stroke-linejoin="round" style="pointer-events:none">'
            '<title>תחום תל אביב מעל רקע ים וקטורי</title></path>'
        )
    return "".join(nodes)


def _server_overlay_icon_nodes(payload: dict[str, Any], project) -> str:
    nodes: list[str] = []
    nodes.append(_server_geometry_icon_nodes(payload, "context_landuse", project, category="park", data_layer="osm", data_object="osm_parks", limit=70))
    nodes.append(_server_geometry_icon_nodes(payload, "municipal_parks", project, category="park", data_layer="municipal", data_object="municipal_parks", limit=120))
    nodes.append(_server_point_icon_nodes((payload.get("nearby_pois") or {}).get("items", []), project, data_layer="pois", limit=160))
    nodes.append(_server_point_icon_nodes(((payload.get("layers") or {}).get("address_points") or {}).get("items", []), project, data_layer="municipal", limit=60, data_object="addresses"))
    nodes.append(_server_point_icon_nodes(((payload.get("layers") or {}).get("municipal_pois") or {}).get("items", []), project, data_layer="municipal", limit=120))
    nodes.append(_server_point_icon_nodes(((payload.get("layers") or {}).get("context_pois") or {}).get("items", []), project, data_layer="osm", limit=80, fallback_category="interest", data_object="osm_interest"))
    return "".join(node for node in nodes if node)


def _server_overlay_label_nodes(payload: dict[str, Any], project) -> str:
    return "".join(
        node
        for node in (
            _layer_text_labels(payload, "neighborhoods", project, "neighborhoods"),
        )
        if node
    )


def _server_basemap_node(project) -> str:
    bounds = getattr(project, "bounds", None)
    if not isinstance(bounds, dict):
        return ""
    min_lon = bounds.get("min_lon")
    min_lat = bounds.get("min_lat")
    max_lon = bounds.get("max_lon")
    max_lat = bounds.get("max_lat")
    if not all(isinstance(value, (int, float)) for value in (min_lon, min_lat, max_lon, max_lat)):
        return ""
    bbox = f"{min_lon:.8f},{min_lat:.8f},{max_lon:.8f},{max_lat:.8f}"
    params = urlencode({"bbox": bbox, "bboxSR": "4326", "imageSR": "4326", "size": "900,620", "format": "png32", "transparent": "false", "f": "image"})
    href = _escape_text(f"https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2MapHeb/MapServer/export?{params}")
    return f'<image x="0" y="0" width="900" height="620" preserveAspectRatio="none" href="{href}" data-map-layer="basemap" data-unfilterable-raster="true" opacity="0.42" style="display:none" />'


def _server_layer_path_node(
    payload: dict[str, Any],
    layer_key: str,
    project,
    *,
    kind: str,
    data_layer: str,
    data_object: str,
    stroke: str,
    stroke_width: str,
    title: str,
    fill: str = "none",
    extra: str = "",
) -> str:
    path_data = _layer_polygon_paths(payload, layer_key, project) if kind == "polygon" else _layer_line_paths(payload, layer_key, project)
    if not path_data:
        return ""
    fill_attr = f' fill="{fill}"' if kind == "polygon" else ' fill="none"'
    return (
        f'<path data-map-layer="{data_layer}" data-map-object="{data_object}" d="{_escape_text(path_data)}"{fill_attr} '
        f'stroke="{stroke}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"{extra}>'
        f'<title>{_escape_text(title)}</title></path>'
    )


def _server_geometry_icon_nodes(payload: dict[str, Any], layer_key: str, project, *, category: str, data_layer: str, data_object: str, limit: int) -> str:
    nodes: list[str] = []
    candidates: list[tuple[float, float, str, str, str]] = []
    for item in (((payload.get("layers") or {}).get(layer_key) or {}).get("items", []) or []):
        if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
            continue
        label_geometry = item.get("label_point") if isinstance(item.get("label_point"), dict) else item["geometry"]
        point = _geometry_label_point(label_geometry, project)
        if point is None:
            continue
        label = _clean_map_label(str(item.get("name_he") or item.get("name_en") or category)) or category
        candidates.append((point[0], point[1], category, _escape_text(label), _escape_text(str(item.get("provenance_id") or item.get("source_id") or layer_key))))
    for x, y, item_category, label, provenance_id in _spread_icon_candidates(candidates, limit=limit):
        nodes.append(_server_icon_node(x, y, item_category, data_layer, label, provenance_id, data_object=data_object))
    return "".join(nodes)


def _server_point_icon_nodes(items: list[dict[str, Any]], project, *, data_layer: str, limit: int, fallback_category: str | None = None, data_object: str | None = None) -> str:
    nodes: list[str] = []
    candidates: list[tuple[float, float, str, str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        x, y = project((float(coordinates[0]), float(coordinates[1])))
        category = str(item.get("poi_category") or item.get("feature_type") or fallback_category or "pin")
        label = _clean_map_label(str(item.get("name_he") or item.get("name_en") or category)) or category
        candidates.append((x, y, category, _escape_text(label), _escape_text(str(item.get("provenance_id") or item.get("source_id") or "gis"))))
    for x, y, category, label, provenance_id in _spread_icon_candidates(candidates, limit=limit):
        nodes.append(_server_icon_node(x, y, category, data_layer, label, provenance_id, data_object=data_object))
    return "".join(nodes)


def _spread_icon_candidates(candidates: list[tuple[float, float, str, str, str]], *, limit: int) -> list[tuple[float, float, str, str, str]]:
    if limit <= 0:
        return []
    if len(candidates) <= limit:
        return candidates
    cell_size = 76.0
    selected: list[tuple[float, float, str, str, str]] = []
    remaining: list[tuple[float, float, str, str, str]] = []
    seen_cells: set[tuple[int, int]] = set()
    for candidate in candidates:
        x, y = candidate[0], candidate[1]
        cell = (int(x // cell_size), int(y // cell_size))
        if cell not in seen_cells and len(selected) < limit:
            selected.append(candidate)
            seen_cells.add(cell)
        else:
            remaining.append(candidate)
    if len(selected) < limit:
        selected.extend(remaining[: limit - len(selected)])
    return selected


def _selected_area_feature(payload: dict[str, Any]) -> dict[str, Any] | None:
    parcel = payload.get("parcel") if isinstance(payload.get("parcel"), dict) else {}
    return parcel if isinstance(parcel.get("geometry"), dict) else None


def _server_view_box_string(box: dict[str, float]) -> str:
    return f'{box["x"]:.2f} {box["y"]:.2f} {box["width"]:.2f} {box["height"]:.2f}'


def _server_box_from_projected_points(points: list[tuple[float, float]]) -> dict[str, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}


def _server_fit_box_to_aspect(box: dict[str, float], aspect: float) -> dict[str, float]:
    current_aspect = box["width"] / (box["height"] or 1)
    if current_aspect > aspect:
        height = box["width"] / aspect
        return {**box, "y": box["y"] - (height - box["height"]) / 2, "height": height}
    width = box["height"] * aspect
    return {**box, "x": box["x"] - (width - box["width"]) / 2, "width": width}


def _server_clamp_box_to_world(box: dict[str, float], world: dict[str, float]) -> dict[str, float]:
    width = min(world["width"], max(1, box["width"]))
    height = min(world["height"], max(1, box["height"]))
    return {
        "x": max(world["x"], min(world["x"] + world["width"] - width, box["x"])),
        "y": max(world["y"], min(world["y"] + world["height"] - height, box["y"])),
        "width": width,
        "height": height,
    }


def _server_focus_view_box(geometry: dict[str, Any], project, world: dict[str, float]) -> dict[str, float]:
    points: list[tuple[float, float]] = []
    _collect_points(geometry.get("coordinates"), points)
    raw = _server_box_from_projected_points([project(point) for point in points])
    if raw is None:
        return world
    pad_x = max(raw["width"] * 0.72, 38)
    pad_y = max(raw["height"] * 0.72, 26)
    padded = {"x": raw["x"] - pad_x, "y": raw["y"] - pad_y, "width": raw["width"] + pad_x * 2, "height": raw["height"] + pad_y * 2}
    return _server_clamp_box_to_world(_server_fit_box_to_aspect(padded, 900 / 620), world)


def _server_projection(payload: dict[str, Any], *, local_only: bool = False):
    points: list[tuple[float, float]] = []
    selected_area = _selected_area_feature(payload)
    parcel = payload.get("parcel") if isinstance(payload.get("parcel"), dict) else {}
    geometry = parcel.get("geometry") if isinstance(parcel.get("geometry"), dict) else {}
    for layer_key in ("municipal_boundaries", "neighboring_municipal_boundaries"):
        for item in ((payload.get("layers") or {}).get(layer_key) or {}).get("items", []):
            if isinstance(item, dict) and isinstance(item.get("geometry"), dict):
                _collect_points(item["geometry"].get("coordinates"), points)
    if not points:
        if selected_area and isinstance(selected_area.get("geometry"), dict):
            _collect_points(selected_area["geometry"].get("coordinates"), points)
        _collect_points(geometry.get("coordinates"), points)
        layer_keys = () if local_only else ("nearby_parcels", "buildings", "context_pois", "context_roads", "context_railways", "context_waterways", "context_water", "context_landuse", "municipal_parks", "municipal_beaches", "municipal_bike_paths", "neighborhoods", "address_points", "municipal_pois", "neighboring_municipal_boundaries")
        for layer_key in layer_keys:
            for item in ((payload.get("layers") or {}).get(layer_key) or {}).get("items", []):
                if isinstance(item, dict) and isinstance(item.get("geometry"), dict):
                    _collect_points(item["geometry"].get("coordinates"), points)
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    lon_pad = max((max_lon - min_lon) * 0.18, 0.001)
    lat_pad = max((max_lat - min_lat) * 0.18, 0.001)
    min_lon -= lon_pad
    max_lon += lon_pad
    min_lat -= lat_pad
    max_lat += lat_pad

    def project(point: tuple[float, float]) -> tuple[float, float]:
        lon, lat = point
        x = ((lon - min_lon) / ((max_lon - min_lon) or 1)) * 900
        y = 620 - ((lat - min_lat) / ((max_lat - min_lat) or 1)) * 620
        return x, y

    project.bounds = {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}
    project.world_view_box = {"x": 0, "y": 0, "width": 900, "height": 620}
    focus_geometry = selected_area.get("geometry") if selected_area and isinstance(selected_area.get("geometry"), dict) else geometry
    project.focus_view_box = _server_focus_view_box(focus_geometry, project, project.world_view_box)
    return project


def _polygon_paths(geometry: dict[str, Any], project) -> str:
    polygons = [geometry.get("coordinates") or []] if geometry.get("type") == "Polygon" else geometry.get("coordinates") or []
    paths = []
    for polygon in polygons:
        if not isinstance(polygon, list):
            continue
        for ring in polygon:
            if not isinstance(ring, list):
                continue
            projected = [project((float(point[0]), float(point[1]))) for point in ring if isinstance(point, list) and len(point) >= 2]
            if projected:
                paths.append("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in projected) + " Z")
    return " ".join(paths)


def _layer_polygon_paths(payload: dict[str, Any], layer_key: str, project) -> str:
    return " ".join(
        _polygon_paths(item.get("geometry"), project)
        for item in ((payload.get("layers") or {}).get(layer_key) or {}).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("geometry"), dict)
    )


def _line_paths(geometry: dict[str, Any], project) -> str:
    lines = [geometry.get("coordinates") or []] if geometry.get("type") == "LineString" else geometry.get("coordinates") or []
    paths = []
    for line in lines:
        if not isinstance(line, list):
            continue
        projected = [project((float(point[0]), float(point[1]))) for point in line if isinstance(point, list) and len(point) >= 2]
        if projected:
            paths.append("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in projected))
    return " ".join(paths)


def _layer_line_paths(payload: dict[str, Any], layer_key: str, project) -> str:
    return " ".join(
        _line_paths(item.get("geometry"), project)
        for item in ((payload.get("layers") or {}).get(layer_key) or {}).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("geometry"), dict)
    )


def _layer_text_labels(payload: dict[str, Any], layer_key: str, project, object_type: str, *, fallback_label: str = "") -> str:
    labels = []
    for item in ((payload.get("layers") or {}).get(layer_key) or {}).get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
            continue
        label = _clean_map_label(str(item.get("name_he") or item.get("name_en") or fallback_label))
        label_geometry = item.get("label_point") if isinstance(item.get("label_point"), dict) else item["geometry"]
        point = _geometry_label_point(label_geometry, project)
        if not label or point is None:
            continue
        x, y = point
        labels.append(
            f'<text class="realGisStaticLabel" data-map-layer="municipal" data-map-object="{object_type}" data-map-label="true" data-base-font-size="11" font-size="0.88" style="font-size:0.88px" x="{x:.1f}" y="{y:.1f}">{_escape_text(label)}</text>'
        )
    return "".join(labels)


def _clean_map_label(value: str) -> str:
    label = str(value or "").strip()
    if "מקורות GIS שנמצאו" in label or "discovered sources" in label.lower():
        return ""
    return label


def _geometry_label_point(geometry: dict[str, Any], project) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    _collect_points(geometry.get("coordinates"), points)
    if not points:
        return None
    projected = [project(point) for point in points]
    return (sum(point[0] for point in projected) / len(projected), sum(point[1] for point in projected) / len(projected))


def _server_icon_node(x: float, y: float, category: str, layer: str, label: str, provenance_id: str, *, data_object: str | None = None) -> str:
    color = _server_poi_color(category)
    icon = _server_poi_icon(category)
    object_type = data_object or _server_map_object_type(category)
    glyph = _server_icon_glyph(icon)
    base_transform = f"translate({x:.1f} {y:.1f})"
    hidden_style = ' style="display:none"' if object_type == "osm_parks" else ""
    return (
        f'<g data-map-layer="{layer}" data-map-object="{object_type}" data-map-icon="true" '
        f'data-base-transform="{base_transform}" transform="{base_transform} scale(0.05)" opacity="0.92"{hidden_style}>'
        f'<title>{label} · {provenance_id}</title>'
        f'<circle r="15" fill="rgba(255,255,255,0.90)" stroke="{color}" stroke-width="1.4" />'
        f'<path d="{glyph}" fill="{color}" stroke="none" />'
        f'</g>'
    )


def _server_poi_color(category: str) -> str:
    if category in {"school", "municipal_school", "kindergarten"}:
        return "#f59e0b"
    if category == "transport_stop":
        return "#0b68d1"
    if category == "address_point":
        return "#9333ea"
    if category == "park":
        return "#18a66a"
    if category in {"municipal_poi", "community_center", "culture"}:
        return "#fb923c"
    if category == "parking":
        return "#2563eb"
    return "#9ca3af"


def _server_poi_icon(category: str) -> str:
    if category == "transport_stop":
        return "bus"
    if category in {"school", "municipal_school"}:
        return "school"
    if category in {"municipal_poi", "community_center", "culture"}:
        return "building"
    if category == "park":
        return "tree"
    if category == "kindergarten":
        return "child"
    if category == "address_point":
        return "home"
    if category == "parking":
        return "parking"
    return "pin"


def _server_map_object_type(category: str) -> str:
    if category == "transport_stop":
        return "transport"
    if category in {"school", "municipal_school", "kindergarten"}:
        return "education"
    if category == "address_point":
        return "addresses"
    if category == "park":
        return "osm_parks"
    if category == "interest":
        return "osm_interest"
    if category in {"community_center", "culture", "parking", "municipal_poi"}:
        return "municipal_pois"
    return "osm_interest"


def _server_icon_glyph(kind: str) -> str:
    return {
        "bus": "M -9 -5 H 9 V 5 H 7 V 8 H 4 V 5 H -4 V 8 H -7 V 5 H -9 Z M -6 -2 H -1 V 1 H -6 Z M 1 -2 H 6 V 1 H 1 Z",
        "school": "M -9 -1 L 0 -8 L 9 -1 L 6 -1 V 8 H -6 V -1 Z M -2 2 H 2 V 8 H -2 Z",
        "building": "M -8 8 V -7 H 8 V 8 H 4 V 4 H 1 V 8 H -1 V 4 H -4 V 8 Z M -5 -4 H -3 V -2 H -5 Z M -1 -4 H 1 V -2 H -1 Z M 3 -4 H 5 V -2 H 3 Z M -5 0 H -3 V 2 H -5 Z M 3 0 H 5 V 2 H 3 Z",
        "tree": "M 0 -9 C -5 -9 -8 -5 -6 -1 C -10 1 -8 6 -3 5 H -1 V 9 H 1 V 5 H 3 C 8 6 10 1 6 -1 C 8 -5 5 -9 0 -9 Z",
        "child": "M 0 -8 A 3 3 0 1 1 0 -2 A 3 3 0 1 1 0 -8 M -7 8 L -3 -1 H 3 L 7 8 H 3 L 1 3 H -1 L -3 8 Z",
        "home": "M -8 0 L 0 -7 L 8 0 H 5 V 8 H -5 V 0 Z",
        "parking": "M -8 -8 H 2 C 7 -8 8 -1 3 1 H -3 V 8 H -8 Z M -3 -4 V -1 H 1 C 3 -1 3 -4 1 -4 Z",
        "culture": "M -8 -6 H 8 V 2 C 8 6 4 8 0 8 C -4 8 -8 6 -8 2 Z M -5 -3 C -3 -1 -1 -1 1 -3 M 3 -3 C 4 -1 5 -1 6 -3",
        "community": "M -8 8 V 0 L -3 -4 L 0 -1 L 3 -4 L 8 0 V 8 H 3 V 3 H -3 V 8 Z",
        "pin": "M 0 9 C -5 3 -7 0 -7 -4 A 7 7 0 1 1 7 -4 C 7 0 5 3 0 9 Z M 0 -1 A 3 3 0 1 0 0 -7 A 3 3 0 1 0 0 -1",
    }.get(kind, "M 0 9 C -5 3 -7 0 -7 -4 A 7 7 0 1 1 7 -4 C 7 0 5 3 0 9 Z")


def _collect_points(coordinates: Any, out: list[tuple[float, float]]) -> None:
    if isinstance(coordinates, list) and len(coordinates) >= 2 and all(isinstance(value, (int, float)) for value in coordinates[:2]):
        out.append((float(coordinates[0]), float(coordinates[1])))
        return
    if isinstance(coordinates, list):
        for item in coordinates:
            _collect_points(item, out)


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
