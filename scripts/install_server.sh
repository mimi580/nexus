#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 server for NEXUS. Run as root:
#   curl -fsSL https://raw.githubusercontent.com/mimi580/nexus/main/scripts/install_server.sh | bash
# (or copy the file over and run it). Afterwards follow docs/USER_GUIDE.md, "Deploying to a server".
set -euo pipefail
apt-get update
apt-get install -y ca-certificates curl git ufw
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
id -u nexus >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker nexus
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
mkdir -p /opt/nexus && chown nexus:nexus /opt/nexus
echo
echo "Server ready. Next, as the nexus user:"
echo "  sudo -iu nexus"
echo "  git clone https://github.com/mimi580/nexus.git /opt/nexus && cd /opt/nexus"
echo "  cp .env.example .env && python3 scripts/generate_secrets.py >> .env && nano .env"
echo "  docker compose up -d --build"
