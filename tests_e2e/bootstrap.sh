#!/bin/bash
# Write a configuration.yaml with this integration enabled, then start HA.
#
# The integration is YAML-configured, and HA only writes a default
# configuration.yaml on first start - too late for us to add to it. So write it
# ourselves before handing off to the image's real entrypoint.
set -euo pipefail

CONFIG_FILE=/config/configuration.yaml

if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Writing ${CONFIG_FILE}"
    cat >"${CONFIG_FILE}" <<'YAML'
# Loads default set of integrations. Do not remove.
default_config:

statistics_outlier_cleaner:
YAML
elif ! grep -q '^statistics_outlier_cleaner:' "${CONFIG_FILE}"; then
    # The config directory is a named volume, so on a repeat run without `-v`
    # the file is already there.
    echo "Enabling statistics_outlier_cleaner in ${CONFIG_FILE}"
    printf '\nstatistics_outlier_cleaner:\n' >>"${CONFIG_FILE}"
fi

# exec so HA stays PID 1 and signals reach it.
exec /init "$@"
