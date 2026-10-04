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

const STYLES = `
  /* ha-panel-custom gives us display:block and safe-area padding but no height,
     so height:100% here would resolve against an auto-height parent and
     collapse. The document would scroll instead of us, and a sticky toolbar
     would pin to that scrollport rather than the viewport. Take a definite
     height from the viewport, less the insets the container already pads for. */
  :host {
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
  /* A custom panel registered with embed_iframe: false owns the whole view —
     Home Assistant renders no header of its own. Without a way to reach the
     sidebar the panel is a dead end on mobile, where the sidebar is hidden.
     Pinned by layout: toolbar and tabs are fixed rows, only #content scrolls. */
  .app-toolbar {
    flex: 0 0 auto;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 4px;
    height: 56px;
    padding: 0 12px;
    box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-background-color, #fafafa));
    color: var(--app-header-text-color, var(--primary-text-color));
  }
  .app-toolbar .app-title { font-size: 1.15rem; font-weight: 500; }
  .menu-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    color: inherit;
    border: none;
    cursor: pointer;
    height: 40px;
    width: 40px;
    padding: 0;
    flex: 0 0 auto;
    border-radius: 50%;
  }
  .menu-btn:hover { background: rgba(var(--rgb-primary-text-color, 0,0,0), 0.08); }
  [role="tablist"] {
    flex: 0 0 auto;
    display: flex;
    gap: 8px;
    padding: 0 16px;
    overflow-x: auto;
    border-bottom: 1px solid var(--divider-color, #ccc);
  }
  [role="tab"] {
    background: none;
    border: none;
    padding: 8px 16px;
    cursor: pointer;
    font-size: 14px;
    color: inherit;
    white-space: nowrap;
  }
  [role="tab"][aria-selected="true"] { border-bottom: 2px solid var(--primary-color, #03a9f4); font-weight: 600; }
  /* A view may own its scrolling (min-height: 0 plus an inner pane) or let
     this pane scroll it. */
  #content {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }
`;

class RecorderToolkitPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._activeTabId = TABS[0].id;
    this._mountedViews = {};
    this._hass = null;
    this._narrow = undefined; // set by HA; undefined means "not told yet"
  }

  set hass(value) {
    this._hass = value;
    for (const view of Object.values(this._mountedViews)) view.hass = value;
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    if (this._narrow === value) return;
    this._narrow = value;
    this._renderToolbar();
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
      <style>${STYLES}</style>
      <div class="app-toolbar" id="app-toolbar"></div>
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
    this._renderToolbar();
    this._mountActiveTab();
  }

  _renderToolbar() {
    const bar = this.shadowRoot.getElementById("app-toolbar");
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
      // bubbles + composed match fireEvent's defaults so the event escapes our
      // shadow root and reaches HA's app shell.
      btn.addEventListener("click", () =>
        this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }))
      );
      bar.appendChild(btn);
    }

    const title = document.createElement("div");
    title.className = "app-title";
    title.textContent = "Recorder Toolkit";
    bar.appendChild(title);
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
