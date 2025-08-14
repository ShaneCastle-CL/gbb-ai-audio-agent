#!/bin/bash

# Define the path to your Zsh profile
zshrc_path="$HOME/.zshrc"
bashrc_path="$HOME/.bashrc"

echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$zshrc_path"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$bashrc_path"

cat $HOME/.zshrc
export PATH="$HOME/.local/bin:$PATH"

set -euo pipefail

echo "[post-create] Ensuring Python deps via uv..."
if command -v uv >/dev/null 2>&1; then
	uv sync || true
else
	echo "uv not found; skipping uv sync"
fi

echo "[post-create] Installing Azure Developer CLI (azd) if missing..."
if ! command -v azd >/dev/null 2>&1; then
	curl -fsSL https://aka.ms/install-azd.sh | bash || true
else
	echo "azd already installed"
fi

echo "[post-create] Ensuring Azure CLI extensions and Bicep..."
if command -v az >/dev/null 2>&1; then
	az bicep install || true
	az bicep upgrade || true
else
	echo "Azure CLI not available; Bicep install skipped"
fi

echo "[post-create] Installing Terraform if missing..."
if ! command -v terraform >/dev/null 2>&1; then
	TMP_DIR=$(mktemp -d)
	pushd "$TMP_DIR" >/dev/null
	# HashiCorp Linux x86_64 latest stable (pin can be added if required)
	TERRAFORM_VERSION=${TERRAFORM_VERSION:-1.8.5}
	curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip"
	sudo unzip -o "terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -d /usr/local/bin
	popd >/dev/null
	rm -rf "$TMP_DIR"
else
	echo "Terraform already installed: $(terraform version | head -n1)"
fi

echo "[post-create] Installing frontend dependencies (React/Vite)..."
FRONTEND_DIR="/workspaces/gbb-ai-audio-agent-migration-target/apps/rtagent/frontend"
if [ -d "$FRONTEND_DIR" ]; then
	pushd "$FRONTEND_DIR" >/dev/null
	if command -v npm >/dev/null 2>&1; then
		npm ci || npm install
	else
		echo "npm not found; Node feature may have failed to install"
	fi
	popd >/dev/null
fi

echo "[post-create] Done."