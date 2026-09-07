/**
 * Statistics Outlier Cleaner — sidebar panel
 *
 * Vanilla web component, no build step required.
 *
 * The statistic and date-range fields are HA's own components — ha-entity-picker
 * and ha-date-range-picker. Neither is loaded by HA for a custom panel, but both
 * are reachable: loading any Lovelace card whose module (or config editor)
 * imports them registers them as an import side effect. See _ensureEntityPicker()
 * and _ensureDateRangePicker(). That depends on HA frontend internals, so each
 * field has a plain fallback that renders on first paint and stays if the load
 * never completes.
 */

const DOMAIN = "statistics_outlier_cleaner";

// Any Lovelace card whose module imports ha-date-range-picker will do; the
// energy date selection card is the shortest path to it.
const DATE_PICKER_HOST_CARD = "energy-date-selection";
const DATE_PICKER_TAG = "ha-date-range-picker";
const DATE_PICKER_LOAD_TIMEOUT_MS = 15_000;
// Retries are cheap; a frontend that has not produced the element after this
// many state updates is not going to.
const DATE_PICKER_MAX_ATTEMPTS = 20;

// ha-entity-picker is imported by the entities card's config editor, so building
// that card and asking it for its config element is enough to register it.
const ENTITY_PICKER_HOST_CARD = "entities";
const ENTITY_PICKER_TAG = "ha-entity-picker";
const ENTITY_PICKER_MAX_ATTEMPTS = 20;

// A statistic id is an entity id when it has a domain.object_id shape. External
// long-term statistics use a "source:object_id" form and have no entity, so they
// cannot appear in an entity picker.
const ENTITY_ID_RE = /^[a-z0-9_]+\.[a-z0-9_]+$/;

const DEFAULT_RANGE_DAYS = 30;

/** Local midnight, `offsetDays` from today. */
function startOfLocalDay(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  d.setHours(0, 0, 0, 0);
  return d;
}

/** The last representable instant of the given day, in local time. */
function endOfLocalDay(date) {
  const d = new Date(date);
  d.setHours(23, 59, 59, 999);
  return d;
}

const METHOD_HELP = {
  mad: {
    title: "MAD — Median Absolute Deviation",
    safe: true,
    summary: `Looks at each reading in context — comparing it to other readings at the same time
      of day. Only flags values that are unusually large compared to typical variation for that hour.
      Safe for automations: if your sensor has no spikes, nothing will be flagged.`,
    example: {
      scenario: "Solar panel: typical noon output ~1.5 kWh, day-to-day variation ±0.3 kWh.",
      result: "At factor 6, a reading must deviate more than ~1.8 kWh from the usual noon value to be flagged.",
      cases: [
        { label: "0.9 kWh on a cloudy day", outcome: "untouched — normal variation", ok: true },
        { label: "500 kWh data glitch", outcome: "flagged at any factor", ok: false },
      ],
    },
    paramName: "MAD factor",
    paramHint: "Range 2–20 · higher = stricter = fewer flags",
    paramRows: [
      { value: "3.5", label: "Sensitive",     desc: "Catches most real outliers; may also flag some normal variation" },
      { value: "6",   label: "Recommended",   desc: "Good balance for most sensors", recommended: true },
      { value: "10",  label: "Conservative",  desc: "Only flags obvious spikes" },
      { value: "20",  label: "Extreme only",  desc: "Only data corruption or severe glitches" },
    ],
    formula: "0.6745 × |change − median| / MAD ≥ factor",
  },
  absolute: {
    title: "Absolute threshold",
    safe: true,
    summary: `Flags any reading where the recorded change is equal to or larger than the number you
      set. Simple and predictable — set the threshold just above the maximum your sensor can
      physically produce in one period.`,
    example: {
      cases: [
        { label: "Solar inverter rated 8 kW → threshold 10",   outcome: "any hour above 10 kWh flagged", ok: false },
        { label: "Gas meter max flow 3 m³/h → threshold 3",    outcome: "any hour above 3 m³ flagged",   ok: false },
        { label: "Normal peak of 7 kWh on a sunny afternoon",  outcome: "untouched",                     ok: true  },
      ],
    },
    paramName: "Threshold",
    paramHint: "Any |change| equal to or above this value is flagged",
    paramRows: [
      { value: "—", label: "Your sensor's physical maximum", desc: "Find the rated max output per period and add a small safety margin (e.g. ×1.2)" },
    ],
    formula: "|change| ≥ threshold",
  },
  top_n: {
    title: "Top N",
    safe: false,
    warning: `This method always returns N results — even if your data has no real outliers. If your
      sensor is perfectly healthy, Top N will still flag the N largest normal readings and overwrite
      them if you apply a fix. Always review results carefully before applying. This method is
      blocked in the <code>clean_outliers</code> automation service for this reason.`,
    summary: `Returns a list of your N biggest recorded changes, regardless of whether any of them
      are genuine outliers. Useful for one-off manual inspection — the same view as the built-in
      Developer Tools → Statistics dialog.`,
    example: {
      cases: [
        { label: "Perfectly normal sensor, N = 10",    outcome: "10 normal readings returned and flagged anyway", ok: false },
        { label: "One genuine spike + N = 10",         outcome: "the spike plus 9 normal readings returned",     ok: false },
      ],
    },
    paramName: "N",
    paramHint: "How many of the largest changes to return",
    paramRows: [
      { value: "10", label: "Good starting point", desc: "Returns the 10 largest changes for manual review" },
    ],
    formula: "Always returns the N rows with the largest |change| value",
  },
};

const WS = {
  list_sum_statistics: `${DOMAIN}/list_sum_statistics`,
  fetch_outliers: `${DOMAIN}/fetch_outliers`,
  apply_fix: `${DOMAIN}/apply_fix`,
  list_fixes: `${DOMAIN}/list_fixes`,
  restore_fix: `${DOMAIN}/restore_fix`,
};

const STYLES = `
  /* ha-panel-custom gives us display:block and safe-area padding but no height,
     so height:100% here would resolve against an auto-height parent and
     collapse. The document would scroll instead of us, and a sticky toolbar
     would pin to that scrollport rather than the viewport. Take a definite
     height from the viewport, less the insets the container already pads for. */
  :host {
    --soc-toolbar-height: 56px;
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: calc(
      100dvh - var(--safe-area-inset-top, 0px) - var(--safe-area-inset-bottom, 0px)
    );
    overflow: hidden;
    box-sizing: border-box;
    font-family: var(--paper-font-body1_-_font-family, inherit);
    color: var(--primary-text-color);
  }
  /* The toolbar is a plain flex row pinned by layout rather than by
     positioning, and only this pane scrolls. */
  .panel-content {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    box-sizing: border-box;
    padding: 16px;
    max-width: 1000px;
  }
  h2 { margin: 0 0 16px; font-size: 1.4rem; font-weight: 500; }
  h3 { margin: 0 0 12px; font-size: 1.1rem; font-weight: 500; }
  /* A custom panel registered with embed_iframe: false owns the whole view —
     Home Assistant renders no header of its own. Without a way to reach the
     sidebar the panel is a dead end on mobile, where the sidebar is hidden.
     Sticky so it stays reachable however far down the page you are. */
  .app-toolbar {
    flex: 0 0 auto;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 4px;
    height: var(--soc-toolbar-height);
    padding: 0 12px;
    box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-background-color, #fafafa));
    color: var(--app-header-text-color, var(--primary-text-color));
  }
  .app-toolbar .app-title { font-size: 1.15rem; font-weight: 500; }
  .menu-btn {
    background: transparent;
    color: inherit;
    height: 40px;
    width: 40px;
    padding: 0;
    flex: 0 0 auto;
    justify-content: center;
    border-radius: 50%;
  }
  .menu-btn:hover { background: rgba(var(--rgb-primary-text-color, 0,0,0), 0.08); }
  .card {
    background: var(--card-background-color, #fff);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.1));
  }
  .form-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
    margin-bottom: 12px;
  }
  .form-group { display: flex; flex-direction: column; gap: 4px; }
  .form-group label {
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  select, input[type=text], input[type=date], input[type=number] {
    padding: 8px 10px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 4px;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 0.9rem;
    height: 40px;
    box-sizing: border-box;
  }
  select { min-width: 160px; }
  input[type=number] { width: 100px; }
  input[type=date] { width: 160px; }
  .stat-field {
    flex: 1;
    min-width: 280px;
  }
  .stat-field input[type=text] { width: 100%; box-sizing: border-box; }
  .stat-field ha-entity-picker { display: block; width: 100%; }
  .stat-recents {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 6px;
  }
  button {
    padding: 0 16px;
    height: 36px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.875rem;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  button.primary { background: var(--primary-color, #03a9f4); color: #fff; }
  button.danger  { background: var(--error-color, #db4437); color: #fff; }
  button.secondary {
    background: transparent;
    color: var(--primary-color, #03a9f4);
    border: 1px solid var(--primary-color, #03a9f4);
  }
  button.text-btn {
    background: transparent;
    color: var(--primary-color, #03a9f4);
    padding: 0 8px;
    height: 28px;
    font-size: 0.8rem;
  }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .hidden { display: none !important; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
  /* .panel-content is the scrollport, so the toolbar is already excluded. */
  th { font-weight: 500; background: var(--secondary-background-color, #f5f5f5); position: sticky; top: 0; z-index: 1; }
  tr:hover td { background: rgba(var(--rgb-primary-color, 3,169,244), 0.05); }
  tr.selected td { background: rgba(var(--rgb-primary-color, 3,169,244), 0.1); }
  td.change-cell { font-family: monospace; }
  .status { padding: 10px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 0.9rem; border-left: 3px solid; }
  .status.info    { background: rgba(var(--rgb-primary-color, 3,169,244), 0.1); color: var(--primary-text-color); border-left-color: var(--primary-color, #03a9f4); }
  .status.success { background: rgba(var(--rgb-success-color, 76,175,80), 0.1); color: var(--primary-text-color); border-left-color: var(--success-color, #4caf50); }
  .status.error   { background: rgba(var(--rgb-error-color, 219,68,55), 0.1); color: var(--primary-text-color); border-left-color: var(--error-color, #db4437); }
  .meta { font-size: 0.8rem; color: var(--secondary-text-color); margin-bottom: 10px; }
  .toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
  .selection-label { font-size: 0.85rem; color: var(--secondary-text-color); }
  .fix-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .fix-controls label { font-size: 0.8rem; color: var(--secondary-text-color); }
  .dry-run-row { display: flex; align-items: center; gap: 6px; font-size: 0.875rem; cursor: pointer; }
  input[type=checkbox] { width: 16px; height: 16px; cursor: pointer; }
  .fix-id-chip {
    font-family: monospace;
    font-size: 0.75rem;
    background: var(--secondary-background-color, #f5f5f5);
    padding: 2px 6px;
    border-radius: 3px;
  }
  .method-help {
    margin-top: 4px;
    margin-bottom: 12px;
    padding: 12px 14px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-left: 3px solid var(--primary-color, #03a9f4);
    border-radius: 4px;
    font-size: 0.85rem;
    line-height: 1.6;
  }
  .method-help.warn { border-left-color: var(--warning-color, #f59e0b); }
  .mh-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .mh-title { font-size: 0.875rem; font-weight: 600; }
  .mh-summary { margin: 0 0 0; color: var(--primary-text-color); }
  .mh-warning {
    margin-top: 10px;
    padding: 8px 10px;
    background: rgba(var(--rgb-warning-color, 255,152,0), 0.1);
    border: 1px solid var(--warning-color, #ff9800);
    border-radius: 4px;
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .mh-section {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--divider-color, #e0e0e0);
  }
  .mh-label {
    display: block;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--secondary-text-color);
    margin-bottom: 6px;
  }
  .mh-param-hint {
    font-size: 0.7rem;
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
  }
  .mh-scenario { margin: 0 0 6px; color: var(--secondary-text-color); font-style: italic; font-size: 0.8rem; }
  .mh-cases { display: flex; flex-direction: column; gap: 4px; }
  .mh-case { display: flex; align-items: baseline; gap: 6px; font-size: 0.85rem; }
  .mh-case-icon { font-weight: 700; flex-shrink: 0; width: 14px; font-size: 0.8rem; }
  .mh-case.ok  .mh-case-icon { color: var(--success-color, #4caf50); }
  .mh-case.bad .mh-case-icon { color: var(--error-color, #db4437); }
  .mh-case-note { margin: 6px 0 0; font-size: 0.8rem; color: var(--secondary-text-color); }
  .mh-param-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-top: 2px; }
  .mh-param-table td {
    padding: 4px 8px 4px 0;
    vertical-align: top;
    border-bottom: none;
    background: none !important;
  }
  .mh-param-table tr:hover td { background: none !important; }
  .mh-param-table .pv { width: 44px; }
  .mh-param-table .pv code { font-size: 0.85rem; }
  .mh-param-table .pl { width: 140px; font-weight: 500; padding-right: 12px; }
  .mh-param-table .pd { color: var(--secondary-text-color); }
  .mh-param-table tr.recommended td { color: var(--primary-color, #03a9f4); }
  .mh-param-table tr.recommended .pd { color: var(--primary-color, #03a9f4); opacity: 0.85; }
  .mh-formula {
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--divider-color, #e0e0e0);
  }
  .mh-formula summary {
    cursor: pointer;
    color: var(--secondary-text-color);
    user-select: none;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    list-style: none;
  }
  .mh-formula summary::-webkit-details-marker { display: none; }
  .mh-formula summary::before { content: "▸ "; }
  details[open].mh-formula summary::before { content: "▾ "; }
  .mh-formula summary:hover { color: var(--primary-text-color); }
  .mh-formula-code {
    display: block;
    margin-top: 6px;
    padding: 8px 10px;
    background: var(--secondary-background-color, #f5f5f5);
    border-radius: 4px;
    font-size: 0.85rem;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .safe-badge {
    display: inline-block;
    font-size: 0.7rem;
    padding: 1px 6px;
    border-radius: 10px;
    margin-left: 6px;
    font-weight: 600;
    vertical-align: middle;
  }
  .safe-badge.yes { background: rgba(var(--rgb-success-color, 76,175,80), 0.15); color: var(--success-color, #4caf50); }
  .safe-badge.no  { background: rgba(var(--rgb-error-color, 219,68,55), 0.15); color: var(--error-color, #db4437); }
  .seg-control {
    display: inline-flex;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
    overflow: hidden;
    height: 40px;
  }
  .seg-btn {
    padding: 0 16px;
    height: 40px;
    border: none;
    border-radius: 0;
    background: transparent;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
    font-weight: 400;
    cursor: pointer;
    border-right: 1px solid var(--divider-color, #e0e0e0);
    transition: background 0.15s, color 0.15s;
  }
  .seg-btn:last-child { border-right: none; }
  .seg-btn.active {
    background: var(--primary-color, #03a9f4);
    color: #fff;
    font-weight: 500;
  }
  .seg-btn:hover:not(.active) {
    background: rgba(var(--rgb-primary-color, 3,169,244), 0.08);
    color: var(--primary-text-color);
  }
  .scan-stats-row {
    display: flex;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .scan-stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 80px;
    padding: 8px 14px;
    background: var(--secondary-background-color, #f5f5f5);
    border-radius: 8px;
  }
  .scan-stat-val { font-size: 1.4rem; font-weight: 600; line-height: 1.2; }
  .scan-stat-lbl { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--secondary-text-color); margin-top: 2px; }
  .apply-summary {
    padding: 10px 14px;
    border-radius: 6px;
    background: rgba(var(--rgb-error-color, 219,68,55), 0.06);
    border: 1px solid rgba(var(--rgb-error-color, 219,68,55), 0.25);
    font-size: 0.875rem;
    line-height: 1.6;
    margin-bottom: 12px;
  }
  .apply-summary strong { color: var(--error-color, #db4437); }
  code { font-family: monospace; background: var(--secondary-background-color, #f5f5f5); padding: 1px 4px; border-radius: 3px; font-size: 0.85em; }
`;

class StatisticsOutlierCleanerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._candidates = [];
    this._selected = new Set();
    this._msgId = 1;
    // Single source of truth for the scan range. Seeded so a scan works even
    // before HA's picker has finished loading.
    this._startDate = startOfLocalDay(-DEFAULT_RANGE_DAYS);
    this._endDate = endOfLocalDay(startOfLocalDay(0));
    this._pickerMounted = false;
    this._pickerLoading = false;
    this._pickerAttempts = 0;
    this._entityPickerMounted = false;
    this._entityPickerLoading = false;
    this._entityPickerAttempts = 0;
    this._statId = null;
    this._allStats = [];      // full list from WS, used for the picker allow-list
    this._recentStats = this._loadRecentStats();
    this._narrow = undefined;   // set by HA; undefined means "not told yet"
  }

  set hass(hass) {
    const firstSet = !this._hass;
    this._hass = hass;
    if (firstSet) {
      this._render();
      this._loadStatistics();
      this._loadHistory();
      this._setupDateRangePicker();
      this._setupEntityPicker();
      return;
    }
    // Every HA state change is a retry tick for a field whose HA component has
    // not loaded yet. Both components read locale and config from HA context, so
    // there is nothing to forward once they are mounted.
    if (!this._pickerMounted) this._setupDateRangePicker();
    if (!this._entityPickerMounted) this._setupEntityPicker();
  }

  // HA sets this on custom panels and updates it as the viewport changes. It is
  // what tells us the sidebar is hidden and the menu button is the only way out.
  set narrow(value) {
    if (this._narrow === value) return;
    this._narrow = value;
    if (this.shadowRoot.getElementById("app-toolbar")) this._renderToolbar();
  }

  get narrow() {
    return this._narrow;
  }

  // ---------------------------------------------------------------------------
  // Statistics list — drives the entity picker's allow-list and resolves names
  // in the fix history
  // ---------------------------------------------------------------------------

  async _loadStatistics() {
    try {
      const result = await this._send({ type: WS.list_sum_statistics });
      this._allStats = (result.statistics || [])
        .filter((s) => s && s.statistic_id)
        .sort((a, b) => {
          const aLabel = (a.name || a.statistic_id).toLowerCase();
          const bLabel = (b.name || b.statistic_id).toLowerCase();
          return aLabel.localeCompare(bLabel);
        });
    } catch (e) {
      this._allStats = [];
    }
    // The list usually arrives after the picker has mounted.
    const picker = this.shadowRoot?.querySelector(ENTITY_PICKER_TAG);
    if (picker) picker.includeEntities = this._entityAllowList();
  }

  /** Sum statistics that are real entities, for the picker's include list. */
  _entityAllowList() {
    return this._allStats
      .map((s) => s.statistic_id)
      .filter((id) => ENTITY_ID_RE.test(id));
  }

  // ---------------------------------------------------------------------------
  // Recent statistics (localStorage)
  // ---------------------------------------------------------------------------

  _loadRecentStats() {
    try {
      return JSON.parse(localStorage.getItem("statistics_outlier_cleaner_recents") || "[]");
    } catch (_) {
      return [];
    }
  }

  _saveRecentStat(statistic_id, name) {
    this._recentStats = this._recentStats.filter((s) => s.statistic_id !== statistic_id);
    this._recentStats.unshift({ statistic_id, name: name || null });
    this._recentStats = this._recentStats.slice(0, 5);
    try {
      localStorage.setItem("statistics_outlier_cleaner_recents", JSON.stringify(this._recentStats));
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // Date range
  // ---------------------------------------------------------------------------

  /**
   * Register ha-date-range-picker, which HA does not load for custom panels.
   *
   * Creating a card element that depends on it is enough: the card module's
   * static imports run on load, and registering the element is one of them. The
   * card itself is discarded — and it throws on setConfig without an energy
   * collection, which is fine and expected.
   */
  async _ensureDateRangePicker() {
    if (customElements.get(DATE_PICKER_TAG)) return true;
    // HA defines this once the frontend bundle is up; on a cold deep-link into
    // the panel it can arrive after our first render.
    if (typeof window.loadCardHelpers !== "function") return false;

    try {
      const helpers = await window.loadCardHelpers();
      try {
        await helpers.createCardElement({ type: DATE_PICKER_HOST_CARD });
      } catch (_) {
        // Only the import side effect matters.
      }
      // Generous, because this is a chunk fetch: a busy or low-powered instance
      // can take seconds, and giving up early leaves the panel with no date
      // control at all.
      await Promise.race([
        customElements.whenDefined(DATE_PICKER_TAG),
        new Promise((_, reject) =>
          setTimeout(reject, DATE_PICKER_LOAD_TIMEOUT_MS, new Error("timeout"))
        ),
      ]);
      return true;
    } catch (_) {
      return false;
    }
  }

  /**
   * Mount HA's picker, retrying while the frontend finishes coming up.
   *
   * There is no fallback control by design, so this has to keep trying rather
   * than give up after one attempt. Retries are driven by `set hass`, which HA
   * calls on every state change, and capped so a genuinely broken frontend does
   * not spin forever.
   */
  async _setupDateRangePicker() {
    if (this._pickerMounted || this._pickerLoading) return;
    if (this._pickerAttempts >= DATE_PICKER_MAX_ATTEMPTS) return;

    this._pickerLoading = true;
    this._pickerAttempts += 1;
    try {
      if (!(await this._ensureDateRangePicker())) return;

      const wrap = this._q("date-range-wrap");
      if (!wrap || wrap.querySelector(DATE_PICKER_TAG)) return;

      const picker = document.createElement(DATE_PICKER_TAG);
      picker.startDate = this._startDate;
      picker.endDate = this._endDate;
      // Leaving `ranges` unset is what makes the element build its own presets.
      picker.extendedPresets = true;
      picker.addEventListener("value-changed", (e) => {
        const value = e.detail?.value;
        if (!value) return;
        this._startDate = value.startDate;
        this._endDate = value.endDate;
        // The element fires the event but does not update its own startDate /
        // endDate — without this the field keeps showing the previous range.
        picker.startDate = value.startDate;
        picker.endDate = value.endDate;
      });

      wrap.replaceChildren(picker);
      this._pickerMounted = true;
    } finally {
      this._pickerLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Statistic field
  // ---------------------------------------------------------------------------

  /**
   * Register ha-entity-picker, which HA does not load for a custom panel.
   *
   * The entities card's config editor imports it, so building that card and
   * asking it for its config element runs the import as a side effect. The card
   * and the editor are discarded.
   */
  async _ensureEntityPicker() {
    if (customElements.get(ENTITY_PICKER_TAG)) return true;
    if (typeof window.loadCardHelpers !== "function") return false;

    try {
      const helpers = await window.loadCardHelpers();
      try {
        const card = await helpers.createCardElement({
          type: ENTITY_PICKER_HOST_CARD,
          entities: [],
        });
        await card.constructor.getConfigElement();
      } catch (_) {
        // Only the import side effect matters.
      }
      await Promise.race([
        customElements.whenDefined(ENTITY_PICKER_TAG),
        new Promise((_, reject) =>
          setTimeout(reject, DATE_PICKER_LOAD_TIMEOUT_MS, new Error("timeout"))
        ),
      ]);
      return true;
    } catch (_) {
      return false;
    }
  }

  /**
   * Swap the plain text field for ha-entity-picker, retrying while the frontend
   * comes up. Driven by `set hass` and capped like the date picker.
   */
  async _setupEntityPicker() {
    if (this._entityPickerMounted || this._entityPickerLoading) return;
    if (this._entityPickerAttempts >= ENTITY_PICKER_MAX_ATTEMPTS) return;

    this._entityPickerLoading = true;
    this._entityPickerAttempts += 1;
    try {
      if (!(await this._ensureEntityPicker())) return;

      const wrap = this._q("stat-wrap");
      if (!wrap || wrap.querySelector(ENTITY_PICKER_TAG)) return;

      const picker = document.createElement(ENTITY_PICKER_TAG);
      picker.label = "Statistic";
      // Let a statistic that has no live entity still be typed in.
      picker.allowCustomEntity = true;
      picker.includeEntities = this._entityAllowList();
      if (this._statId) picker.value = this._statId;
      picker.addEventListener("value-changed", (e) => {
        this._statId = e.detail?.value || null;
      });

      const fallback = this._q("stat-input");
      if (fallback) fallback.replaceWith(picker);
      else wrap.appendChild(picker);
      this._entityPickerMounted = true;
    } finally {
      this._entityPickerLoading = false;
    }
  }

  /** Render the recent-statistics chip row. */
  _renderRecents() {
    const row = this._q("stat-recents");
    if (!row) return;
    if (!this._recentStats.length) {
      row.innerHTML = "";
      row.classList.add("hidden");
      return;
    }
    row.classList.remove("hidden");
    row.innerHTML = this._recentStats
      .map(
        (s) =>
          `<button class="text-btn" type="button" data-value="${s.statistic_id}">${
            s.name || s.statistic_id
          }</button>`
      )
      .join("");
    row.querySelectorAll("button[data-value]").forEach((b) => {
      b.addEventListener("click", () => this._pickStat(b.dataset.value));
    });
  }

  /** Select a statistic id from outside the picker (a recent chip). */
  _pickStat(id) {
    this._statId = id;
    const picker = this.shadowRoot.querySelector(ENTITY_PICKER_TAG);
    if (picker) picker.value = id;
    else {
      const input = this._q("stat-input");
      if (input) input.value = id;
    }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>

      <div class="app-toolbar" id="app-toolbar"></div>

      <div class="panel-content" id="panel-content">
      <div class="card">
        <h3>Scan</h3>

        <div class="form-row">
          <div class="form-group stat-field" id="stat-wrap">
            <div class="stat-recents hidden" id="stat-recents"></div>
            <input type="text" id="stat-input" placeholder="Statistic id" autocomplete="off">
          </div>
        </div>

        <div class="form-row" id="date-range-wrap"></div>

        <div class="form-row">
          <div class="form-group">
            <label>Detection method</label>
            <div class="seg-control" id="method-seg" role="group" aria-label="Detection method">
              <button class="seg-btn active" data-value="mad" type="button">MAD</button>
              <button class="seg-btn" data-value="absolute" type="button">Absolute</button>
              <button class="seg-btn" data-value="top_n" type="button">Top N</button>
            </div>
          </div>
          <div class="form-group" id="opt-mad">
            <label>MAD factor</label>
            <input type="number" id="mad-factor" value="6" min="1" max="50" step="0.5">
          </div>
          <div class="form-group hidden" id="opt-absolute">
            <label>Threshold</label>
            <input type="number" id="threshold" value="100" min="0" step="0.001">
          </div>
          <div class="form-group hidden" id="opt-top-n">
            <label>Top N</label>
            <input type="number" id="top-n" value="10" min="1">
          </div>
        </div>

        <div id="method-help"></div>

        <div class="form-row">
          <button class="primary" id="btn-scan">Scan</button>
        </div>
      </div>

      <div id="scan-status"></div>

      <div id="results-card" class="card hidden">
        <h3>Detected Outliers</h3>
        <div id="scan-stats-row" class="scan-stats-row hidden"></div>
        <details id="scan-detail" class="hidden" style="margin-bottom:10px">
          <summary style="cursor:pointer;font-size:0.75rem;color:var(--secondary-text-color);user-select:none">Statistical detail</summary>
          <div id="scan-meta" class="meta" style="margin-top:4px"></div>
        </details>

        <div class="toolbar">
          <button class="text-btn" id="btn-select-all">Select all</button>
          <button class="text-btn" id="btn-select-none">Deselect all</button>
          <span class="selection-label" id="selection-count"></span>
        </div>

        <div id="results-table"></div>

        <div id="apply-area" class="hidden" style="margin-top:14px">
          <div id="apply-summary" class="apply-summary"></div>
          <div class="fix-controls">
            <div class="form-group">
              <label>Replace each reading with</label>
              <input type="number" id="replacement" value="0" step="0.001">
            </div>
            <label class="dry-run-row">
              <input type="checkbox" id="dry-run">
              Preview only (no DB changes)
            </label>
            <button class="danger" id="btn-apply">Apply Fix</button>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Fix History</h3>
        <button class="secondary" id="btn-refresh-history" style="margin-bottom:12px">Refresh</button>
        <div id="history-table"></div>
      </div>
      </div>
    `;

    this._renderToolbar();
    this._renderRecents();
    this._wireEvents();
  }

  _renderToolbar() {
    const bar = this._q("app-toolbar");
    if (!bar) return;
    bar.innerHTML = "";

    // Only needed when the sidebar is hidden. `narrow` is set by HA (see
    // setCustomPanelProperties in ha-panel-custom). Undefined means it hasn't
    // told us yet, so show the button rather than risk a dead end.
    if (this._narrow !== false) {
      // We own this button rather than reusing HA's `ha-menu-button`: that
      // element resolves `narrow` and `ui` through @lit/context and may not be
      // defined at the moment we render, so depending on it is a race. Firing
      // the event directly is what ha-menu-button itself does on click.
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "menu-btn";
      btn.setAttribute("aria-label", "Open sidebar");
      btn.innerHTML =
        `<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">` +
        `<path fill="currentColor" d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>`;
      btn.addEventListener("click", () => this._toggleSidebar());
      bar.appendChild(btn);
    }

    const title = document.createElement("div");
    title.className = "app-title";
    title.textContent = "Statistics Outlier Cleaner";
    bar.appendChild(title);
  }

  _toggleSidebar() {
    // HA listens for this on the way up from the panel. bubbles + composed match
    // fireEvent's defaults so it escapes our shadow root and reaches the app.
    this.dispatchEvent(
      new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })
    );
  }

  _wireEvents() {
    this._q("method-seg").querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._q("method-seg").querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this._updateMethodOptions();
      });
    });
    this._updateMethodOptions(); // render initial help box
    this._q("btn-scan").addEventListener("click", () => this._scan());
    this._q("btn-apply").addEventListener("click", () => this._applyFix());
    this._q("btn-select-all").addEventListener("click", () => this._selectAll(true));
    this._q("btn-select-none").addEventListener("click", () => this._selectAll(false));
    this._q("btn-refresh-history").addEventListener("click", () => this._loadHistory());
    this._q("replacement").addEventListener("input", () => this._renderApplySummary());
    this._q("dry-run").addEventListener("change", () => this._renderApplySummary());

    // The fallback text field only matters until ha-entity-picker mounts; a
    // typed id is read straight off it by _scan().
    this._q("stat-input")?.addEventListener("input", (e) => {
      this._statId = e.target.value.trim() || null;
    });
  }

  _q(id) { return this.shadowRoot.getElementById(id); }

  _getMethod() {
    return this._q("method-seg")?.querySelector(".seg-btn.active")?.dataset.value || "mad";
  }

  _updateMethodOptions() {
    const m = this._getMethod();
    this._q("opt-mad").classList.toggle("hidden", m !== "mad");
    this._q("opt-absolute").classList.toggle("hidden", m !== "absolute");
    this._q("opt-top-n").classList.toggle("hidden", m !== "top_n");
    this._renderMethodHelp(m);
  }

  _renderMethodHelp(method) {
    const h = METHOD_HELP[method];
    if (!h) return;

    const safeBadge = h.safe
      ? `<span class="safe-badge yes">✓ Safe for automations</span>`
      : `<span class="safe-badge no">⚠ Manual use only</span>`;

    const warningHtml = h.warning
      ? `<div class="mh-warning">${h.warning}</div>`
      : "";

    const scenarioHtml = h.example.scenario
      ? `<p class="mh-scenario">${h.example.scenario}</p>`
      : "";

    const casesHtml = h.example.cases.map((c) =>
      `<div class="mh-case ${c.ok ? "ok" : "bad"}">
        <span class="mh-case-icon">${c.ok ? "✓" : "✗"}</span>
        <span><strong>${c.label}</strong> — ${c.outcome}</span>
      </div>`
    ).join("");

    const resultHtml = h.example.result
      ? `<p class="mh-case-note">${h.example.result}</p>`
      : "";

    const paramRowsHtml = h.paramRows.map((r) =>
      `<tr class="${r.recommended ? "recommended" : ""}">
        <td class="pv"><code>${r.value}</code></td>
        <td class="pl">${r.label}</td>
        <td class="pd">${r.desc}</td>
      </tr>`
    ).join("");

    this._q("method-help").innerHTML = `
      <div class="method-help ${h.safe ? "" : "warn"}">
        <div class="mh-header">
          <span class="mh-title">${h.title}</span>
          ${safeBadge}
        </div>
        <p class="mh-summary">${h.summary}</p>
        ${warningHtml}
        <div class="mh-section">
          <span class="mh-label">Example</span>
          ${scenarioHtml}
          <div class="mh-cases">${casesHtml}</div>
          ${resultHtml}
        </div>
        <div class="mh-section">
          <span class="mh-label">${h.paramName} <span class="mh-param-hint">· ${h.paramHint}</span></span>
          <table class="mh-param-table"><tbody>${paramRowsHtml}</tbody></table>
        </div>
        <details class="mh-formula">
          <summary>Technical formula</summary>
          <code class="mh-formula-code">${h.formula}</code>
        </details>
      </div>`;
  }

  // ---------------------------------------------------------------------------
  // WS helpers
  // ---------------------------------------------------------------------------

  _send(msg) {
    return this._hass.connection.sendMessagePromise({ id: this._msgId++, ...msg });
  }

  // ---------------------------------------------------------------------------
  // Scan
  // ---------------------------------------------------------------------------

  async _scan() {
    const statId = this._statId || this._q("stat-input")?.value.trim() || "";
    if (!statId) { this._showStatus("error", "Select a statistic first."); return; }

    const method = this._getMethod();

    const params = {
      type: WS.fetch_outliers,
      statistic_id: statId,
      period: "hybrid",
      method,
    };

    // Both controls keep _startDate/_endDate current, and _endDate is already
    // the last instant of its day, so no end-of-day adjustment is needed here.
    if (this._startDate) params.start_ts = this._startDate.getTime() / 1000;
    if (this._endDate) params.end_ts = this._endDate.getTime() / 1000;

    if (method === "mad")      params.mad_factor = parseFloat(this._q("mad-factor").value) || 6;
    if (method === "absolute") params.threshold  = parseFloat(this._q("threshold").value) || 0;
    if (method === "top_n")    params.top_n      = parseInt(this._q("top-n").value) || 10;

    this._showStatus("info", "Scanning…");
    this._q("btn-scan").disabled = true;

    try {
      const result = await this._send(params);
      this._candidates = result.candidates || [];
      // Nothing is pre-selected. A scan result is a set of suggestions, not a
      // verdict — top_n in particular always returns N rows whether or not the
      // data is clean, so pre-selecting them invites fixing normal readings.
      this._selected = new Set();
      this._renderResults(result);
      this._clearStatus();
      const statMeta = this._allStats.find((s) => s.statistic_id === statId);
      this._saveRecentStat(statId, statMeta?.name || null);
    } catch (e) {
      this._showStatus("error", `Scan failed: ${e.message || JSON.stringify(e)}`);
    } finally {
      this._q("btn-scan").disabled = false;
    }

    this._loadHistory();
  }

  _renderResults(report) {
    const card = this._q("results-card");
    card.classList.remove("hidden");

    // Stats row
    const statsRow = this._q("scan-stats-row");
    statsRow.innerHTML = `
      <div class="scan-stat"><span class="scan-stat-val">${report.scanned_rows}</span><span class="scan-stat-lbl">rows scanned</span></div>
      <div class="scan-stat"><span class="scan-stat-val">${this._candidates.length}</span><span class="scan-stat-lbl">flagged</span></div>
      <div class="scan-stat"><span class="scan-stat-val" style="font-size:1rem;text-transform:uppercase">${report.method}</span><span class="scan-stat-lbl">method</span></div>
    `;
    statsRow.classList.remove("hidden");

    // Collapsible stat detail
    const parts = [`${report.scanned_rows} rows scanned`, `Method: ${report.method}`];
    if (report.median != null) parts.push(`Median: ${report.median.toFixed(4)}`);
    if (report.mad    != null) parts.push(`MAD: ${report.mad.toFixed(4)}`);
    this._q("scan-meta").textContent = parts.join(" · ");
    if (report.median != null || report.mad != null) {
      this._q("scan-detail").classList.remove("hidden");
    }

    if (!this._candidates.length) {
      this._q("results-table").innerHTML = `
        <div class="status success" style="display:flex;align-items:center;gap:8px">
          <span style="font-size:1.1rem">✓</span>
          <span>No outliers detected in the selected date range.</span>
        </div>`;
      this._q("apply-area").classList.add("hidden");
      this._updateSelectionCount();
      return;
    }

    this._renderTable();
    this._q("apply-area").classList.remove("hidden");
    this._renderApplySummary();
  }

  _renderApplySummary() {
    const n = this._selected.size;
    const summaryEl = this._q("apply-summary");
    const applyBtn = this._q("btn-apply");
    if (!summaryEl || !applyBtn) return;
    if (n === 0) {
      summaryEl.innerHTML = "Select rows above to apply a fix.";
      applyBtn.textContent = "Apply Fix";
      return;
    }
    const replacement = this._q("replacement")?.value ?? "0";
    const isDry = this._q("dry-run")?.checked;
    summaryEl.innerHTML = isDry
      ? `Preview: would replace <strong>${n} reading${n !== 1 ? "s" : ""}</strong> with <strong>${replacement}</strong> — no DB changes`
      : `Replace <strong>${n} reading${n !== 1 ? "s" : ""}</strong> with <strong>${replacement}</strong>`;
    applyBtn.textContent = isDry ? `Preview ${n} rows` : `Apply to ${n} rows`;
  }

  _renderTable() {
    const tbody = this._candidates.map((c, i) => {
      const dt = new Date(c.start).toLocaleString();
      const checked = this._selected.has(i) ? "checked" : "";
      return `<tr class="${this._selected.has(i) ? "selected" : ""}" data-idx="${i}">
        <td><input type="checkbox" class="row-check" data-idx="${i}" ${checked}></td>
        <td>${dt}</td>
        <td>${c.period}</td>
        <td class="change-cell">${c.change.toFixed(4)}</td>
        <td>${c.state != null ? c.state.toFixed(4) : "—"}</td>
      </tr>`;
    }).join("");

    this._q("results-table").innerHTML = `
      <table>
        <thead>
          <tr>
            <th style="width:32px"><input type="checkbox" id="check-all" ${
              this._allSelected() ? "checked" : ""
            }></th>
            <th>Start</th><th>Period</th><th>Change</th><th>State</th>
          </tr>
        </thead>
        <tbody>${tbody}</tbody>
      </table>`;

    this._q("check-all").addEventListener("change", (e) => this._selectAll(e.target.checked));
    this._q("results-table").querySelectorAll(".row-check").forEach((cb) => {
      cb.addEventListener("change", (e) => {
        const idx = parseInt(e.target.dataset.idx);
        e.target.checked ? this._selected.add(idx) : this._selected.delete(idx);
        this._q("results-table").querySelector(`tr[data-idx="${idx}"]`)
          ?.classList.toggle("selected", e.target.checked);
        this._updateSelectionCount();
        this._updateCheckAll();
      });
    });

    this._updateSelectionCount();
  }

  _selectAll(on) {
    if (on) this._candidates.forEach((_, i) => this._selected.add(i));
    else this._selected.clear();
    this._renderTable();
  }

  _allSelected() {
    return this._candidates.length > 0 && this._selected.size === this._candidates.length;
  }

  _updateSelectionCount() {
    const n = this._selected.size, total = this._candidates.length;
    this._q("selection-count").textContent = total ? `${n} of ${total} selected` : "";
    this._q("btn-apply").disabled = n === 0;
    this._renderApplySummary();
  }

  _updateCheckAll() {
    const cb = this._q("check-all");
    if (!cb) return;
    cb.checked = this._allSelected();
    cb.indeterminate = this._selected.size > 0 && this._selected.size < this._candidates.length;
  }

  // ---------------------------------------------------------------------------
  // Apply fix
  // ---------------------------------------------------------------------------

  async _applyFix() {
    if (!this._selected.size) return;

    const statId = this._statId || this._q("stat-input")?.value.trim() || "";
    const replacement = parseFloat(this._q("replacement").value) || 0;
    const dryRun = this._q("dry-run").checked;

    const candidates = [...this._selected].map((i) => ({
      start_ts: this._candidates[i].start / 1000,
      period: this._candidates[i].period,
    }));

    this._showStatus("info", dryRun ? "Running dry-run…" : "Applying fix…");
    this._q("btn-apply").disabled = true;

    try {
      const result = await this._send({
        type: WS.apply_fix,
        statistic_id: statId,
        candidates,
        replacement,
        dry_run: dryRun,
      });

      const hasErrors = result.errors?.length > 0;
      const msg = dryRun
        ? `Dry run: would fix ${result.planned} row(s).`
        : `Fixed ${result.applied} row(s). Fix ID: <span class="fix-id-chip">${result.fix_id}</span>`;
      this._showStatus(hasErrors ? "error" : "success", msg);

      if (dryRun && result.queries && result.queries.length) {
        const pre = document.createElement("pre");
        pre.style.cssText = "font-size:0.75rem;overflow-x:auto;background:var(--secondary-background-color,#f5f5f5);padding:12px;border-radius:4px;margin-top:8px;white-space:pre-wrap;word-break:break-all;";
        pre.textContent = result.queries.join("\n\n");
        this._q("scan-status").appendChild(pre);
      }

      if (!dryRun) {
        const removed = new Set(this._selected);
        this._candidates = this._candidates.filter((_, i) => !removed.has(i));
        this._selected = new Set();
        if (this._candidates.length) {
          this._renderTable();
        } else {
          this._q("results-table").innerHTML = `
            <div class="status success" style="display:flex;align-items:center;gap:8px">
              <span style="font-size:1.1rem">✓</span>
              <span>All selected outliers have been fixed.</span>
            </div>`;
          this._q("apply-area").classList.add("hidden");
        }
        this._loadHistory();
      }
    } catch (e) {
      this._showStatus("error", `Apply failed: ${e.message || JSON.stringify(e)}`);
    } finally {
      if (this._selected.size) this._q("btn-apply").disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Fix history
  // ---------------------------------------------------------------------------

  async _loadHistory() {
    try {
      const result = await this._send({ type: WS.list_fixes, limit: 20 });
      this._renderHistory(result.fixes || []);
    } catch (e) {
      this._q("history-table").innerHTML =
        `<p style="color:var(--error-color)">Failed to load history: ${e.message || e}</p>`;
    }
  }

  _renderHistory(fixes) {
    const div = this._q("history-table");
    if (!fixes.length) { div.innerHTML = "<p>No fixes recorded yet.</p>"; return; }

    const rows = fixes.map((f) => {
      const dt = new Date(f.fix_ts * 1000).toLocaleString();
      const statMeta = this._allStats.find((s) => s.statistic_id === f.statistic_id);
      const name = statMeta?.name;
      const sensorCell = name
        ? `<div style="font-weight:500;font-size:0.85rem">${name}</div><div style="font-size:0.75rem;color:var(--secondary-text-color)">${f.statistic_id}</div>`
        : f.statistic_id;
      return `<tr>
        <td>${dt}</td>
        <td>${sensorCell}</td>
        <td>${f.row_count}</td>
        <td><button class="text-btn restore-btn" data-fix-id="${f.fix_id}">Restore</button></td>
      </tr>`;
    }).join("");

    div.innerHTML = `
      <table>
        <thead><tr><th>Date</th><th>Sensor</th><th>Rows</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;

    div.querySelectorAll(".restore-btn").forEach((btn) => {
      btn.addEventListener("click", () => this._restoreFix(btn.dataset.fixId));
    });
  }

  async _restoreFix(fixId) {
    if (!confirm(`Restore fix ${fixId.slice(0, 8)}…? This will revert the database changes.`)) return;
    this._showStatus("info", "Restoring…");
    try {
      const result = await this._send({ type: WS.restore_fix, fix_id: fixId });
      this._showStatus("success", `Restored ${result.restored} row(s).`);
      this._loadHistory();
    } catch (e) {
      this._showStatus("error", `Restore failed: ${e.message || JSON.stringify(e)}`);
    }
  }

  // ---------------------------------------------------------------------------
  // Status helpers
  // ---------------------------------------------------------------------------

  _showStatus(type, msg) {
    this._q("scan-status").innerHTML = `<div class="status ${type}">${msg}</div>`;
  }

  _clearStatus() { this._q("scan-status").innerHTML = ""; }
}

customElements.define("statistics-outlier-cleaner-panel", StatisticsOutlierCleanerPanel);
