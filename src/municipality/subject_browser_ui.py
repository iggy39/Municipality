from __future__ import annotations


def render_subject_browser_page() -> str:
    return """
<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Municipal Subject Browser</title>
  <style>
    :root {
      --ink: #171410;
      --muted: #6d6258;
      --paper: #f7f0e4;
      --paper-strong: #fffaf0;
      --line: #d8cbb9;
      --line-dark: #9b856b;
      --blue: #173d59;
      --blue-soft: #dce9ee;
      --amber: #b36a18;
      --amber-soft: #f3dfbd;
      --green: #1f6d50;
      --red: #8f332c;
      --shadow: 0 18px 48px rgba(51, 39, 25, 0.14);
      --font-title: "Frank Ruhl Libre", "Noto Serif Hebrew", "Times New Roman", serif;
      --font-body: "Noto Sans Hebrew", "Assistant", "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(900px 420px at 18% -120px, rgba(179, 106, 24, 0.18), transparent 70%),
        linear-gradient(90deg, rgba(23, 61, 89, 0.06) 1px, transparent 1px),
        linear-gradient(180deg, #fbf6eb 0%, #efe4d3 100%);
      background-size: auto, 32px 32px, auto;
      font-family: var(--font-body);
      line-height: 1.55;
    }

    button, input, select { font: inherit; }

    button:focus-visible,
    input:focus-visible,
    a:focus-visible {
      outline: 3px solid rgba(179, 106, 24, 0.34);
      outline-offset: 3px;
    }

    .shell {
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 36px;
    }

    .masthead {
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 20px;
      align-items: end;
      padding: 28px;
      border: 1px solid var(--line-dark);
      border-radius: 22px 22px 8px 8px;
      background:
        linear-gradient(135deg, rgba(255, 250, 240, 0.96), rgba(244, 232, 212, 0.95)),
        repeating-linear-gradient(0deg, transparent, transparent 29px, rgba(155, 133, 107, 0.12) 30px);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .masthead::before {
      content: "";
      position: absolute;
      inset-block: 0;
      inset-inline-start: 0;
      width: 13px;
      background: repeating-linear-gradient(180deg, var(--blue), var(--blue) 18px, var(--amber) 18px, var(--amber) 28px);
    }

    .kicker {
      margin: 0 0 6px;
      color: var(--amber);
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      max-width: 920px;
      font-family: var(--font-title);
      font-size: clamp(2.2rem, 5vw, 5rem);
      line-height: 0.94;
      letter-spacing: -0.045em;
      color: var(--blue);
    }

    .intro {
      max-width: 780px;
      margin: 16px 0 0;
      color: #3f382f;
      font-size: 1.08rem;
    }

    .runStamp {
      min-width: 250px;
      padding: 18px;
      border: 1px dashed var(--line-dark);
      border-radius: 18px;
      background: rgba(255,255,255,0.42);
      transform: rotate(-1deg);
    }

    .runStampLabel {
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      font-weight: 800;
    }

    .runStampValue {
      margin: 3px 0 0;
      color: var(--blue);
      font-family: var(--font-title);
      font-size: 2rem;
      font-weight: 900;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
      gap: 18px;
      margin-top: 18px;
      align-items: start;
    }

    .shell.groupsCollapsed .layout {
      grid-template-columns: 58px minmax(0, 1fr);
    }

    .panel {
      border: 1px solid var(--line);
      background: rgba(255, 250, 240, 0.92);
      box-shadow: var(--shadow);
    }

    .groupsPanel {
      position: sticky;
      top: 14px;
      border-radius: 8px 8px 22px 22px;
      overflow: hidden;
    }

    .shell.groupsCollapsed .groupsPanel {
      align-self: stretch;
    }

    .shell.groupsCollapsed .groupsPanel .toolbar,
    .shell.groupsCollapsed .groupsPanel .statusLine,
    .shell.groupsCollapsed .groupsPanel .groupsList,
    .shell.groupsCollapsed .groupsPanel .countPill,
    .shell.groupsCollapsed .groupsPanel h2 {
      display: none;
    }

    .shell.groupsCollapsed .groupsPanel .panelHeader {
      min-height: 640px;
      justify-content: center;
      padding: 12px 8px;
    }

    .panelHeader {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(23, 61, 89, 0.07);
    }

    .panelHeader h2 {
      margin: 0;
      color: var(--blue);
      font-family: var(--font-title);
      font-size: 1.48rem;
    }

    .collapseButton {
      border: 1px solid var(--line-dark);
      border-radius: 999px;
      background: #fffdf6;
      color: var(--blue);
      padding: 7px 10px;
      cursor: pointer;
      font-weight: 950;
      white-space: nowrap;
    }

    .collapseButton:hover { border-color: var(--amber); color: var(--amber); }

    .shell.groupsCollapsed .collapseButton {
      writing-mode: vertical-rl;
      transform: rotate(180deg);
      padding: 12px 8px;
    }

    .countPill {
      display: inline-flex;
      min-width: 36px;
      justify-content: center;
      align-items: center;
      padding: 5px 10px;
      border-radius: 999px;
      background: var(--blue);
      color: #fffaf0;
      font-weight: 900;
    }

    .toolbar {
      display: grid;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }

    .fieldLabel {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 800;
    }

    .selectBox {
      width: 100%;
      border: 1px solid var(--line-dark);
      border-radius: 999px;
      background: #fffdf6;
      color: var(--ink);
      padding: 10px 15px;
    }

    .searchBox {
      width: 100%;
      border: 1px solid var(--line-dark);
      border-radius: 999px;
      background: #fffdf6;
      color: var(--ink);
      padding: 10px 15px;
    }

    .toggleLine {
      display: flex;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 0.92rem;
    }

    .toggleLine input { width: 18px; height: 18px; }

    .groupsList {
      display: grid;
      gap: 8px;
      padding: 14px;
      max-height: calc(100vh - 340px);
      overflow: auto;
    }

    .groupButton {
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px 12px;
      text-align: start;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255, 253, 246, 0.82);
      color: var(--ink);
      padding: 13px 14px;
      cursor: pointer;
      transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
    }

    .groupButton:hover { transform: translateY(-1px); border-color: var(--amber); }
    .groupButton.isSelected { background: var(--blue); color: #fffaf0; border-color: var(--blue); }

    .groupName { font-weight: 900; }
    .groupMeta { color: var(--muted); font-size: 0.87rem; }
    .groupButton.isSelected .groupMeta { color: rgba(255,250,240,0.76); }

    .groupCount {
      grid-row: span 2;
      align-self: center;
      min-width: 46px;
      height: 46px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: var(--amber-soft);
      color: var(--blue);
      font-weight: 950;
    }

    .groupButton.isSelected .groupCount { background: #fffaf0; }

    .detailsPanel {
      min-height: 640px;
      border-radius: 8px 22px 22px 8px;
      overflow: hidden;
    }

    .selectedTitle {
      display: grid;
      gap: 2px;
    }

    .selectedTitle h2 { margin: 0; }
    .selectedTitle p { margin: 0; color: var(--muted); }

    .itemsList {
      padding: 8px;
      overflow-x: auto;
      max-width: 100%;
    }

    .fieldGuide {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 250, 240, 0.66);
    }

    .guideCard {
      border: 1px solid rgba(155, 133, 107, 0.42);
      border-radius: 10px;
      background: rgba(255, 253, 246, 0.82);
      padding: 6px 8px;
    }

    .guideCard strong {
      display: block;
      color: var(--blue);
      font-size: 0.72rem;
      font-weight: 950;
    }

    .guideCard span {
      display: block;
      margin-top: 3px;
      color: #4b4238;
      font-size: 0.74rem;
      line-height: 1.28;
    }

    .subjectTable {
      width: max(1280px, 100%);
      border-collapse: separate;
      border-spacing: 0 5px;
      table-layout: fixed;
    }

    .subjectTable th {
      position: sticky;
      top: 0;
      z-index: 1;
      padding: 5px 6px;
      color: var(--blue);
      background: #f5ead8;
      border-block: 1px solid var(--line);
      font-size: 0.66rem;
      text-align: start;
      white-space: normal;
      line-height: 1.1;
    }

    .subjectTable td {
      vertical-align: top;
      padding: 6px;
      border-block: 1px solid var(--line);
      background: #fffdf6;
      color: var(--ink);
      font-size: 0.73rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }

    .subjectTable tr td:first-child {
      border-inline-start: 6px solid var(--amber);
      border-start-start-radius: 16px;
      border-end-start-radius: 16px;
    }

    .subjectTable tr td:last-child {
      border-inline-end: 1px solid var(--line);
      border-start-end-radius: 16px;
      border-end-end-radius: 16px;
    }

    .detailRow { display: none; }
    .detailRow.isOpen { display: table-row; }

    .summaryRow.isExpanded {
      display: none;
    }

    .detailRow td {
      padding: 0;
      border: 0;
      background: transparent;
    }

    .rowDetailPanel {
      height: var(--expanded-row-height, calc(100vh - 220px));
      min-height: 360px;
      overflow: auto;
      border: 1px solid var(--line-dark);
      border-inline-start: 6px solid var(--blue);
      border-radius: 18px;
      background: #fffdf6;
      box-shadow: inset 0 0 0 1px rgba(255, 250, 240, 0.72);
      padding: 12px;
    }

    .detailHeader {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 10px;
    }

    .detailGrid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }

    .detailBox {
      border: 1px solid rgba(155, 133, 107, 0.36);
      border-radius: 10px;
      background: rgba(247, 240, 228, 0.5);
      padding: 8px;
      min-height: 62px;
    }

    .detailBox.wide { grid-column: span 2; }
    .detailBox.full { grid-column: 1 / -1; }

    .expandButton {
      margin-top: 6px;
      border: 1px solid var(--line-dark);
      border-radius: 999px;
      background: var(--blue);
      color: #fffaf0;
      padding: 3px 8px;
      cursor: pointer;
      font-size: 0.68rem;
      font-weight: 950;
    }

    .expandButton:hover { background: var(--amber); border-color: var(--amber); }

    .caseCell { width: 145px; }
    .shortCell { width: 74px; }
    .mediumCell { width: 105px; }
    .wideCell { width: 150px; }
    .sourceCell { width: 190px; }

    .cellLabel {
      display: none;
      color: var(--muted);
      font-size: 0.75rem;
      font-weight: 850;
    }

    .cellValue {
      display: block;
      font-weight: 800;
      line-height: 1.22;
    }

    .caseTitle {
      display: block;
      color: var(--blue);
      font-family: var(--font-title);
      font-size: 0.9rem;
      font-weight: 950;
      line-height: 1.08;
    }

    .caseSummary {
      display: block;
      margin-top: 4px;
      color: #3b342d;
      font-size: 0.72rem;
      line-height: 1.25;
      font-weight: 500;
    }

    .tableTextBox {
      max-height: 92px;
      overflow: auto;
      border: 1px solid rgba(155, 133, 107, 0.34);
      border-radius: 8px;
      background: #fffaf0;
      padding: 5px;
      white-space: pre-wrap;
    }

    .hebrewText {
      direction: rtl;
      text-align: right;
    }

    .subjectCard {
      position: relative;
      display: grid;
      gap: 11px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fffdf6;
      padding: 16px;
      overflow: hidden;
    }

    .subjectCard::before {
      content: "";
      position: absolute;
      inset-inline-start: 0;
      inset-block: 0;
      width: 6px;
      background: var(--amber);
      opacity: 0.78;
    }

    .cardTop {
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 14px;
    }

    .subjectObject {
      margin: 0;
      color: var(--blue);
      font-family: var(--font-title);
      font-size: clamp(1.28rem, 2vw, 1.8rem);
      line-height: 1.08;
    }

    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      justify-content: flex-end;
    }

    .badge {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 6px;
      background: var(--paper);
      color: var(--muted);
      font-size: 0.64rem;
      font-weight: 850;
      white-space: nowrap;
    }

    .badge.decision { background: #e7f2ec; color: var(--green); border-color: #a7cdbb; }
    .badge.warning { background: #fff1cf; color: #8a4a04; border-color: #e4bd68; }

    .summary {
      margin: 0;
      color: #3b342d;
    }

    .auditGrid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .auditBox {
      border: 1px solid rgba(23, 61, 89, 0.18);
      border-radius: 14px;
      background: rgba(220, 233, 238, 0.32);
      padding: 11px 12px;
    }

    .auditBox.fullWidth { grid-column: 1 / -1; }

    .auditTitle {
      margin: 0 0 5px;
      color: var(--blue);
      font-size: 0.82rem;
      font-weight: 950;
    }

    .auditText {
      margin: 0;
      color: #352f28;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .fullSourceText {
      max-height: 220px;
      overflow: auto;
      border: 1px solid rgba(155, 133, 107, 0.38);
      border-radius: 12px;
      background: #fffaf0;
      padding: 10px;
      direction: rtl;
      text-align: right;
    }

    .linkedDetails {
      display: grid;
      gap: 8px;
      margin-top: 8px;
    }

    .linkedDetailCard {
      border: 1px dashed var(--line-dark);
      border-radius: 12px;
      background: rgba(255, 250, 240, 0.76);
      padding: 10px;
    }

    .metaGrid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 9px;
    }

    .metaBox {
      border: 1px solid rgba(155, 133, 107, 0.45);
      border-radius: 12px;
      padding: 9px 10px;
      background: rgba(247, 240, 228, 0.62);
    }

    .metaLabel {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 850;
    }

    .metaValue {
      display: block;
      margin-top: 2px;
      color: var(--ink);
      font-weight: 800;
      overflow-wrap: anywhere;
    }

    .sourceLink {
      justify-self: start;
      color: var(--blue);
      text-decoration: none;
      border-bottom: 1px solid var(--amber);
      font-weight: 900;
    }

    .sourceLink:hover { color: var(--amber); }

    .statusLine {
      min-height: 36px;
      padding: 12px 16px;
      color: var(--muted);
    }

    .emptyState {
      margin: 18px;
      padding: 32px;
      border: 1px dashed var(--line-dark);
      border-radius: 18px;
      color: var(--muted);
      text-align: center;
    }

    .errorText { color: var(--red); font-weight: 900; }

    @media (max-width: 960px) {
      .masthead { grid-template-columns: 1fr; }
      .runStamp { transform: none; }
      .layout { grid-template-columns: 1fr; }
      .shell.groupsCollapsed .layout { grid-template-columns: 1fr; }
      .groupsPanel { position: static; }
      .shell.groupsCollapsed .groupsPanel .panelHeader { min-height: 0; }
      .groupsList { max-height: none; }
      .detailsPanel { min-height: 0; }
      .fieldGuide { grid-template-columns: 1fr; }
      .detailGrid { grid-template-columns: 1fr; }
      .detailBox.wide { grid-column: auto; }
      .metaGrid { grid-template-columns: 1fr; }
      .auditGrid { grid-template-columns: 1fr; }
      .cardTop { display: grid; }
      .badges { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <main class="shell" data-subject-browser data-groups-endpoint="/api/topic-subjects/groups" data-items-endpoint="/api/topic-subjects">
    <header class="masthead">
      <section>
        <p class="kicker" id="municipalityKicker">Ashdod protocol subjects</p>
        <h1>Municipal Subject Browser</h1>
        <p class="intro">A product view of municipal actions and their subject matter extracted from protocols and attachments. UI labels are in English; extracted Hebrew source values are preserved for audit.</p>
      </section>
      <aside class="runStamp" aria-label="Processing run information">
        <p class="runStampLabel">Processing run</p>
        <p class="runStampValue" id="runId">Loading</p>
        <p class="runStampLabel" id="runMeta">Waiting for data</p>
      </aside>
    </header>

    <section class="layout">
      <aside class="panel groupsPanel" aria-label="Subject groups">
        <div class="panelHeader">
          <h2>Groups</h2>
          <div class="badges">
            <span class="countPill" id="groupCount">0</span>
            <button class="collapseButton" id="groupsCollapseButton" type="button" aria-expanded="true" aria-controls="groupsList">Collapse</button>
          </div>
        </div>
        <div class="toolbar">
          <label class="fieldLabel">Municipality
            <select class="selectBox" id="municipalitySelect" aria-label="Municipality">
              <option value="ashdod" selected>Ashdod</option>
              <option value="tel_aviv">Tel Aviv</option>
            </select>
          </label>
          <input class="searchBox" id="groupSearch" type="search" placeholder="Search group or stored label" aria-label="Search groups" />
          <label class="toggleLine"><input id="includeLinkedDetails" type="checkbox" /> Include linked detail rows</label>
        </div>
        <div class="statusLine" id="groupsStatus">Loading groups...</div>
        <div class="groupsList" id="groupsList"></div>
      </aside>

      <section class="panel detailsPanel" aria-label="Subject items">
        <div class="panelHeader">
          <div class="selectedTitle">
            <h2 id="selectedGroupTitle">Select a group</h2>
            <p id="selectedGroupMeta">Items will appear here after selection.</p>
          </div>
          <span class="countPill" id="itemCount">0</span>
        </div>
        <div class="fieldGuide" aria-label="Field guide">
          <div class="guideCard"><strong>Source topic</strong><span>Upstream retrieval/topic grouping for the source chunk. It is not the action or the final subject matter and may be inaccurate.</span></div>
          <div class="guideCard"><strong>Correct action (judge)</strong><span>My judged procedural action after checking the source, such as inquiry, response to inquiry, approval, report, or instruction.</span></div>
          <div class="guideCard"><strong>Correct subject matter</strong><span>The concrete matter the action is about, such as a deficit, appointment, report, plan, budget, request, or protocol.</span></div>
          <div class="guideCard"><strong>Raw model output</strong><span>The model answer before judging/validation. It can differ from the correct action and subject matter.</span></div>
        </div>
        <div class="statusLine" id="itemsStatus">No group selected.</div>
        <div class="itemsList" id="itemsList"></div>
      </section>
    </section>
  </main>

  <script>
    (() => {
      const root = document.querySelector("[data-subject-browser]");
      if (!root) return;

      const groupsEndpoint = root.dataset.groupsEndpoint || "/api/topic-subjects/groups";
      const itemsEndpoint = root.dataset.itemsEndpoint || "/api/topic-subjects";
      const runId = document.getElementById("runId");
      const runMeta = document.getElementById("runMeta");
      const groupCount = document.getElementById("groupCount");
      const itemCount = document.getElementById("itemCount");
      const groupsStatus = document.getElementById("groupsStatus");
      const itemsStatus = document.getElementById("itemsStatus");
      const groupsList = document.getElementById("groupsList");
      const itemsList = document.getElementById("itemsList");
      const selectedGroupTitle = document.getElementById("selectedGroupTitle");
      const selectedGroupMeta = document.getElementById("selectedGroupMeta");
      const groupSearch = document.getElementById("groupSearch");
      const includeLinkedDetails = document.getElementById("includeLinkedDetails");
      const municipalitySelect = document.getElementById("municipalitySelect");
      const municipalityKicker = document.getElementById("municipalityKicker");
      const groupsCollapseButton = document.getElementById("groupsCollapseButton");

      let groups = [];
      let selectedGroup = null;

      const setText = (node, value) => { if (node) node.textContent = value == null ? "" : String(value); };
      const clear = (node) => { if (node) node.replaceChildren(); };
      const hebrewList = (values) => values.filter(Boolean).join(" · ");
      const municipalityLabel = () => municipalitySelect?.selectedOptions?.[0]?.textContent || "Ashdod";
      const selectedMunicipality = () => municipalitySelect?.value || "ashdod";
      const unknown = (value) => value == null || value === "" ? "Unknown" : String(value);

      const setGroupsCollapsed = (collapsed) => {
        root.classList.toggle("groupsCollapsed", collapsed);
        if (groupsCollapseButton) {
          groupsCollapseButton.textContent = collapsed ? "Show groups" : "Collapse";
          groupsCollapseButton.setAttribute("aria-expanded", collapsed ? "false" : "true");
        }
      };

      const apiUrl = (base, params = {}) => {
        const url = new URL(base, window.location.origin);
        Object.entries(params).forEach(([key, value]) => {
          if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
        });
        return url.toString();
      };

      const fetchJson = async (url) => {
        const response = await fetch(url, { headers: { "Accept": "application/json" } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      };

      const qualityLabel = (flag) => {
        if (flag === "topic_suspect") return "Source topic may be inaccurate";
        if (flag === "decision_rejected_subject_kept") return "Rejected as decision, kept as action";
        return flag;
      };

      const renderGroups = () => {
        clear(groupsList);
        const query = (groupSearch?.value || "").trim().toLowerCase();
        const visibleGroups = groups.filter((group) => {
          if (!query) return true;
          const stored = Object.keys(group.stored_labels || {}).join(" ");
          return `${group.label_he} ${stored}`.toLowerCase().includes(query);
        });
        setText(groupCount, visibleGroups.length);
        if (!visibleGroups.length) {
          const empty = document.createElement("div");
          empty.className = "emptyState";
          empty.textContent = "No matching groups found.";
          groupsList.appendChild(empty);
          return;
        }
        visibleGroups.forEach((group) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "groupButton" + (selectedGroup?.group_key === group.group_key ? " isSelected" : "");
          button.dataset.groupKey = group.group_key;

          const name = document.createElement("span");
          name.className = "groupName";
          name.textContent = group.label_he;

          const meta = document.createElement("span");
          meta.className = "groupMeta";
          meta.textContent = hebrewList([
            `${group.count} items`,
            group.decision_count ? `${group.decision_count} decisions` : "",
            group.topic_suspect_count ? `${group.topic_suspect_count} source-topic review` : "",
          ]);

          const count = document.createElement("span");
          count.className = "groupCount";
          count.textContent = group.count;

          button.append(name, count, meta);
          button.addEventListener("click", () => selectGroup(group));
          groupsList.appendChild(button);
        });
      };

      const renderBadges = (item) => {
        const badges = document.createElement("div");
        badges.className = "badges";
        if (item.decision?.is_decision) {
          const badge = document.createElement("span");
          badge.className = "badge decision";
          badge.textContent = "Decision";
          badges.appendChild(badge);
        }
        (item.quality?.flags || []).forEach((flag) => {
          const badge = document.createElement("span");
          badge.className = "badge warning";
          badge.textContent = qualityLabel(flag);
          badges.appendChild(badge);
        });
        const storedBadge = document.createElement("span");
        storedBadge.className = "badge";
        storedBadge.textContent = item.stored?.label_he || "";
        badges.appendChild(storedBadge);
        return badges;
      };

      const tableTextBox = (text, extraClass = "") => {
        const box = document.createElement("div");
        box.className = `tableTextBox ${extraClass}`.trim();
        box.textContent = text || "Unknown";
        return box;
      };

      const cell = (label, content, className = "mediumCell") => {
        const td = document.createElement("td");
        td.className = className;
        const labelNode = document.createElement("span");
        labelNode.className = "cellLabel";
        labelNode.textContent = label;
        td.appendChild(labelNode);
        if (content instanceof Node) {
          td.appendChild(content);
        } else {
          const valueNode = document.createElement("span");
          valueNode.className = "cellValue";
          valueNode.textContent = unknown(content);
          td.appendChild(valueNode);
        }
        return td;
      };

      const pageLabel = (source) => source?.page_start ? `${source.page_start}${source.page_end && source.page_end !== source.page_start ? `-${source.page_end}` : ""}` : "Unknown";

      const renderLinkedDetails = (item) => {
        const linkedList = document.createElement("div");
        linkedList.className = "linkedDetails";
        const details = item.linked_details || [];
        if (!details.length) {
          const empty = document.createElement("p");
          empty.className = "auditText";
          empty.textContent = "No linked detail rows for this action.";
          linkedList.appendChild(empty);
        } else {
          details.forEach((detail, index) => {
            const detailCard = document.createElement("div");
            detailCard.className = "linkedDetailCard";
            const detailTitle = document.createElement("h5");
            detailTitle.className = "auditTitle";
            detailTitle.textContent = `Detail ${index + 1} · ${detail.row_role || detail.status || "context"}`;
            const detailGroundTruth = document.createElement("p");
            detailGroundTruth.className = "auditText";
            detailGroundTruth.textContent = detail.agent_subject_prediction_en || "No action/subject-matter prediction.";
            const detailSource = document.createElement("p");
            detailSource.className = "auditText fullSourceText";
            detailSource.textContent = detail.full_source_text_he || "No source text.";
            detailCard.append(detailTitle, detailGroundTruth, detailSource);
            linkedList.appendChild(detailCard);
          });
        }
        return linkedList;
      };

      const detailBox = (label, content, extraClass = "") => {
        const box = document.createElement("section");
        box.className = `detailBox ${extraClass}`.trim();
        const labelNode = document.createElement("span");
        labelNode.className = "metaLabel";
        labelNode.textContent = label;
        box.appendChild(labelNode);
        if (content instanceof Node) {
          box.appendChild(content);
        } else {
          const valueNode = document.createElement("span");
          valueNode.className = "metaValue";
          valueNode.textContent = unknown(content);
          box.appendChild(valueNode);
        }
        return box;
      };

      const renderDetailPanel = (item, titleText) => {
        const panel = document.createElement("div");
        panel.className = "rowDetailPanel";
        const header = document.createElement("div");
        header.className = "detailHeader";
        const title = document.createElement("span");
        title.className = "caseTitle";
        title.textContent = titleText;
        header.append(title, renderBadges(item));

        const grid = document.createElement("div");
        grid.className = "detailGrid";
        grid.append(
          detailBox("Judged action + subject matter", item.audit?.agent_subject_prediction_en, "wide"),
          detailBox("Source topic", item.topic?.label_he),
          detailBox("Correct action: root", item.audit?.judge_prediction?.root_label_he),
          detailBox("Correct action: child", item.audit?.judge_prediction?.child_label_he || "None"),
          detailBox("Correct subject matter", item.audit?.judge_prediction?.object_he),
          detailBox("Raw model action: root", item.audit?.model_prediction?.root_label_he),
          detailBox("Raw model action: child", item.audit?.model_prediction?.child_label_he || "None"),
          detailBox("Raw model subject matter", item.audit?.model_prediction?.object_he),
          detailBox("Raw model details", item.audit?.model_prediction?.details_he, "wide"),
          detailBox("Document", item.source?.document_title || item.source?.title, "wide"),
          detailBox("Document full path", item.source?.document_full_path || item.source?.document_file_path, "wide"),
          detailBox("Page", pageLabel(item.source)),
          detailBox("Full source text", tableTextBox(item.audit?.full_source_text_he || "No source text available.", "hebrewText"), "full"),
          detailBox("Linked detail rows", renderLinkedDetails(item), "full"),
        );
        panel.append(header, grid);
        return panel;
      };

      const setExpandedRowHeight = () => {
        const panel = itemsList.querySelector(".detailRow.isOpen .rowDetailPanel");
        const anchor = panel || itemsList.querySelector(".subjectTable");
        if (!anchor) return;
        const rect = anchor.getBoundingClientRect();
        const availableHeight = window.innerHeight - Math.max(0, rect.top) - 12;
        const height = Math.max(360, Math.min(window.innerHeight - 72, availableHeight));
        root.style.setProperty("--expanded-row-height", `${Math.floor(height)}px`);
      };

      const closeOpenRows = (table, exceptDetailRow = null) => {
        table.querySelectorAll(".detailRow.isOpen").forEach((row) => {
          if (row === exceptDetailRow) return;
          row.classList.remove("isOpen");
          const summaryRow = row.previousElementSibling;
          summaryRow?.classList.remove("isExpanded");
          const button = summaryRow?.querySelector(".expandButton");
          if (button) {
            button.textContent = "Show row";
            button.setAttribute("aria-expanded", "false");
          }
        });
      };

      const renderItem = (item, columnCount) => {
        const row = document.createElement("tr");
        row.className = "summaryRow";
        const caseBox = document.createElement("div");
        const title = document.createElement("span");
        title.className = "caseTitle";
        title.textContent = item.audit?.agent_subject_prediction_en || item.subject?.object_he || item.subject?.summary_he || item.stored?.label_he || "Untitled subject";
        const summary = document.createElement("span");
        summary.className = "caseSummary";
        summary.textContent = item.decision?.summary_he || item.subject?.summary_he || item.subject?.what_text_is_about_he || "No summary available.";
        const expandButton = document.createElement("button");
        expandButton.type = "button";
        expandButton.className = "expandButton";
        expandButton.textContent = "Show row";
        expandButton.setAttribute("aria-expanded", "false");
        caseBox.append(title, summary, renderBadges(item), expandButton);

        const docBox = document.createElement("div");
        const docTitle = document.createElement("span");
        docTitle.className = "cellValue";
        docTitle.textContent = unknown(item.source?.document_title || item.source?.title);
        docBox.appendChild(docTitle);
        if (item.source?.document_version_id) {
          const link = document.createElement("a");
          link.className = "sourceLink";
          link.href = `/document-versions/${item.source.document_version_id}/source.pdf`;
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = "Open source PDF";
          docBox.appendChild(link);
        }

        row.append(
          cell("Case", caseBox, "caseCell"),
          cell("Judged action + subject matter", item.audit?.agent_subject_prediction_en, "mediumCell"),
          cell("Source topic", item.topic?.label_he, "mediumCell"),
          cell("Correct action: root", item.audit?.judge_prediction?.root_label_he, "shortCell"),
          cell("Correct action: child", item.audit?.judge_prediction?.child_label_he || "None", "shortCell"),
          cell("Correct subject matter", item.audit?.judge_prediction?.object_he, "mediumCell"),
          cell("Raw model action: root", item.audit?.model_prediction?.root_label_he, "shortCell"),
          cell("Raw model action: child", item.audit?.model_prediction?.child_label_he || "None", "shortCell"),
          cell("Raw model subject matter", item.audit?.model_prediction?.object_he, "mediumCell"),
          cell("Raw model details", item.audit?.model_prediction?.details_he, "wideCell"),
          cell("Document", docBox, "wideCell"),
          cell("Document full path", item.source?.document_full_path || item.source?.document_file_path, "wideCell"),
          cell("Page", pageLabel(item.source), "shortCell"),
          cell("Full source text", tableTextBox(item.audit?.full_source_text_he || "No source text available.", "hebrewText"), "sourceCell"),
          cell("Linked detail rows", renderLinkedDetails(item), "wideCell"),
        );
        const detailRow = document.createElement("tr");
        detailRow.className = "detailRow";
        const detailCell = document.createElement("td");
        detailCell.colSpan = columnCount;
        detailCell.appendChild(renderDetailPanel(item, title.textContent));
        detailRow.appendChild(detailCell);

        expandButton.addEventListener("click", () => {
          const table = row.closest("table");
          const willOpen = !detailRow.classList.contains("isOpen");
          if (table) closeOpenRows(table, willOpen ? detailRow : null);
          detailRow.classList.toggle("isOpen", willOpen);
          row.classList.toggle("isExpanded", willOpen);
          expandButton.textContent = willOpen ? "Hide row" : "Show row";
          expandButton.setAttribute("aria-expanded", willOpen ? "true" : "false");
          if (willOpen) {
            detailRow.scrollIntoView({ block: "start" });
            requestAnimationFrame(setExpandedRowHeight);
          }
        });

        return [row, detailRow];
      };

      const renderItemsTable = (items) => {
        const table = document.createElement("table");
        table.className = "subjectTable";
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        const headers = [
          "Case",
          "Judged action + subject matter",
          "Source topic",
          "Correct action: root",
          "Correct action: child",
          "Correct subject matter",
          "Raw model action: root",
          "Raw model action: child",
          "Raw model subject matter",
          "Raw model details",
          "Document",
          "Document full path",
          "Page",
          "Full source text",
          "Linked detail rows",
        ];
        headers.forEach((label) => {
          const th = document.createElement("th");
          th.textContent = label;
          headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        const tbody = document.createElement("tbody");
        items.forEach((item) => tbody.append(...renderItem(item, headers.length)));
        table.append(thead, tbody);
        setTimeout(setExpandedRowHeight, 0);
        return table;
      };

      const selectGroup = async (group) => {
        selectedGroup = group;
        renderGroups();
        clear(itemsList);
        setText(itemCount, group.count);
        setText(selectedGroupTitle, group.label_he);
        setText(selectedGroupMeta, hebrewList([
          `${group.count} items`,
          group.decision_count ? `${group.decision_count} decisions` : "No decisions",
          group.topic_suspect_count ? `${group.topic_suspect_count} source topics may be inaccurate` : "No source-topic review flags in displayed items",
        ]));
        setText(itemsStatus, "Loading items...");
        try {
          const payload = await fetchJson(apiUrl(itemsEndpoint, {
            muni: selectedMunicipality(),
            display_group: group.group_key,
            include_linked_details: includeLinkedDetails.checked ? "true" : "false",
            limit: 200,
          }));
          setText(itemCount, payload.total_count || payload.count || 0);
          setText(itemsStatus, payload.total_count ? "" : "No items to display.");
          if ((payload.items || []).length) {
            itemsList.appendChild(renderItemsTable(payload.items || []));
          }
        } catch (error) {
          setText(itemsStatus, "Error loading items.");
          itemsStatus.classList.add("errorText");
        }
      };

      const loadGroups = async () => {
        groupsStatus.classList.remove("errorText");
        itemsStatus.classList.remove("errorText");
        setText(groupsStatus, "Loading groups...");
        setText(municipalityKicker, `${municipalityLabel()} protocol subjects`);
        clear(groupsList);
        clear(itemsList);
        selectedGroup = null;
        try {
          const payload = await fetchJson(apiUrl(groupsEndpoint, { muni: selectedMunicipality(), include_linked_details: includeLinkedDetails.checked ? "true" : "false" }));
          groups = payload.groups || [];
          setText(runId, payload.run ? `#${payload.run.id}` : "Unknown");
          setText(runMeta, payload.run ? `${payload.count} anchor items · ${payload.run.model_name}` : "");
          setText(groupsStatus, groups.length ? "" : "No groups to display.");
          selectedGroup = groups[0] || null;
          renderGroups();
          if (selectedGroup) await selectGroup(selectedGroup);
        } catch (error) {
          setText(groupsStatus, `No subject run found for ${municipalityLabel()}.`);
          groupsStatus.classList.add("errorText");
          setText(runId, "Error");
          setText(runMeta, "Run subject extraction first for this municipality.");
          setText(groupCount, "0");
          setText(itemCount, "0");
          setText(selectedGroupTitle, "Select a group");
          setText(selectedGroupMeta, "Items will appear here after selection.");
          setText(itemsStatus, "No group selected.");
        }
      };

      groupSearch?.addEventListener("input", renderGroups);
      includeLinkedDetails?.addEventListener("change", loadGroups);
      municipalitySelect?.addEventListener("change", loadGroups);
      groupsCollapseButton?.addEventListener("click", () => setGroupsCollapsed(!root.classList.contains("groupsCollapsed")));
      window.addEventListener("resize", setExpandedRowHeight);
      loadGroups();
    })();
  </script>
</body>
</html>
"""
