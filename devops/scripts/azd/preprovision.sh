#!/bin/bash
# filepath: /Users/jinle/Repos/_AIProjects/migration_staging/gbb-ai-audio-agent-migration-target/devops/scripts/azd/preprovision.sh

# ========================================================================
# 🎯 Azure Developer CLI Pre-Provisioning Script
# ========================================================================
# This script runs before Azure resources are provisioned by azd.
# It handles provider-specific setup (Bicep or Terraform)
#
# CI/CD Mode: Automatically detected via CI, GITHUB_ACTIONS, or AZD_SKIP_INTERACTIVE
# ========================================================================

# Check for CI/CD mode
SKIP_INTERACTIVE="${AZD_SKIP_INTERACTIVE:-false}"
CI_MODE="${CI:-false}"
GITHUB_ACTIONS_MODE="${GITHUB_ACTIONS:-false}"

# Auto-detect CI/CD environments
if [ "$CI_MODE" = "true" ] || [ "$GITHUB_ACTIONS_MODE" = "true" ] || [ "$SKIP_INTERACTIVE" = "true" ]; then
    INTERACTIVE_MODE=false
    echo "🤖 CI/CD mode detected - running non-interactively"
else
    INTERACTIVE_MODE=true
fi

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

# Log helper
log_info() {
    echo "ℹ️  $1"
}

log_success() {
    echo "✅ $1"
}

log_warning() {
    echo "⚠️  $1"
}

# Validate the provider argument
case "$PROVIDER" in
    "bicep")
        echo "Bicep deployment detected"
        
        # Call ssl-preprovision.sh from helpers directory
        SSL_PREPROVISION_SCRIPT="$SCRIPT_DIR/helpers/ssl-preprovision.sh"
        if [ -f "$SSL_PREPROVISION_SCRIPT" ]; then
            if [ "$INTERACTIVE_MODE" = "false" ]; then
                log_info "CI/CD mode: Checking for SSL certificates..."
                # In CI/CD mode, check if certificates exist or are provided via env vars
                if [ -n "${SSL_CERT_BASE64:-}" ] && [ -n "${SSL_KEY_BASE64:-}" ]; then
                    log_info "Using SSL certificates from environment variables"
                    # Decode and save certificates
                    echo "$SSL_CERT_BASE64" | base64 -d > "$SCRIPT_DIR/helpers/ssl-cert.pem"
                    echo "$SSL_KEY_BASE64" | base64 -d > "$SCRIPT_DIR/helpers/ssl-key.pem"
                    log_success "SSL certificates configured from environment"
                else
                    log_warning "No SSL certificates found in CI/CD mode"
                    log_info "Set SSL_CERT_BASE64 and SSL_KEY_BASE64 environment variables"
                fi
            else
                echo "Running SSL pre-provisioning setup..."
                bash "$SSL_PREPROVISION_SCRIPT"
            fi
        else
            echo "Error: ssl-preprovision.sh not found at $SSL_PREPROVISION_SCRIPT"
            if [ "$INTERACTIVE_MODE" = "false" ]; then
                log_warning "Continuing without SSL setup in CI/CD mode"
            else
                exit 1
            fi
        fi
        ;;
        
    "terraform")
        echo "Terraform deployment detected"
        echo "Running Terraform Remote State initialization..."
        
        # Call tf-init.sh from helpers directory
        TF_INIT_SCRIPT="$SCRIPT_DIR/helpers/tf-init.sh"
        if [ -f "$TF_INIT_SCRIPT" ]; then
            # Pass CI/CD mode flag to tf-init.sh
            if [ "$INTERACTIVE_MODE" = "false" ]; then
                export TF_INIT_SKIP_INTERACTIVE=true
            fi
            bash "$TF_INIT_SCRIPT"
        else
            log_warning "tf-init.sh not found at $TF_INIT_SCRIPT"
        fi
        
        # Set terraform variables through environment exports and tfvars file
        echo "Setting Terraform variables from Azure environment..."
        export TF_VAR_environment_name="$AZURE_ENV_NAME"
        export TF_VAR_location="$AZURE_LOCATION"

        # Validate required variables
        if [ -z "$AZURE_ENV_NAME" ]; then
            echo "Error: AZURE_ENV_NAME environment variable is not set"
            exit 1
        fi

        if [ -z "$AZURE_LOCATION" ]; then
            echo "Error: AZURE_LOCATION environment variable is not set"
            exit 1
        fi
        
        # Get optional ACS phone number from AZD environment and cleanse error output
        ACS_SOURCE_PHONE_NUMBER_RAW=$(azd env get-value ACS_SOURCE_PHONE_NUMBER 2>&1)
        if [[ "$ACS_SOURCE_PHONE_NUMBER_RAW" == *"not found"* || "$ACS_SOURCE_PHONE_NUMBER_RAW" == *"No value"* ]]; then
            # In CI/CD mode, check environment variable
            if [ "$INTERACTIVE_MODE" = "false" ] && [ -n "${ACS_SOURCE_PHONE_NUMBER:-}" ]; then
                ACS_SOURCE_PHONE_NUMBER="$ACS_SOURCE_PHONE_NUMBER"
                log_info "Using ACS phone number from environment variable"
            else
                ACS_SOURCE_PHONE_NUMBER=""
            fi
        else
            ACS_SOURCE_PHONE_NUMBER="$ACS_SOURCE_PHONE_NUMBER_RAW"
        fi
        
        # Generate tfvars.json
        TFVARS_FILE="infra/terraform/main.tfvars.json"
        echo "Generating $TFVARS_FILE..."
        
        # Build JSON content dynamically
        JSON_CONTENT="{
  \"environment_name\": \"$AZURE_ENV_NAME\",
  \"location\": \"$AZURE_LOCATION\""
        
        if [ -n "$ACS_SOURCE_PHONE_NUMBER" ]; then
            JSON_CONTENT="$JSON_CONTENT,
  \"acs_source_phone_number\": \"$ACS_SOURCE_PHONE_NUMBER\""
        fi
        
        JSON_CONTENT="$JSON_CONTENT
}"
        
        # Write to file
        echo "$JSON_CONTENT" > "$TFVARS_FILE"

        # Display configuration summary
        echo ""
        log_success "Terraform variables configured:"
        echo "   Environment: $AZURE_ENV_NAME"
        echo "   Location: $AZURE_LOCATION"
        if [ -n "$ACS_SOURCE_PHONE_NUMBER" ]; then
            echo "   ACS Phone: $ACS_SOURCE_PHONE_NUMBER"
        else
            echo "   ACS Phone: null (not set)"
        fi
        echo "   Config file: $TFVARS_FILE"
        
        if [ "$INTERACTIVE_MODE" = "false" ]; then
            echo ""
            log_info "CI/CD mode: No interactive prompts"
        fi
        ;;
        
    *)
        echo "Error: Invalid provider '$PROVIDER'. Must be 'bicep' or 'terraform'"
        usage
        ;;
esac

log_success "Pre-provisioning complete!"