#!/bin/bash
# filepath: /Users/jinle/Repos/_AIProjects/migration_staging/gbb-ai-audio-agent-migration-target/devops/scripts/azd/postprovision.sh

# ========================================================================
# 🎯 Azure Developer CLI Post-Provisioning Script
# ========================================================================
# This script runs after Azure resources are provisioned by azd.
# It handles:
# 1. ACS phone number setup (interactive or existing)
# 2. Environment file generation
# 3. Backend service configuration updates
# ========================================================================

set -e  # Exit on error (we'll handle specific failures with || true)

# ========================================================================
# 🔧 Configuration & Setup
# ========================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPERS_DIR="$SCRIPT_DIR/helpers"

# Color codes for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ========================================================================
# 🛠️ Helper Functions
# ========================================================================

log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

log_section() {
    echo ""
    echo -e "${BLUE}$1${NC}"
    echo "$(printf '=%.0s' {1..50})"
    echo ""
}

# Safely get azd environment values
get_azd_env_value() {
    local var_name="$1"
    local default_value="${2:-}"
    local value
    
    value=$(azd env get-value "$var_name" 2>&1 || echo "")
    
    if [[ "$value" == *ERROR* ]] || [ -z "$value" ]; then
        echo "$default_value"
    else
        echo "$value"
    fi
}

# Check if running in interactive mode
is_interactive() {
    [ -t 0 ] && [ -z "${CI:-}" ] && [ -z "${GITHUB_ACTIONS:-}" ]
}

# Validate E.164 phone number format
is_valid_phone_number() {
    [[ "$1" =~ ^\+[0-9]{10,15}$ ]]
}

# ========================================================================
# 🔍 Phone Number Management Functions
# ========================================================================

check_existing_phone_number() {
    local existing_number
    existing_number=$(get_azd_env_value "ACS_SOURCE_PHONE_NUMBER")
    
    if [ -n "$existing_number" ]; then
        log_success "ACS_SOURCE_PHONE_NUMBER is already set: $existing_number"
        return 0
    else
        return 1
    fi
}

prompt_for_phone_number() {
    if ! is_interactive; then
        log_warning "Running in non-interactive mode. Cannot prompt for phone number."
        log_info "To set a phone number later, run: azd env set ACS_SOURCE_PHONE_NUMBER '+1234567890'"
        return 3  # Return 3 for non-interactive skip
    fi
    
    log_info "ACS_SOURCE_PHONE_NUMBER is not defined."
    echo "Options:"
    echo "  1) Enter an existing phone number"
    echo "  2) Provision a new phone number from Azure"
    echo "  3) Skip (configure later)"
    echo ""
    
    read -p "Your choice (1-3): " choice
    
    case "$choice" in
        1)
            read -p "Enter phone number in E.164 format (e.g., +1234567890): " user_phone
            if is_valid_phone_number "$user_phone"; then
                azd env set ACS_SOURCE_PHONE_NUMBER "$user_phone"
                log_success "Set ACS_SOURCE_PHONE_NUMBER to $user_phone"
            else
                log_error "Invalid phone number format"
            fi
            ;;
        2)
            # User wants to provision new number
            provision_new_phone_number || {
                log_warning "Phone number provisioning failed, continuing with other tasks..."
            }
            ;;
        3)
            log_info "Skipping phone number configuration"
            ;;
        *)
            log_error "Invalid choice"
            return 1
            ;;
    esac
}

provision_new_phone_number() {
    log_section "📞 Provisioning New ACS Phone Number"
    
    local acs_endpoint
    acs_endpoint=$(get_azd_env_value "ACS_ENDPOINT")
    
    if [ -z "$acs_endpoint" ]; then
        log_error "ACS_ENDPOINT is not set. Cannot provision phone number."
        return 1
    fi
    
    # Ensure Azure CLI communication extension is installed
    log_info "Checking Azure CLI communication extension..."
    if ! az extension list --query "[?name=='communication']" -o tsv | grep -q communication; then
        log_info "Installing Azure CLI communication extension..."
        az extension add --name communication || {
            log_error "Failed to install communication extension"
            return 1
        }
    fi
    
    # Install required Python packages
    log_info "Installing required Python packages..."
    pip3 install -q azure-identity azure-communication-phonenumbers || {
        log_error "Failed to install required Python packages"
        return 1
    }
    
    # Run the provisioning script
    log_info "Creating new phone number..."
    local phone_number
    phone_number=$(python3 "$HELPERS_DIR/acs_phone_number_manager.py" \
        --endpoint "$acs_endpoint" purchase 2>/dev/null) || {
        log_error "Failed to provision phone number"
        return 1
    }
    
    # Extract clean phone number
    local clean_number
    clean_number=$(echo "$phone_number" | grep -o '+[0-9]\+' | head -1)
    
    if [ -z "$clean_number" ]; then
        log_error "Failed to extract phone number from provisioning output"
        return 1
    fi
    
    # Save to azd environment
    azd env set ACS_SOURCE_PHONE_NUMBER "$clean_number"
    log_success "Successfully provisioned phone number: $clean_number"
    
    # Update backend service
    update_backend_phone_number "$clean_number" || {
        log_warning "Failed to update backend service, but phone number was provisioned"
    }
    
    return 0
}

update_backend_phone_number() {
    local phone_number="$1"
    local resource_group
    local backend_name
    local backend_type=""
    
    resource_group=$(get_azd_env_value "AZURE_RESOURCE_GROUP")
    
    if [ -z "$resource_group" ]; then
        log_warning "AZURE_RESOURCE_GROUP not set. Cannot update backend."
        return 1
    fi
    
    # Check for container app
    backend_name=$(get_azd_env_value "BACKEND_CONTAINER_APP_NAME")
    if [ -n "$backend_name" ]; then
        backend_type="containerapp"
    else
        # Check for app service
        backend_name=$(get_azd_env_value "BACKEND_APP_SERVICE_NAME")
        if [ -n "$backend_name" ]; then
            backend_type="appservice"
        fi
    fi
    
    if [ -z "$backend_type" ]; then
        log_warning "No backend service found to update"
        return 1
    fi
    
    log_info "Updating $backend_type: $backend_name"
    
    case "$backend_type" in
        "containerapp")
            az containerapp update \
                --name "$backend_name" \
                --resource-group "$resource_group" \
                --set-env-vars "ACS_SOURCE_PHONE_NUMBER=$phone_number" \
                --output none || return 1
            ;;
        "appservice")
            az webapp config appsettings set \
                --name "$backend_name" \
                --resource-group "$resource_group" \
                --settings "ACS_SOURCE_PHONE_NUMBER=$phone_number" \
                --output none || return 1
            ;;
    esac
    
    log_success "Updated backend service with phone number"
    return 0
}

# ========================================================================
# 🚀 Main Execution
# ========================================================================

main() {
    log_section "🚀 Starting Post-Provisioning Script"
    
    # Step 1: Handle phone number configuration
    log_section "📱 Configuring ACS Phone Number"
    
    if ! check_existing_phone_number; then
        prompt_for_phone_number
        local prompt_result=$?
        
        case $prompt_result in
            0)
                # Phone number was set successfully
                log_success "Phone number configured"
                ;;
            1)
                # Error occurred
                log_warning "Phone number configuration failed"
                ;;
        esac
    fi
    
    # Step 2: Generate environment files (always runs)
    log_section "📄 Generating Environment Configuration Files"
    
    local env_name
    local env_file
    env_name=$(get_azd_env_value "AZURE_ENV_NAME" "dev")
    env_file=".env.${env_name}"
    
    if [ -f "$HELPERS_DIR/generate-env.sh" ]; then
        log_info "Generating environment file: $env_file"
        "$HELPERS_DIR/generate-env.sh" "$env_name" "$env_file" || {
            log_error "Environment file generation failed"
            # Don't exit - this is critical but we want to show summary
        }
        
        if [ -f "$env_file" ]; then
            local var_count
            var_count=$(grep -c '^[A-Z]' "$env_file" 2>/dev/null || echo "0")
            log_success "Generated environment file with $var_count variables"
        fi
    else
        log_error "generate-env.sh not found at: $HELPERS_DIR/generate-env.sh"
    fi
    
    # Step 3: Summary
    log_section "🎯 Post-Provisioning Summary"
    
    echo "📋 Generated Files:"
    [ -f "$env_file" ] && echo "  ✓ ${env_file} (Backend environment configuration)"
    echo ""
    
    echo "🔧 Next Steps:"
    echo "  1. Review the environment file: cat ${env_file}"
    echo "  2. Source the environment: source ${env_file}"
    echo "  3. Test your application"
    
    local phone_status
    phone_status=$(get_azd_env_value "ACS_SOURCE_PHONE_NUMBER")
    if [ -z "$phone_status" ]; then
        echo ""
        echo "⚠️  Note: No phone number configured. To add one later:"
        echo "     azd env set ACS_SOURCE_PHONE_NUMBER '+1234567890'"
    fi
    
    echo ""
    log_success "Post-provisioning complete!"
}

# Run main function
main "$@"