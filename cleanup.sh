#!/usr/bin/env bash
#
# Cleanup script for AI Stack deployment
# Use this to completely remove the stack for a fresh installation
#
# Usage: ./cleanup.sh
#
# This script will:
# 1. Stop and remove all Docker containers
# 2. Remove Docker volumes for persistent data
# 3. Remove custom-built images
## 5. Remove generated configuration files

set -euo pipefail

echo "=== AI Stack Cleanup Script ==="
echo ""

# Ask for confirmation
read -p "Are you sure you want to remove ALL AI Stack data and containers? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted. No changes made."
    exit 0
fi

# Stop and remove all containers
echo "Stopping and removing Docker containers..."
docker compose down --volumes --remove-orphans 2>/dev/null || true
docker container prune -f 2>/dev/null || true

# Remove custom-built images
echo "Removing custom-built images..."
docker rmi -f ai-runs-ui:latest ai-stack-server:latest 2>/dev/null || true

# Remove any leftover Ollama data
rm -rf ./postgres 2>/dev/null || true
docker volume rm postgres_data 2>/dev/null || true

# Remove generated configuration files

# Remove .env if it was generated from example (optional)
read -p "Do you want to remove the .env file as well? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -f .env
    echo ".env file removed."
fi

echo ""
echo "=== Cleanup Complete ==="
echo "You can now perform a fresh installation with:"
echo "  cp .env.example .env"
echo "  docker compose up -d"
echo ""
echo "Note: You may need to manually remove any remaining Docker resources:"
echo "  docker system prune -a --volumes"