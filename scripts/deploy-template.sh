#!/usr/bin/env bash
# Pi-Deployer: Generic deploy script template
#
# Available environment variables (set by pi-deployer):
#   DEPLOYER_PROJECT_NAME  - Project name from projects.yml
#   DEPLOYER_REPO_DIR      - Absolute path to the repo directory
#   DEPLOYER_DEPLOY_MODE   - Deploy mode (docker-compose, systemd, etc.)
#   DEPLOYER_BRANCH        - Expected branch name
#   DEPLOYER_COMMIT_SHA    - Commit SHA (if triggered by webhook)
#   DEPLOYER_COMMIT_AUTHOR - Commit author (if triggered by webhook)
#   DEPLOYER_COMMIT_MESSAGE - Commit message (if triggered by webhook)

set -euo pipefail

echo "=== Deploying ${DEPLOYER_PROJECT_NAME} ==="
echo "Dir: ${DEPLOYER_REPO_DIR}"
echo "Mode: ${DEPLOYER_DEPLOY_MODE}"
echo "Branch: ${DEPLOYER_BRANCH}"

cd "${DEPLOYER_REPO_DIR}"

# Step 1: Pull latest changes
echo "--- git pull ---"
git pull --ff-only

# Step 2: Deploy based on mode
case "${DEPLOYER_DEPLOY_MODE}" in
  docker-compose)
    echo "--- docker compose down ---"
    docker compose down
    echo "--- docker compose up -d ---"
    docker compose up -d --build
    ;;
  systemd)
    SERVICE_NAME="${DEPLOYER_PROJECT_NAME}"
    echo "--- systemctl restart ${SERVICE_NAME} ---"
    sudo systemctl restart "${SERVICE_NAME}"
    ;;
  pull-only)
    echo "--- pull-only mode, done ---"
    ;;
  *)
    echo "Unknown deploy mode: ${DEPLOYER_DEPLOY_MODE}"
    exit 1
    ;;
esac

echo "=== Deploy complete ==="
