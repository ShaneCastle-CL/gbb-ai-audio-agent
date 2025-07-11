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
        
        echo "Setting Terraform variables from Azure environment..."
        
        # Define required tfvars mappings (environment_variable:tfvar_key)
        declare -A REQUIRED_TFVARS=(
            ["AZURE_ENV_NAME"]="environment_name"
            ["AZURE_LOCATION"]="location"
        )
        
        TFVARS_FILE="./infra-tf/main.tfvars.json"
        
        # Check if update is needed
        needs_tfvars_update() {
            if [ ! -f "$TFVARS_FILE" ]; then
                echo "Creating new $TFVARS_FILE..."
                return 0
            fi
            
            # Check each required variable
            for env_var in "${!REQUIRED_TFVARS[@]}"; do
                tfvar_key="${REQUIRED_TFVARS[$env_var]}"
                
                if ! grep -q "\"$tfvar_key\"" "$TFVARS_FILE"; then
                    echo "Missing required variable '$tfvar_key' in $TFVARS_FILE"
                    return 0
                fi
                
                # Check if value is empty
                value=$(jq -r ".$tfvar_key // empty" "$TFVARS_FILE" 2>/dev/null)
                if [ -z "$value" ] || [ "$value" = "null" ]; then
                    echo "Empty value for '$tfvar_key' in $TFVARS_FILE"
                    return 0
                fi
            done
            
            echo "$TFVARS_FILE already contains all required variables with values"
            return 1
        }
        
        # Update tfvars file if needed
        if needs_tfvars_update; then
            echo "Updating $TFVARS_FILE..."
            
            # Start with existing file or empty object
            if [ -f "$TFVARS_FILE" ]; then
                base_json=$(cat "$TFVARS_FILE")
            else
                base_json="{}"
            fi
            
            # Build jq arguments for all required variables
            jq_args=()
            for env_var in "${!REQUIRED_TFVARS[@]}"; do
                tfvar_key="${REQUIRED_TFVARS[$env_var]}"
                env_value="${!env_var}"
                
                if [ -z "$env_value" ]; then
                    echo "Warning: Environment variable $env_var is not set"
                    continue
                fi
                
                jq_args+=(--arg "$tfvar_key" "$env_value")
            done
            
            # Build jq expression to set all variables
            jq_expr=". + {"
            first=true
            for env_var in "${!REQUIRED_TFVARS[@]}"; do
                tfvar_key="${REQUIRED_TFVARS[$env_var]}"
                if [ ! -z "${!env_var}" ]; then
                    if [ "$first" = false ]; then
                        jq_expr+=", "
                    fi
                    jq_expr+="\"$tfvar_key\": \$$tfvar_key"
                    first=false
                fi
            done
            jq_expr+="}"
            
            # Apply updates
            echo "$base_json" | jq "${jq_args[@]}" "$jq_expr" > "$TFVARS_FILE"
            echo "  tfvars file updated: $TFVARS_FILE"
        fi
        ;;
    *)
        echo "Error: Invalid provider '$PROVIDER'. Must be 'bicep' or 'terraform'"
        usage
        ;;
esac