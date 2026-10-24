#!/usr/bin/env bash
#
# Start, stop and inspect the panels listed in rigs.conf, side by side.
#
# A rig is one panel, run by run-local.sh with a broker and ports of its own. The
# ports follow from the rig's slot N:
#
#   REST http   2808N      dashboard   2818N
#   REST https  2908N      MQTTS       2888N
#
# (The simulator serves https on the http port + 1000.)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

RIGS_FILE="${RIGS_FILE:-${SCRIPT_DIR}/rigs.conf}"
CONFIG_DIR="${CONFIG_DIR:-${REPO_DIR}/configs}"
LOG_DIR="${LOG_DIR:-${REPO_DIR}/.local/logs}"
HTTP_BASE="${HTTP_BASE:-28080}"
DASHBOARD_BASE="${DASHBOARD_BASE:-28180}"
BROKER_BASE="${BROKER_BASE:-28880}"
READY_TIMEOUT="${READY_TIMEOUT:-90}"

RUN_LOCAL="${SCRIPT_DIR}/run-local.sh"
PID_DIR="${REPO_DIR}/.local/pids"

usage() {
    cat <<EOF
Usage: $(basename "$0") start|stop|restart|status

  start     Start every rig in rigs.conf that is not already running
  stop      Stop every simulator and broker of this checkout
  restart   Stop, then start
  status    Show each rig, its ports and whether it is running

Environment variables:
  RIGS_FILE        Table of rigs (default: rigs.conf beside this script)
  CONFIG_DIR       Directory holding the rigs' YAML (default: ./configs)
  LOG_DIR          One log per rig (default: ./.local/logs)
  HTTP_BASE        http port of slot 0 (default: 28080)
  DASHBOARD_BASE   dashboard port of slot 0 (default: 28180)
  BROKER_BASE      MQTTS port of slot 0 (default: 28880)
  READY_TIMEOUT    Seconds to wait for a rig to answer (default: 90)
  UV_NO_SYNC       Set to 1 to run in the current .venv as it is, without syncing it
                   to the lockfile first
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

# The rig table, comments and blank lines dropped: rig i is RIG_SLOTS[i] running
# RIG_CONFIGS[i].
RIG_SLOTS=()
RIG_CONFIGS=()
load_rigs() {
    local slot config
    [[ -f "${RIGS_FILE}" ]] || die "no rig table at ${RIGS_FILE}"
    while read -r slot config _; do
        [[ -z "${slot}" || "${slot}" == \#* ]] && continue
        [[ "${slot}" =~ ^[0-9]$ ]] || die "${RIGS_FILE}: slot '${slot}' is not a digit 0-9"
        [[ -n "${config}" ]] || die "${RIGS_FILE}: slot ${slot} names no config"
        RIG_SLOTS+=("${slot}")
        RIG_CONFIGS+=("${config}")
    done <"${RIGS_FILE}"
    ((${#RIG_SLOTS[@]} > 0)) || die "${RIGS_FILE} lists no rigs"
}

# The PID of the simulator running *config*, as run-local.sh records it; nothing
# when it is not running.
rig_pid() {
    local config="$1" pid_file pid
    pid_file="${PID_DIR}/simulator-${config%.*}.pid"
    [[ -f "${pid_file}" ]] || return 0
    pid="$(cat "${pid_file}")"
    if kill -0 "${pid}" 2>/dev/null; then
        echo "${pid}"
    fi
}

# Wait until the rig answers on its http port, or its launcher has gone.
wait_ready() {
    local port="$1" launcher="$2" waited=0
    while ((waited < READY_TIMEOUT)); do
        if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}/api/v2/status" 2>/dev/null; then
            return 0
        fi
        kill -0 "${launcher}" 2>/dev/null || return 1
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

start_all() {
    local i slot config pid log http failed=0

    if [[ -z "${UV_NO_SYNC:-}" ]]; then
        # Once, here: run-local.sh would sync at every rig's start, each against the
        # one .venv.
        echo "==> Syncing dependencies..."
        bash "${SCRIPT_DIR}/dev-setup.sh" >/dev/null
    fi
    mkdir -p "${LOG_DIR}"

    # One at a time, each answering before the next starts: the first start writes
    # the certificates and the generated templates the others read.
    for i in "${!RIG_SLOTS[@]}"; do
        slot="${RIG_SLOTS[i]}"
        config="${RIG_CONFIGS[i]}"
        http=$((HTTP_BASE + slot))
        pid="$(rig_pid "${config}")"
        if [[ -n "${pid}" ]]; then
            echo "==> Rig ${slot} ${config}: already running (pid ${pid})"
            continue
        fi
        log="${LOG_DIR}/rig-${slot}-${config%.*}.log"
        echo "==> Rig ${slot} ${config}: starting on http ${http}..."
        UV_NO_SYNC=1 \
            CONFIG_DIR="${CONFIG_DIR}" \
            CONFIG_NAME="${config}" \
            HTTP_PORT="${http}" \
            DASHBOARD_PORT="$((DASHBOARD_BASE + slot))" \
            BROKER_PORT="$((BROKER_BASE + slot))" \
            nohup bash "${RUN_LOCAL}" >"${log}" 2>&1 </dev/null &
        if wait_ready "${http}" "$!"; then
            echo "    ready (pid $(rig_pid "${config}"))"
        else
            echo "    FAILED to answer within ${READY_TIMEOUT}s; see ${log}" >&2
            failed=1
        fi
    done

    return "${failed}"
}

stop_all() {
    bash "${RUN_LOCAL}" --stop
}

show_status() {
    local i slot config pid state
    printf '%-4s %-46s %-6s %-6s %-9s %-6s %s\n' SLOT CONFIG HTTP HTTPS DASHBOARD MQTTS STATE
    for i in "${!RIG_SLOTS[@]}"; do
        slot="${RIG_SLOTS[i]}"
        config="${RIG_CONFIGS[i]}"
        pid="$(rig_pid "${config}")"
        if [[ -n "${pid}" ]]; then
            state="running (pid ${pid})"
        else
            state="stopped"
        fi
        printf '%-4s %-46s %-6s %-6s %-9s %-6s %s\n' \
            "${slot}" "${config}" \
            "$((HTTP_BASE + slot))" "$((HTTP_BASE + slot + 1000))" \
            "$((DASHBOARD_BASE + slot))" "$((BROKER_BASE + slot))" "${state}"
    done
}

[[ $# -eq 1 ]] || {
    usage
    exit 1
}

case "$1" in
    start)
        load_rigs
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        load_rigs
        stop_all
        start_all
        ;;
    status)
        load_rigs
        show_status
        ;;
    -h | --help)
        usage
        ;;
    *)
        usage
        exit 1
        ;;
esac
