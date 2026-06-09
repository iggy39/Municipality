from __future__ import annotations


def render_rag_dashboard_page() -> str:
    return """
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

    .mapFrame {
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 0;
      direction: ltr;
      overflow: hidden;
      border-radius: inherit;
      background: linear-gradient(90deg, #bde7f7 0%, #dff6fb 18%, #f5f7f6 29%, #f8faf8 100%);
    }

    .schematicMapSvg {
      width: 100%;
      height: 100%;
      display: block;
    }

    .mapControls {
      position: absolute;
      top: 12px;
      left: 12px;
      display: grid;
      gap: 10px;
      z-index: 3;
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
      width: 46px;
      height: 46px;
      border: 0;
      border-bottom: 1px solid #e8edf3;
      background: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: #111827;
      cursor: pointer;
    }

    .mapControlButton:last-child { border-bottom: 0; }
    .mapControlButton:hover { background: #f7f9fc; }

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
      width: 145px;
      padding: 14px 12px;
      border: 1px solid #d9e1ea;
      border-radius: 8px;
      background: rgba(255,255,255,0.94);
      box-shadow: 0 8px 22px rgba(23, 36, 55, 0.16);
      direction: rtl;
    }

    .legendTitle {
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 850;
      text-align: center;
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

    .legendSwatch {
      width: 18px;
      height: 18px;
      border-radius: 4px;
      display: inline-block;
      justify-self: center;
    }

    .legendSwatch.area { background: #efe7ff; border: 2px solid #7a4de8; }
    .legendSwatch.transit { border-radius: 999px; background: #1377d4; position: relative; }
    .legendSwatch.park { border-radius: 999px; background: #18a865; }
    .legendSwatch.building { border-radius: 999px; background: var(--orange); }
    .legendSwatch.interest { border-radius: 999px; background: #b9c0ca; }

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

              <path d="M525 -10 C508 54 514 123 548 184 C574 231 608 263 600 328 C592 388 559 447 562 518 C563 556 581 592 606 632" fill="none" stroke="#1676d2" stroke-width="2" stroke-dasharray="8 8" opacity="0.95" />

              <path data-map-entity-id="entity_rova_tet_vav_selected_area" tabindex="0" role="button" aria-label="רובע טו" d="M336 330 377 238 455 250 518 287 491 344 426 341 404 392 362 367 366 346Z" fill="rgba(111,70,217,0.20)" stroke="#6f46d9" stroke-width="2.2" />
              <rect data-map-entity-id="entity_rova_tet_vav_selected_area" x="414" y="292" width="78" height="34" rx="9" fill="#6f46d9" filter="url(#mapShadow)" />
              <text data-map-entity-id="entity_rova_tet_vav_selected_area" id="map-selected-label" x="453" y="314" fill="#fff" font-size="17" font-weight="800" text-anchor="middle" direction="rtl">רובע טו</text>

              <g fill="#1f2937" font-size="17" font-weight="850" text-anchor="middle" direction="rtl">
                <text id="map-area-north" x="590" y="44">צפון העיר</text>
                <text id="map-area-center" x="489" y="261">מרכז העיר</text>
                <text id="map-area-west" x="238" y="307">מערב העיר</text>
                <text id="map-area-east" x="806" y="292">מזרח העיר</text>
                <text id="map-area-south" x="450" y="555">דרום העיר</text>
              </g>
              <text id="map-sea-label" x="136" y="166" fill="#0b68d1" font-size="16" font-weight="850" text-anchor="middle" direction="rtl">חוף הים</text>

              <g class="mapMarkers" filter="url(#mapShadow)">
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" transform="translate(520 90)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" transform="translate(587 248)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" transform="translate(552 373)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="תחנת תחבורה ציבורית" transform="translate(528 500)">
                  <circle r="15" fill="#0b68d1" />
                  <path d="M-6-5h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z" fill="#fff" />
                  <path d="M-4 8h2M2 8h2M-5-1h10" stroke="#0b68d1" stroke-width="1.3" stroke-linecap="round" />
                </g>

                <g tabindex="0" role="button" aria-label="פארק" transform="translate(330 86)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק" transform="translate(590 324)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק" transform="translate(301 410)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק" transform="translate(684 213)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>
                <g tabindex="0" role="button" aria-label="פארק" transform="translate(764 545)">
                  <circle r="17" fill="#fff" />
                  <path d="M0-10c6 0 9 6 5 11h3c2 6-4 9-8 5-4 4-10 1-8-5h3c-4-5-1-11 5-11Z" fill="#18a865" />
                  <path d="M0 5v8" stroke="#0f8b54" stroke-width="2" stroke-linecap="round" />
                </g>

                <g tabindex="0" role="button" aria-label="מבנה ציבור" transform="translate(375 220)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
                <g tabindex="0" role="button" aria-label="מבנה ציבור" transform="translate(585 216)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
                <g tabindex="0" role="button" aria-label="מבנה ציבור" transform="translate(720 382)">
                  <circle r="18" fill="#f07a28" />
                  <path d="M-8 8V-4l8-5 8 5V8M-11 8h22M-4 8V0h8v8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </g>
              </g>
            </svg>

            <aside class="mapProvenanceBadge" data-spatial-representation="schematic" aria-label="מקוריות המפה">
              <p class="mapProvenanceBadgeTitle">מפה סכמטית בלבד</p>
              <p class="mapProvenanceBadgeText">אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.</p>
            </aside>

            <div class="mapControls" aria-label="פקדי מפה">
              <div class="mapControlGroup">
                <button class="mapControlButton" type="button" aria-label="מרכז מפה">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" stroke-linecap="round" /></svg>
                </button>
              </div>
              <div class="mapControlGroup">
                <button class="mapControlButton" type="button" aria-label="התקרבות">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke-linecap="round" /></svg>
                </button>
                <button class="mapControlButton" type="button" aria-label="התרחקות">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M5 12h14" stroke-linecap="round" /></svg>
                </button>
              </div>
              <div class="mapControlGroup">
                <button class="mapControlButton" type="button" aria-label="שכבות מפה">
                  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5Z" stroke-linejoin="round" /><path d="m4 12 8 4 8-4M4 16l8 4 8-4" stroke-linejoin="round" /></svg>
                </button>
              </div>
            </div>

            <section class="legendCard" aria-label="מקרא מפה">
              <h2 class="legendTitle">מקרא</h2>
              <ul class="legendList">
                <li class="legendItem"><span class="legendSwatch area"></span><span>אזור נבחר</span></li>
                <li class="legendItem"><span class="legendSwatch transit"></span><span>תחבורה ציבורית</span></li>
                <li class="legendItem"><span class="legendSwatch park"></span><span>פארקים</span></li>
                <li class="legendItem"><span class="legendSwatch building"></span><span>מבני ציבור</span></li>
                <li class="legendItem"><span class="legendSwatch interest"></span><span>מוקדי עניין</span></li>
              </ul>
            </section>
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
      const dashboardRoot = document.getElementById("rag-dashboard");
      const DASHBOARD_DATA_ENDPOINT = dashboardRoot?.dataset.dashboardEndpoint || "/api/ui/rag-dashboard/mock";
      const DASHBOARD_QUERY_ENDPOINT = "/api/ui/rag-dashboard/query";
      const DASHBOARD_INTERACTION_ENDPOINT = "/api/ui/rag-dashboard/interaction";
      const DASHBOARD_EVIDENCE_ENDPOINT = "/api/ui/rag-dashboard/evidence";
      const DASHBOARD_QUERY_TIMEOUT_MS = 45000;
      const evidenceDialog = document.getElementById("evidence-preview");
      let currentDashboardData = null;
      let dashboardState = null;
      let dashboardRequestSeq = 0;
      let lastFocusedEvidenceLink = null;
      let activeFilters = {};

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
        setText(".legendTitle", mapCopy.legend_title);
        const legendLabels = Array.from(document.querySelectorAll(".legendItem span:last-child"));
        const legend = workspace.map?.legend || [];
        for (let idx = 0; idx < legendLabels.length; idx += 1) {
          if (legend[idx]) {
            legendLabels[idx].textContent = legend[idx].label || "";
          }
        }

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
          dashboardRoot.dataset.realGisAvailable = String(Boolean(currentDashboardData?.main_civic_workspace?.map?.real_gis_available));
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

      const categorySection = document.querySelector(".startDiscoveryPanel .discoverySection:nth-of-type(1)");
      const hotTopicSection = document.querySelector(".startDiscoveryPanel .discoverySection:nth-of-type(2)");
      const treeChildren = document.querySelector(".treeChildren");
      const timelineCards = document.querySelector(".timelineCards");
      const mapFrame = document.querySelector(".mapFrame");
      const relatedChips = document.querySelector(".relatedChips");

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
        const entity = event.target.closest("[data-map-entity-id]");
        if (entity?.dataset.mapEntityId) {
          applyDashboardInteraction("select_map_entity", entity.dataset.mapEntityId);
        }
      });
      mapFrame?.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        const entity = event.target.closest("[data-map-entity-id]");
        if (entity?.dataset.mapEntityId) {
          event.preventDefault();
          applyDashboardInteraction("select_map_entity", entity.dataset.mapEntityId);
        }
      });
      relatedChips?.addEventListener("click", (event) => {
        const chip = event.target.closest(".topicChip");
        if (chip?.dataset.relatedId) {
          event.preventDefault();
          applyDashboardInteraction("select_related_topic", chip.dataset.relatedId);
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
          const payload = {
            question,
            top_k: topK,
            muni: "ashdod",
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
              ok: response.ok,
              status: data.state?.generation_status || response.status,
              evidence_count: Array.isArray(data.evidence) ? data.evidence.length : 0,
              spatial_representation: data.main_civic_workspace?.map?.spatial_representation
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
</body>
</html>
"""
