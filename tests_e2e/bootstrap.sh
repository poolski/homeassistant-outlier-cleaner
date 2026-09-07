#!/bin/bash
# Prepare and launch Home Assistant with statistics_outlier_cleaner enabled.
#
# The official image expects the onboarding wizard to create the first user.
# These tests run unattended, so stand in for it: generate a config, add the
# login user, and mark onboarding done. Everything here is idempotent so
# `npm run up` can be re-run against a persisted config volume.
set -euo pipefail

CONFIG_DIR=/config
CONFIG_FILE="${CONFIG_DIR}/configuration.yaml"
USERNAME="${HASS_USERNAME:-dev}"
PASSWORD="${HASS_PASSWORD:-dev}"

hass --script ensure_config -c "${CONFIG_DIR}"

if ! hass --script auth -c "${CONFIG_DIR}" list | grep -qx "${USERNAME}"; then
    echo "Creating Home Assistant user ${USERNAME}"
    hass --script auth -c "${CONFIG_DIR}" add "${USERNAME}" "${PASSWORD}"
fi

mkdir -p "${CONFIG_DIR}/.storage"
if [[ ! -f "${CONFIG_DIR}/.storage/onboarding" ]]; then
    echo "Marking onboarding done"
    cat > "${CONFIG_DIR}/.storage/onboarding" <<'EOF'
{"data": {"done": ["user", "core_config", "integration"]}, "key": "onboarding", "version": 3}
EOF
fi

if ! grep -q '^statistics_outlier_cleaner:' "${CONFIG_FILE}"; then
    echo "Enabling statistics_outlier_cleaner in configuration.yaml"
    printf '\nstatistics_outlier_cleaner:\n' >> "${CONFIG_FILE}"
fi

echo "Home Assistant version: $(hass --version)"
exec hass -c "${CONFIG_DIR}"
