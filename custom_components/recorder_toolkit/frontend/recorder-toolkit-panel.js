/**
 * Recorder Toolkit — sidebar panel shell.
 *
 * Vanilla web component, no build step. Hosts two tabs, each backed by its
 * own lazily-imported custom element:
 *   - "Outlier Cleaner" -> <outlier-cleaner-view> (./outlier-cleaner-view.js)
 *   - "Duplicate Finder" -> <duplicate-finder-view> (./duplicate-finder-view.js)
 *
 * A view's module is imported the first time its tab is selected (matching
 * this integration's existing lazy-load convention for HA frontend
 * internals), and its `hass`/`narrow` properties are kept in sync with
 * whatever HA has set on this shell.
 */

const TABS = [
  { id: "outlier", label: "Outlier Cleaner", tag: "outlier-cleaner-view", module: "./outlier-cleaner-view.js" },
  { id: "duplicates", label: "Duplicate Finder", tag: "duplicate-finder-view", module: "./duplicate-finder-view.js" },
];

class RecorderToolkitPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._activeTabId = TABS[0].id;
    this._mountedViews = {};
    this._hass = null;
    this._narrow = false;
  }

  set hass(value) {
    this._hass = value;
    for (const view of Object.values(this._mountedViews)) view.hass = value;
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrow = value;
    for (const view of Object.values(this._mountedViews)) view.narrow = value;
  }

  get narrow() {
    return this._narrow;
  }

  connectedCallback() {
    this._render();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; }
        [role="tablist"] { display: flex; gap: 8px; border-bottom: 1px solid var(--divider-color, #ccc); margin-bottom: 16px; }
        [role="tab"] { background: none; border: none; padding: 8px 16px; cursor: pointer; font-size: 14px; }
        [role="tab"][aria-selected="true"] { border-bottom: 2px solid var(--primary-color, #03a9f4); font-weight: 600; }
        #content { min-height: 200px; }
      </style>
      <div role="tablist">
        ${TABS.map(
          (tab) => `<button role="tab" aria-selected="${tab.id === this._activeTabId}" data-tab-id="${tab.id}">${tab.label}</button>`
        ).join("")}
      </div>
      <div id="content"></div>
    `;
    for (const button of this.shadowRoot.querySelectorAll("[role='tab']")) {
      button.addEventListener("click", () => this._selectTab(button.dataset.tabId));
    }
    this._mountActiveTab();
  }

  _selectTab(tabId) {
    if (tabId === this._activeTabId) return;
    this._activeTabId = tabId;
    this._render();
  }

  async _mountActiveTab() {
    const tab = TABS.find((t) => t.id === this._activeTabId);
    const content = this.shadowRoot.getElementById("content");
    content.innerHTML = "";
    if (!customElements.get(tab.tag)) {
      try {
        await import(tab.module);
      } catch (err) {
        // Relative dynamic import() resolves against this script's own URL
        // in a browser (served from PANEL_STATIC_PATH), which works. Some
        // test harnesses that eval this script's text have no such URL to
        // resolve against, so failure here is caught rather than left as
        // an unhandled rejection.
        console.error(`Failed to load view module for tab "${tab.id}":`, err);
        return;
      }
    }
    let view = this._mountedViews[tab.id];
    if (!view) {
      view = document.createElement(tab.tag);
      this._mountedViews[tab.id] = view;
    }
    view.hass = this._hass;
    view.narrow = this._narrow;
    content.appendChild(view);
  }
}

customElements.define("recorder-toolkit-panel", RecorderToolkitPanel);
