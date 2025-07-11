#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 <provider>"
    echo "  provider: bicep or terraform"
    exit 1
}

# Check if argument is provided
if [ $# -ne 1 ]; then
    echo "Error: Provider argument is required"
    usage
fi

PROVIDER="$1"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate the provider argument
case "$PROVIDER" in
    "bicep")
        echo "Bicep deployment detected"
        # Call ssl-preprovision.sh from helpers directory
        SSL_PREPROVISION_SCRIPT="$SCRIPT_DIR/helpers/ssl-preprovision.sh"
        if [ -f "$SSL_PREPROVISION_SCRIPT" ]; then
            echo "Running SSL pre-provisioning setup..."
            bash "$SSL_PREPROVISION_SCRIPT"
        else
            echo "Error: ssl-preprovision.sh not found at $SSL_PREPROVISION_SCRIPT"
            exit 1
        fi
        ;;
    "terraform")
        echo "Terraform deployment detected"
        # Call tf-init.sh from helpers directory
        TF_INIT_SCRIPT="$SCRIPT_DIR/helpers/tf-init.sh"
        if [ -f "$TF_INIT_SCRIPT" ]; then
            echo "Running Terraform Remote State initialization..."
            bash "$TF_INIT_SCRIPT"
        else
            echo "Error: tf-init.sh not found at $TF_INIT_SCRIPT"
            exit 1
        fi
        ;;
    *)
        echo "Error: Invalid provider '$PROVIDER'. Must be 'bicep' or 'terraform'"
        usage
        ;;
esac