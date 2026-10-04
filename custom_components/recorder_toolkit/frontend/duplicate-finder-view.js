/**
 * Duplicate Finder — scans for entities recording the same measurement
 * twice and generates a recorder exclude YAML block.
 *
 * Two-phase by design: `_scan` only fuzzy-matches entity ids/names (no
 * statistics reads at all — reading statistics for every candidate on
 * every scan overloaded the recorder executor on larger installs). Each
 * fuzzy group is confirmed on demand via `_correlate`, which reads
 * statistics only for that one group's members.
 *
 * Vanilla web component, no build step. `_send` wraps
 * `hass.connection.sendMessagePromise`, overridable in tests.
 */

const DOMAIN = "recorder_toolkit";

class DuplicateFinderView extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._fuzzyGroups = []; // [{members, status, error, confirmedSubgroups}]
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
      this._fuzzyGroups = (result.groups || []).map((group) => ({
        members: group.members,
        status: "unconfirmed",
        error: null,
        confirmedSubgroups: [],
      }));
      this._yaml = "";
    } catch (err) {
      this._error = err;
    } finally {
      this._scanning = false;
      this._render();
    }
  }

  async _correlate(groupIndex) {
    const fuzzyGroup = this._fuzzyGroups[groupIndex];
    if (!fuzzyGroup || fuzzyGroup.status === "checking") return;
    fuzzyGroup.status = "checking";
    fuzzyGroup.error = null;
    this._render();
    try {
      const result = await this._send({
        type: `${DOMAIN}/correlate_duplicate_group`,
        members: fuzzyGroup.members,
      });
      fuzzyGroup.confirmedSubgroups = (result.groups || []).map((group) => ({
        // Already ranked best-first by the server; default to that suggestion.
        members: group.members,
        keep: group.members[0].entity_id,
      }));
      fuzzyGroup.status = "confirmed";
    } catch (err) {
      fuzzyGroup.status = "error";
      fuzzyGroup.error = err;
    }
    this._render();
  }

  async _generateYaml() {
    const group_selections = [];
    for (const fuzzyGroup of this._fuzzyGroups) {
      for (const sub of fuzzyGroup.confirmedSubgroups) {
        group_selections.push({
          members: sub.members.map((m) => m.entity_id),
          keep: sub.keep,
        });
      }
    }
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

  _setKeep(groupIndex, subgroupIndex, entityId) {
    this._fuzzyGroups[groupIndex].confirmedSubgroups[subgroupIndex].keep = entityId;
  }

  _hasConfirmedSubgroups() {
    return this._fuzzyGroups.some((fg) => fg.confirmedSubgroups.length > 0);
  }

  _renderMemberRow(entityId, extra = "") {
    return `<div class="member" data-entity-id="${entityId}">${extra}<span>${entityId}</span></div>`;
  }

  _renderFuzzyGroup(fuzzyGroup, groupIndex) {
    if (fuzzyGroup.status === "confirmed") {
      if (fuzzyGroup.confirmedSubgroups.length === 0) {
        return `
          <div class="group" data-group-index="${groupIndex}">
            <div class="no-duplicates">No duplicates confirmed in this group.</div>
          </div>
        `;
      }
      return `
        <div class="group" data-group-index="${groupIndex}">
          ${fuzzyGroup.confirmedSubgroups
            .map(
              (sub, subgroupIndex) => `
            <div class="subgroup" data-subgroup-index="${subgroupIndex}">
              ${sub.members
                .map((member) =>
                  this._renderMemberRow(
                    member.entity_id,
                    `<input
                      type="radio"
                      name="keep-${groupIndex}-${subgroupIndex}"
                      value="${member.entity_id}"
                      ${sub.keep === member.entity_id ? "checked" : ""}
                    />`
                  ) + ` (${member.row_count} rows)`
                )
                .join("")}
            </div>
          `
            )
            .join("")}
        </div>
      `;
    }

    const checking = fuzzyGroup.status === "checking";
    return `
      <div class="group" data-group-index="${groupIndex}">
        ${fuzzyGroup.members.map((id) => this._renderMemberRow(id)).join("")}
        ${fuzzyGroup.error ? `<div class="group-error">${fuzzyGroup.error.message || fuzzyGroup.error}</div>` : ""}
        <button id="correlate-button-${groupIndex}" ${checking ? "disabled" : ""}>
          ${checking ? "Checking…" : fuzzyGroup.status === "error" ? "Retry" : "Check correlation"}
        </button>
      </div>
    `;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; }
        button { margin-bottom: 16px; }
        .group { border: 1px solid var(--divider-color, #ccc); border-radius: 4px; padding: 8px; margin-bottom: 8px; }
        .subgroup { padding: 4px 0; }
        .member { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
        .group-error { color: var(--error-color, #db4437); margin: 8px 0; }
        .no-duplicates { color: var(--secondary-text-color, #666); }
        #yaml-output { white-space: pre; background: var(--secondary-background-color, #f5f5f5); padding: 12px; border-radius: 4px; }
        #scan-error { color: var(--error-color, #db4437); margin-bottom: 16px; }
      </style>
      <button id="scan-button" ${this._scanning ? "disabled" : ""}>
        ${this._scanning ? "Scanning…" : "Scan for duplicates"}
      </button>
      ${this._error ? `<div id="scan-error">${this._error.message || this._error}</div>` : ""}
      <div id="groups">
        ${this._fuzzyGroups
          .map((fuzzyGroup, groupIndex) => this._renderFuzzyGroup(fuzzyGroup, groupIndex))
          .join("")}
      </div>
      ${this._hasConfirmedSubgroups() ? '<button id="generate-yaml-button">Generate YAML</button>' : ""}
      ${this._yaml ? `<pre id="yaml-output">${this._yaml}</pre>` : ""}
    `;

    this.shadowRoot
      .getElementById("scan-button")
      .addEventListener("click", () => this._scan());

    this._fuzzyGroups.forEach((_, groupIndex) => {
      const button = this.shadowRoot.getElementById(`correlate-button-${groupIndex}`);
      if (button) {
        button.addEventListener("click", () => this._correlate(groupIndex));
      }
    });

    const generateButton = this.shadowRoot.getElementById("generate-yaml-button");
    if (generateButton) {
      generateButton.addEventListener("click", () => this._generateYaml());
    }

    for (const radio of this.shadowRoot.querySelectorAll("input[type='radio']")) {
      radio.addEventListener("change", (event) => {
        const groupIndex = Number(event.target.closest(".group").dataset.groupIndex);
        const subgroupIndex = Number(event.target.closest(".subgroup").dataset.subgroupIndex);
        this._setKeep(groupIndex, subgroupIndex, event.target.value);
      });
    }
  }
}

customElements.define("duplicate-finder-view", DuplicateFinderView);
