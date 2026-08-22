#!/bin/bash
# Prepare and launch Home Assistant with statistics_outlier_cleaner enabled.
#
# The image's `container` entrypoint does setup and launch in one shot, with no
# hook in between. We need one: the integration is YAML-configured, and the
# configuration.yaml it writes does not exist until setup has run. Hence
# setup -> edit -> launch rather than the default `container`.
set -euo pipefail

CONFIG_DIR=/config
CONFIG_FILE="${CONFIG_DIR}/configuration.yaml"

if [[ -n "${HA_VERSION:-}" ]]; then
    echo "Installing homeassistant==${HA_VERSION}"
    uv pip install --quiet "homeassistant==${HA_VERSION}"
fi

# `sudo -E` is not enough: sudoers' secure_path replaces PATH regardless, which
# drops the image's virtualenv and makes `hass` unfindable. The image's own
# CMD (`sudo -E container`) hits this too. Re-inject PATH explicitly.
as_root() {
    sudo -E env "PATH=${PATH}" "$@"
}

as_root container setup

# Idempotent: the config directory is a named volume on repeat runs.
if ! sudo grep -q '^statistics_outlier_cleaner:' "${CONFIG_FILE}"; then
    echo "Enabling statistics_outlier_cleaner in configuration.yaml"
    printf '\nstatistics_outlier_cleaner:\n' | sudo tee -a "${CONFIG_FILE}" >/dev/null
fi

echo "Home Assistant version: $(hass --version 2>/dev/null || echo unknown)"
# exec cannot run a shell function, so spell it out to keep HA as PID 1's child
# and let signals reach it.
exec sudo -E env "PATH=${PATH}" container launch
