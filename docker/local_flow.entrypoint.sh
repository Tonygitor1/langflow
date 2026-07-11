#!/usr/bin/env bash
# Entrypoint for local flow execution containers.
#
# 1. Starts Langflow in the background (SQLite mode)
# 2. Waits for health check
# 3. Imports the flow snapshot from FLOW_SNAPSHOT_PATH (default /opt/langflow/flow.json)
# 4. Reports flow_id to the registry callback URL (if set)
# 5. Foregrounds Langflow with --reload on /flows for hot reload

set -euo pipefail

LANGFLOW_HOST="${LANGFLOW_HOST:-0.0.0.0}"
LANGFLOW_PORT="${LANGFLOW_PORT:-7860}"
FLOW_SNAPSHOT_PATH="${FLOW_SNAPSHOT_PATH:-/opt/langflow/flow.json}"
REGISTRY_CALLBACK_URL="${REGISTRY_CALLBACK_URL:-}"

export PATH="/app/.venv/bin:$PATH"

# ── Start Langflow in background ──────────────────────────────────────────────
echo "[entrypoint] Starting Langflow on ${LANGFLOW_HOST}:${LANGFLOW_PORT} ..."
langflow run --host "$LANGFLOW_HOST" --port "$LANGFLOW_PORT" &
LANGFLOW_PID=$!

# ── Wait for health ───────────────────────────────────────────────────────────
# /health_check (no /api/v1 prefix — health_check_router is mounted bare)
# actually verifies DB + chat service; bare /health responds via uvicorn
# before langflow itself is up, so it's not a reliable readiness signal.
#
# This wait is NOT redundant with the k8s startupProbe/readinessProbe in
# deployment.yaml — those gate traffic routing to the pod, this gates
# whether it's safe to call the login/flow-import API below. Budget matches
# the startupProbe's own grace period (30 + 15*30 = 480s) so this doesn't
# give up before k8s would.
echo "[entrypoint] Waiting for Langflow to be healthy ..."
HEALTH_URL="http://${LANGFLOW_HOST}:${LANGFLOW_PORT}/health_check"
for i in $(seq 1 240); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        echo "[entrypoint] Langflow is healthy."
        break
    fi
    if [ "$i" -eq 240 ]; then
        echo "[entrypoint] ERROR: Langflow did not become healthy after 480s"
        exit 1
    fi
    sleep 2
done

# ── Import flow snapshot if present ───────────────────────────────────────────
if [ -f "$FLOW_SNAPSHOT_PATH" ]; then
    echo "[entrypoint] Importing flow snapshot from ${FLOW_SNAPSHOT_PATH} ..."

    # Authenticate
    LANGFLOW_USER="${LANGFLOW_SUPERUSER:-admin}"
    LANGFLOW_PASS="${LANGFLOW_SUPERUSER_PASSWORD:-admin}"

    LOGIN_RESP=$(curl -sf -X POST "http://${LANGFLOW_HOST}:${LANGFLOW_PORT}/api/v1/login" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=${LANGFLOW_USER}&password=${LANGFLOW_PASS}") || {
        echo "[entrypoint] WARNING: Failed to authenticate. Continuing without flow import."
    }

    if [ -n "${LOGIN_RESP:-}" ]; then
        ACCESS_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)

        if [ -n "${ACCESS_TOKEN:-}" ]; then
            # Import the flow
            IMPORT_RESP=$(curl -sf -X POST "http://${LANGFLOW_HOST}:${LANGFLOW_PORT}/api/v1/flows/" \
                -H "Authorization: Bearer ${ACCESS_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "@${FLOW_SNAPSHOT_PATH}") || {
                echo "[entrypoint] WARNING: Failed to import flow snapshot."
            }

            if [ -n "${IMPORT_RESP:-}" ]; then
                FLOW_ID=$(echo "$IMPORT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
                echo "[entrypoint] Flow imported with ID: ${FLOW_ID:-unknown}"

                # Report back to registry if callback URL is set
                if [ -n "${REGISTRY_CALLBACK_URL}" ] && [ -n "${FLOW_ID:-}" ]; then
                    curl -sf -X POST "${REGISTRY_CALLBACK_URL}" \
                        -H "Content-Type: application/json" \
                        -d "{\"flow_id\":\"${FLOW_ID}\",\"agent_id\":\"${AGENT_ID:-}\"}" \
                        > /dev/null 2>&1 || true
                    echo "[entrypoint] Reported flow_id to registry."
                fi
            fi
        fi
    fi
else
    echo "[entrypoint] No flow snapshot found at ${FLOW_SNAPSHOT_PATH}; skipping import."
fi

# ── Bring Langflow to foreground ──────────────────────────────────────────────
echo "[entrypoint] Flow import complete. Bringing Langflow to foreground."
wait "$LANGFLOW_PID"
