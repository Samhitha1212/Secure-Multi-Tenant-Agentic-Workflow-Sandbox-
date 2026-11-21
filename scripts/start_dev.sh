#!/usr/bin/env bash
set -Eeuo pipefail

# Project-specific development environment startup.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CONFIG="$HOME/.sandbox.toml"
UV="$HOME/.local/bin/uv"

NETWORK_NAME="m2-sandbox-isolated-test"
NETWORK_SUBNET="172.30.0.0/24"
SERVER_URL="http://127.0.0.1:8080"
HEALTH_URL="$SERVER_URL/health"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

echo "========================================"
echo " Secure Agentic Sandbox - Dev Startup"
echo "========================================"

# 1. Check required tools and configuration.
for cmd in docker curl ip iptables python3 pgrep; do
    command -v "$cmd" >/dev/null 2>&1 || die "Missing command: $cmd"
done

[[ -f "$CONFIG" ]] || die "OpenSandbox config not found: $CONFIG"
[[ -x "$UV" ]] || die "uv executable not found: $UV"

# 2. Validate the expected OpenSandbox configuration.
python3 - "$CONFIG" <<'PY'
import sys
import tomllib
from pathlib import Path

config = tomllib.loads(Path(sys.argv[1]).read_text())

checks = {
    "server.host": (config.get("server", {}).get("host"), "127.0.0.1"),
    "server.port": (config.get("server", {}).get("port"), 8080),
    "runtime.type": (config.get("runtime", {}).get("type"), "docker"),
    "docker.network_mode": (
        config.get("docker", {}).get("network_mode"),
        "m2-sandbox-isolated-test",
    ),
    "docker.publish_host": (
        config.get("docker", {}).get("publish_host"),
        "127.0.0.1",
    ),
    "docker.pids_limit": (config.get("docker", {}).get("pids_limit"), 256),
    "secure_runtime.type": (
        config.get("secure_runtime", {}).get("type"),
        "gvisor",
    ),
    "secure_runtime.docker_runtime": (
        config.get("secure_runtime", {}).get("docker_runtime"),
        "runsc",
    ),
}

failed = False
for name, (actual, expected) in checks.items():
    if actual != expected:
        print(f"FAIL: {name}: expected {expected!r}, got {actual!r}")
        failed = True

if failed:
    sys.exit(1)

print("OpenSandbox configuration verified.")
PY

# 3. Check Docker access without sudo.
if ! docker info >/dev/null 2>&1; then
    die "Cannot access Docker. Check Docker is running and your user has Docker group access. You may need to log out and log in again, or run 'newgrp docker'."
fi
echo "[OK] Docker is accessible."

# 4. Create the isolated network if it is missing.
if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "Creating isolated Docker network..."

    docker network create \
        --driver bridge \
        --internal \
        --subnet "$NETWORK_SUBNET" \
        --opt com.docker.network.bridge.enable_icc=false \
        "$NETWORK_NAME" >/dev/null
fi

# 5. Verify the network configuration.
INTERNAL="$(docker network inspect -f '{{.Internal}}' "$NETWORK_NAME")"
ICC="$(docker network inspect -f \
    '{{index .Options "com.docker.network.bridge.enable_icc"}}' \
    "$NETWORK_NAME")"
SUBNET="$(docker network inspect -f \
    '{{(index .IPAM.Config 0).Subnet}}' \
    "$NETWORK_NAME")"
NETWORK_ID="$(docker network inspect -f '{{.Id}}' "$NETWORK_NAME")"

[[ "$INTERNAL" == "true" ]] || die "Docker network is not internal."
[[ "$ICC" == "false" ]] || die "Inter-container communication is not disabled."
[[ "$SUBNET" == "$NETWORK_SUBNET" ]] || die "Unexpected Docker network subnet: $SUBNET"

BRIDGE="br-${NETWORK_ID:0:12}"

ip link show "$BRIDGE" >/dev/null 2>&1 ||
    die "Expected Docker bridge interface not found: $BRIDGE"

echo "[OK] Isolated network: $NETWORK_NAME"
echo "[OK] Bridge interface: $BRIDGE"

# 6. Install the host firewall rule if it is not already present.
echo
echo "Checking host firewall rule..."
sudo -v

if sudo iptables -C INPUT \
    -i "$BRIDGE" \
    -s "$NETWORK_SUBNET" \
    -m conntrack --ctstate NEW \
    -j DROP 2>/dev/null; then

    echo "[OK] Host isolation firewall rule already exists."
else
    sudo iptables -I INPUT 1 \
        -i "$BRIDGE" \
        -s "$NETWORK_SUBNET" \
        -m conntrack --ctstate NEW \
        -j DROP

    echo "[OK] Host isolation firewall rule installed."
fi

# 7. Avoid starting a duplicate OpenSandbox server.
if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    echo
    echo "[OK] OpenSandbox is already running."
    echo "Health endpoint: $HEALTH_URL"
    exit 0
fi

if pgrep -af "opensandbox-server.*--config $CONFIG" >/dev/null; then
    die "An OpenSandbox process exists but its health endpoint is unavailable. Inspect the existing server before starting another."
fi

# 8. Start OpenSandbox in the foreground.
echo
echo "Starting OpenSandbox 1.2.0..."
echo "Config: $CONFIG"
echo "Runtime: Docker + gVisor (runsc)"
echo "Network: $NETWORK_NAME"
echo "Published ports: 127.0.0.1"
echo
echo "Keep this terminal open while using the project."
echo "Press Ctrl+C to stop OpenSandbox."
echo "========================================"

exec "$UV" tool uvx \
    --from opensandbox-server==1.2.0 \
    opensandbox-server \
    --config "$CONFIG"
