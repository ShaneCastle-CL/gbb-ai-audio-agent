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
        # Set terraform variables through environment exports
        echo "Setting Terraform variables from Azure environment..."
        export TF_VAR_environment_name="$AZURE_ENV_NAME"
        export TF_VAR_location="$AZURE_LOCATION"

        if [ -z "$AZURE_ENV_NAME" ]; then
            echo "Warning: AZURE_ENV_NAME environment variable is not set"
        fi

        if [ -z "$AZURE_LOCATION" ]; then
            echo "Warning: AZURE_LOCATION environment variable is not set"
        fi

        echo "Terraform variables configured:"
        echo "  TF_VAR_environment_name=$TF_VAR_environment_name"
        echo "  TF_VAR_location=$TF_VAR_location"
        ;;
    *)
        echo "Error: Invalid provider '$PROVIDER'. Must be 'bicep' or 'terraform'"
        usage
        ;;
esac