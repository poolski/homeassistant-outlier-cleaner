#!/usr/bin/env bash
# Create the admin user and clear onboarding, so the browser tests can log in.
#
# The official image ships no bootstrap for this, and pre-seeding .storage files
# would tie the suite to whatever those files look like in one HA version, which
# is the opposite of what we want. The onboarding API is a stable contract, so
# drive that instead.
#
# Idempotent: re-running against an already onboarded instance is a no-op.
set -euo pipefail

BASE="http://localhost:${HA_PORT:-8123}"
CLIENT_ID="${BASE}/"
USERNAME="${HASS_USERNAME:-dev}"
PASSWORD="${HASS_PASSWORD:-dev}"

# Anonymous, so it works before a user exists.
remaining() {
    curl -fsS "${BASE}/api/onboarding" |
        python3 -c 'import json,sys; print(" ".join(s["step"] for s in json.load(sys.stdin) if not s["done"]))'
}

steps=$(remaining)
if [[ -z "${steps}" ]]; then
    echo "Already onboarded"
    exit 0
fi
echo "Onboarding steps outstanding: ${steps}"

if [[ " ${steps} " == *" user "* ]]; then
    echo "Creating user ${USERNAME}"
    auth_code=$(
        curl -fsS -X POST "${BASE}/api/onboarding/users" \
            -H 'Content-Type: application/json' \
            -d "$(printf '{"client_id":"%s","name":"Dev","username":"%s","password":"%s","language":"en"}' \
                "${CLIENT_ID}" "${USERNAME}" "${PASSWORD}")" |
            python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_code"])'
    )
else
    # A user already exists, so log in the normal way to get a code.
    echo "User exists; authenticating as ${USERNAME}"
    flow=$(
        curl -fsS -X POST "${BASE}/auth/login_flow" \
            -H 'Content-Type: application/json' \
            -d "$(printf '{"client_id":"%s","handler":["homeassistant",null],"redirect_uri":"%s"}' \
                "${CLIENT_ID}" "${CLIENT_ID}")" |
            python3 -c 'import json,sys; print(json.load(sys.stdin)["flow_id"])'
    )
    auth_code=$(
        curl -fsS -X POST "${BASE}/auth/login_flow/${flow}?client_id=${CLIENT_ID}" \
            -H 'Content-Type: application/json' \
            -d "$(printf '{"client_id":"%s","username":"%s","password":"%s"}' \
                "${CLIENT_ID}" "${USERNAME}" "${PASSWORD}")" |
            python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])'
    )
fi

token=$(
    curl -fsS -X POST "${BASE}/auth/token" \
        -d "grant_type=authorization_code" \
        -d "code=${auth_code}" \
        -d "client_id=${CLIENT_ID}" |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)

# The remaining steps vary by HA version, so drive whatever is left rather than a
# fixed list. A step this HA does not have simply 404s, which is not a failure.
for step in $(remaining); do
    [[ "${step}" == "user" ]] && continue
    echo "Completing onboarding step: ${step}"
    body='{}'
    if [[ "${step}" == "integration" ]]; then
        body=$(printf '{"client_id":"%s","redirect_uri":"%s"}' "${CLIENT_ID}" "${CLIENT_ID}")
    fi
    code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/onboarding/${step}" \
        -H "Authorization: Bearer ${token}" \
        -H 'Content-Type: application/json' \
        -d "${body}")
    case "${code}" in
    2*) ;;
    404) echo "  step not present in this HA version, skipping" ;;
    *) echo "  unexpected status ${code}" >&2 ;;
    esac
done

left=$(remaining)
if [[ -n "${left}" ]]; then
    echo "Onboarding incomplete, still outstanding: ${left}" >&2
    exit 1
fi
echo "Onboarding complete"
