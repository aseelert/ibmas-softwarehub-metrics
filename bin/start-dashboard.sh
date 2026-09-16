#!/usr/bin/env bash
# ==============================================================================
# IBM Software Hub Telemetry & Metering Dashboard
# Launcher script with automatic Python virtual environment (venv) activation
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
VENV_DIR="${REPO_ROOT}/.venv"
APP_PY="${REPO_ROOT}/app/server.py"

PORT="${1:-8088}"
HOST="${2:-0.0.0.0}"

cd "${REPO_ROOT}"

# Create virtual environment if not present
if [ ! -d "${VENV_DIR}" ]; then
    echo "==> Creating Python virtual environment at .venv..."
    python3 -m venv "${VENV_DIR}"
fi

# Activate virtual environment
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# Auto-source .env if present
if [ -f "${REPO_ROOT}/.env" ]; then
    echo "==> Loaded environment from .env"
fi

echo "================================================================================"
echo " IBM Software Hub & CPD Usage Metering & Licensing Dashboard"
echo " URL:     http://localhost:${PORT}"
echo " Host:    ${HOST} | Port: ${PORT}"
echo " Python:  $(python3 --version) [${VENV_DIR}]"
echo " PID:     $$"
echo " Press Ctrl+C to terminate"
echo "================================================================================"

exec python3 "${APP_PY}" "${PORT}" "${HOST}"
