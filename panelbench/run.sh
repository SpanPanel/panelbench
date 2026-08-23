#!/usr/bin/env bash
set -euo pipefail

# Import s6-overlay container environment so Supervisor-injected vars
# (SUPERVISOR_TOKEN, etc.) are visible to this process tree.
if [ -d /run/s6/container_environment ]; then
    for _f in /run/s6/container_environment/*; do
        [ -f "$_f" ] || continue
        export "$(basename "$_f")=$(cat "$_f")"
    done
    unset _f
fi

# Read add-on options from standard HA location
OPTIONS_FILE="/data/options.json"

TICK_INTERVAL=$(jq -r '.tick_interval' "${OPTIONS_FILE}")
LOG_LEVEL=$(jq -r '.log_level' "${OPTIONS_FILE}")
DASHBOARD_ENABLED=$(jq -r '.dashboard_enabled' "${OPTIONS_FILE}")
BASE_HTTP_PORT=$(jq -r '.base_http_port // 8081' "${OPTIONS_FILE}")

# Auto-detect host IP for TLS cert SAN.
# Inside a bridge-networked container the default gateway is the host.
# Strip control characters — some container ip implementations emit trailing
# non-printables that would break Python string literals or cert generation.
ADVERTISE_ADDRESS=$(ip route | awk '/default/ { print $3 }' | tr -d '[:cntrl:]' || true)
export ADVERTISE_ADDRESS
export CERT_DIR="/data/certs"
export BROKER_USERNAME="span"
export BROKER_PASSWORD="sim-password"

# PanelBench owns this directory outright. It used to be /config/span_simulator,
# which the flat SPAN Panel Simulator add-on also writes to and still does -- the
# slug changed in the rename and the config path did not. Two add-ons publishing
# incompatible schemas shared one config store, and because seeding only ever
# creates a file that is missing, whichever add-on got there first decided what
# the other one loaded, permanently. A host that had run the flat simulator handed
# PanelBench a flat-era default_MAIN_40.yaml with no `grid_forming` and no
# `islandable`, so the BESS published no MID and nothing said why.
LEGACY_CONFIG_DIR="/config/span_simulator"
CONFIG_DIR="/config/panelbench"
mkdir -p "${CONFIG_DIR}"

# Seed the shipped defaults, still only where a file is missing, so an edited
# config is never overwritten. The difference is that this directory starts empty
# on an upgrade, so the shipped defaults actually land.
for src in /app/configs/*.yaml /app/configs/*.yml; do
    [ -f "${src}" ] || continue
    dest="${CONFIG_DIR}/$(basename "${src}")"
    if [ ! -f "${dest}" ]; then
        cp "${src}" "${dest}"
        echo "Seeded config: $(basename "${src}")"
    fi
done

# Nothing is migrated from the old directory. A file there may have been written
# by either add-on and nothing distinguishes them, so copying would reintroduce
# exactly the defect this removes. Say where the old panels are instead of
# leaving them to be discovered.
if [ -d "${LEGACY_CONFIG_DIR}" ]; then
    # `set -e` plus `pipefail` would abort the whole start-up if find tripped on a
    # permission, so a failure here means "nothing to report", not "die".
    legacy_count=$(find "${LEGACY_CONFIG_DIR}" -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) 2>/dev/null | wc -l | tr -d ' ') || legacy_count=0
    if [ "${legacy_count}" != "0" ]; then
        echo "NOTE: ${legacy_count} config(s) remain in ${LEGACY_CONFIG_DIR}, which PanelBench no longer reads."
        echo "NOTE: PanelBench now uses ${CONFIG_DIR}. Copy a panel across by hand if you want it back,"
        echo "NOTE: but check it declares 'grid_forming' under 'bess:' -- configs written by the flat"
        echo "NOTE: simulator predate that key, and without it a battery publishes no MID."
    fi
fi

mkdir -p "${CERT_DIR}"

# Generate TLS certs
python3 -c "
import os
from panelbench.certs import generate_certificates
from pathlib import Path
addr = os.environ.get('ADVERTISE_ADDRESS') or None
generate_certificates(Path(os.environ['CERT_DIR']), advertise_address=addr)
"

chmod 644 "${CERT_DIR}"/*.crt "${CERT_DIR}"/*.key

# Set up Mosquitto credentials
mosquitto_passwd -b -c /app/mosquitto/passwd "${BROKER_USERNAME}" "${BROKER_PASSWORD}"
chmod 644 /app/mosquitto/passwd

# Generate Mosquitto config with correct cert paths
cat > /app/mosquitto/mosquitto.conf <<CONF
listener 18883
cafile ${CERT_DIR}/ca.crt
certfile ${CERT_DIR}/server.crt
keyfile ${CERT_DIR}/server.key
require_certificate false

allow_anonymous false
password_file /app/mosquitto/passwd

persistence false

log_dest stdout
log_type warning
log_type error
log_type notice
CONF

# Start Mosquitto
mosquitto -c /app/mosquitto/mosquitto.conf -d
sleep 1

# Build simulator CLI arguments
ARGS=(
    --config-dir "${CONFIG_DIR}"
    --tick-interval "${TICK_INTERVAL}"
    --log-level "${LOG_LEVEL}"
    --base-http-port "${BASE_HTTP_PORT}"
)

if [ "${DASHBOARD_ENABLED}" = "true" ]; then
    ARGS+=(--dashboard-port 18080)
fi

exec python3 -m panelbench "${ARGS[@]}"
