/**
 * Duplicate Finder — scans for entities recording the same measurement
 * twice and generates a recorder exclude YAML block.
 *
 * Vanilla web component, no build step. `_send` wraps
 * `hass.connection.sendMessagePromise`, overridable in tests.
 */

const DOMAIN = "recorder_toolkit";

class DuplicateFinderView extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._groups = [];
    this._keepByGroup = {}; // groupIndex -> entity_id
    this._yaml = "";
    this._scanning = false;
    this._error = null;
    this._render();
  }

  _send(msg) {
    return this.hass.connection.sendMessagePromise(msg);
  }

  async _scan() {
    if (this._scanning) return;
    this._scanning = true;
    this._error = null;
    this._render();
    try {
      const result = await this._send({ type: `${DOMAIN}/list_duplicate_candidates` });
      this._groups = result.groups || [];
      this._keepByGroup = {};
      this._groups.forEach((group, index) => {
        // Scan results are already ranked best-first; default to that suggestion.
        this._keepByGroup[index] = group.members[0].entity_id;
      });
      this._yaml = "";
    } catch (err) {
      this._error = err;
    } finally {
      this._scanning = false;
      this._render();
    }
  }

  async _generateYaml() {
    const group_selections = this._groups.map((group, index) => ({
      members: group.members.map((m) => m.entity_id),
      keep: this._keepByGroup[index],
    }));
    try {
      const result = await this._send({
        type: `${DOMAIN}/generate_exclude_yaml`,
        group_selections,
      });
      this._yaml = result.yaml || "";
      this._error = null;
    } catch (err) {
      this._error = err;
    }
    this._render();
  }

  _setKeep(groupIndex, entityId) {
    this._keepByGroup[groupIndex] = entityId;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        button { margin-bottom: 16px; }
        .group { border: 1px solid var(--divider-color, #ccc); border-radius: 4px; padding: 8px; margin-bottom: 8px; }
        .member { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
        #yaml-output { white-space: pre; background: var(--secondary-background-color, #f5f5f5); padding: 12px; border-radius: 4px; }
        #scan-error { color: var(--error-color, #db4437); margin-bottom: 16px; }
      </style>
      <button id="scan-button" ${this._scanning ? "disabled" : ""}>
        ${this._scanning ? "Scanning…" : "Scan for duplicates"}
      </button>
      ${this._error ? `<div id="scan-error">${this._error.message || this._error}</div>` : ""}
      <div id="groups">
        ${this._groups
          .map(
            (group, groupIndex) => `
          <div class="group" data-group-index="${groupIndex}">
            ${group.members
              .map(
                (member) => `
              <div class="member" data-entity-id="${member.entity_id}">
                <input
                  type="radio"
                  name="keep-${groupIndex}"
                  value="${member.entity_id}"
                  ${this._keepByGroup[groupIndex] === member.entity_id ? "checked" : ""}
                />
                <span>${member.entity_id} (${member.row_count} rows)</span>
              </div>
            `
              )
              .join("")}
          </div>
        `
          )
          .join("")}
      </div>
      ${this._groups.length ? '<button id="generate-yaml-button">Generate YAML</button>' : ""}
      ${this._yaml ? `<pre id="yaml-output">${this._yaml}</pre>` : ""}
    `;

    this.shadowRoot
      .getElementById("scan-button")
      .addEventListener("click", () => this._scan());

    const generateButton = this.shadowRoot.getElementById("generate-yaml-button");
    if (generateButton) {
      generateButton.addEventListener("click", () => this._generateYaml());
    }

    for (const radio of this.shadowRoot.querySelectorAll("input[type='radio']")) {
      radio.addEventListener("change", (event) => {
        const groupIndex = Number(event.target.closest(".group").dataset.groupIndex);
        this._setKeep(groupIndex, event.target.value);
      });
    }
  }
}

customElements.define("duplicate-finder-view", DuplicateFinderView);
