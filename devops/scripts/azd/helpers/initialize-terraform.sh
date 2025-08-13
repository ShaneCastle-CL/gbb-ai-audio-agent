#!/bin/bash

# ========================================================================
# 🏗️ Terraform Remote State Storage Account Setup
# ========================================================================
# This script creates Azure Storage Account for Terraform remote state
# using fully Entra-backed authentication.

set -euo pipefail

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Helper functions
log_info() { echo -e "${BLUE}ℹ️  [INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}✅ [SUCCESS]${NC} $*"; }
log_warning() { echo -e "${YELLOW}⚠️  [WARNING]${NC} $*"; }
log_error() { echo -e "${RED}❌ [ERROR]${NC} $*" >&2; }

# Check dependencies
check_dependencies() {
    local deps=("az" "azd")
    for cmd in "${deps[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            log_error "Missing required command: $cmd"
            exit 1
        fi
    done
    
    if ! az account show &> /dev/null; then
        log_error "Not logged in to Azure. Please run 'az login' first."
        exit 1
    fi
}

# Get azd environment variable value
get_azd_env() {
    azd env get-value "$1" 2>/dev/null || echo ""
}

# Check if storage account exists and is accessible
storage_exists() {
    local account="$1"
    local rg="$2"
    az storage account show --name "$account" --resource-group "$rg" &> /dev/null
}

# Generate unique resource names
generate_names() {
    local env_name="${1:-tfdev}"
    local sub_id="$2"
    local suffix=$(echo "${sub_id}${env_name}" | sha256sum | cut -c1-8)
    
    echo "tfstate${suffix}" # storage account
    echo "tfstate" # container
    echo "rg-tfstate-${env_name}-${suffix}" # resource group
}

# Create storage resources
create_storage() {
    local storage_account="$1"
    local container="$2"
    local resource_group="$3"
    local location="${4:-eastus2}"
    
    # Create resource group
    if ! az group show --name "$resource_group" &> /dev/null; then
        log_info "Creating resource group: $resource_group"
        az group create --name "$resource_group" --location "$location" --output none
    fi
    
    # Create storage account
    if ! storage_exists "$storage_account" "$resource_group"; then
        log_info "Creating storage account: $storage_account"
        az storage account create \
            --name "$storage_account" \
            --resource-group "$resource_group" \
            --location "$location" \
            --sku Standard_LRS \
            --kind StorageV2 \
            --allow-blob-public-access false \
            --min-tls-version TLS1_2 \
            --output none
            
        # Enable versioning and change feed (best-effort)
        # Some Azure CLI versions/extensions may hit InvalidApiVersionParameter; do not fail setup.
        if ! az storage account blob-service-properties update \
            --account-name "$storage_account" \
            --resource-group "$resource_group" \
            --enable-versioning true \
            --enable-change-feed true \
            --output none 2>/tmp/blob_props_err.txt; then
            log_warning "Could not update blob service properties (versioning/change feed)."
            if grep -q "InvalidApiVersionParameter" /tmp/blob_props_err.txt; then
                log_warning "Azure API version not supported by your CLI for this operation. Skipping this step."
                log_warning "You can enable Versioning and Change Feed later in the Azure Portal under Storage Account > Data management."
            else
                log_warning "Reason: $(tr -d '\n' < /tmp/blob_props_err.txt)"
            fi
        fi
    fi
    
    # Create container
    if ! az storage container show \
        --name "$container" \
        --account-name "$storage_account" \
        --auth-mode login &> /dev/null; then
        log_info "Creating storage container: $container"
        az storage container create \
            --name "$container" \
            --account-name "$storage_account" \
            --auth-mode login \
            --output none
    fi
    
    # Assign permissions
    local user_id=$(az ad signed-in-user show --query id -o tsv)
    local storage_id=$(az storage account show \
        --name "$storage_account" \
        --resource-group "$resource_group" \
        --query id -o tsv)
        
    if ! az role assignment list \
        --assignee "$user_id" \
        --scope "$storage_id" \
        --role "Storage Blob Data Contributor" \
        --query "length(@)" -o tsv | grep -q "1"; then
        log_info "Assigning storage permissions..."
        az role assignment create \
            --assignee "$user_id" \
            --role "Storage Blob Data Contributor" \
            --scope "$storage_id" \
            --output none
    fi
}

# Attempt to obtain the current public IP using multiple strategies
get_public_ip() {
    local ip=""
    # Try DNS-based discovery (often works without HTTPS egress restrictions)
    if command -v dig >/dev/null 2>&1; then
        ip=$(dig +short myip.opendns.com @resolver1.opendns.com 2>/dev/null || echo "")
    fi
    # Fallbacks via HTTPS services
    if [ -z "$ip" ] && command -v curl >/dev/null 2>&1; then
        ip=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "")
    fi
    if [ -z "$ip" ] && command -v curl >/dev/null 2>&1; then
        ip=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null || echo "")
    fi
    # Final sanity check: ensure it matches IPv4 format
    if echo "$ip" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
        echo "$ip"
        return 0
    else
        echo ""
        return 1
    fi
}

# Check if we can list containers using Azure AD auth; returns 0 on success
can_access_storage_containers() {
    local account="$1"; local rg="$2"
    az storage container list \
        --account-name "$account" \
        --resource-group "$rg" \
        --auth-mode login \
        -o none 2>/tmp/storage_list_err.txt && return 0
    return 1
}

# Determine if current azd environment is a dev/sandbox context
is_dev_sandbox() {
    local env_name sandbox
    env_name=$(get_azd_env "AZURE_ENV_NAME")
    sandbox=$(get_azd_env "SANDBOX_MODE")
    # Explicit override via SANDBOX_MODE=true/1/yes
    if echo "${sandbox}" | grep -Eiq '^(1|true|yes)$'; then

        log_info "Detected dev/sandbox environment: ${env_name}"
        return 0
    fi
    # Heuristic based on environment name
    if echo "${env_name}" | grep -Eiq '^(dev|local|sandbox)$'; then
        return 0
    fi
    return 1
}

# Ensure current public IP is whitelisted if storage is not reachable via public network rules
ensure_storage_ip_whitelisted() {
    local account="$1"; local rg="$2"

    # If reachable already, nothing to do
    if can_access_storage_containers "$account" "$rg"; then
        log_success "Storage account '$account' is reachable with current credentials."
        return 0
    fi

    # Check if public network access is disabled (private endpoints scenario)
    local pna
    pna=$(az storage account show --name "$account" --resource-group "$rg" --query "publicNetworkAccess" -o tsv 2>/dev/null || echo "Enabled")
    if [ "$pna" = "Disabled" ]; then
        if is_dev_sandbox; then
            log_warning "Public network access is Disabled on '$account'. Enabling PNA and setting default-action=Deny for dev/sandbox, then whitelisting current IP."
            if ! az storage account update --name "$account" --resource-group "$rg" --public-network-access Enabled -o none 2>/tmp/storage_pna_enable_err.txt; then
                log_warning "Failed to enable Public Network Access on '$account'. Reason: $(tr -d '\n' < /tmp/storage_pna_enable_err.txt)"
                return 1
            fi
            # Lock down to selected networks by setting default action to Deny
            if ! az storage account network-rule update --resource-group "$rg" --account-name "$account" --default-action Deny -o none 2>/tmp/storage_default_deny_err.txt; then
                log_warning "Failed to set default-action=Deny on '$account'. Reason: $(tr -d '\n' < /tmp/storage_default_deny_err.txt)"
                # Continue anyway; adding an IP rule may still help depending on current policy
            fi
        else
            log_warning "Public network access is disabled for '$account'. Skipping IP whitelist (non-dev environment)."
            return 1
        fi
    fi

    # Get current public IP
    local ip
    ip=$(get_public_ip)
    if [ -z "$ip" ]; then
        log_warning "Could not determine current public IP. Skipping IP whitelist."
        return 1
    fi

    log_info "Adding current IP to storage network rules: $ip"
    if az storage account network-rule add \
        --resource-group "$rg" \
        --account-name "$account" \
        --ip-address "$ip" \
        -o none 2>/tmp/storage_rule_err.txt; then
        # Re-check access
        sleep 3
        if can_access_storage_containers "$account" "$rg"; then
            log_success "Whitelisted IP $ip for storage '$account' and verified access."
            return 0
        else
            log_warning "IP added, but access still failing. This may be due to RBAC propagation or other policies."
            return 1
        fi
    else
        log_warning "Failed to add IP rule to storage '$account'. Reason: $(tr -d '\n' < /tmp/storage_rule_err.txt)"
        return 1
    fi
}

# Check if JSON file has meaningful content
has_json_content() {
    local file="$1"
    
    # If file doesn't exist or is empty, return false
    [[ ! -f "$file" ]] || [[ ! -s "$file" ]] && return 1
    
    # Remove whitespace and check if it's just empty braces
    local content=$(tr -d '[:space:]' < "$file")
    [[ "$content" == "{}" ]] && return 1
    
    # Check if file has any JSON keys
    if python3 -c "import json; data=json.load(open('$file')); exit(0 if data else 1)" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Update tfvars file only if empty or non-existent
update_tfvars() {
    local tfvars_file="./infra/terraform/main.tfvars.json"
    local env_name="${1}"
    local location="${2}"
    
    # Ensure directory exists
    mkdir -p "$(dirname "$tfvars_file")"
    
    # Check if file has actual content
    if has_json_content "$tfvars_file"; then
        log_info "tfvars file already contains values, skipping update"
        return 0
    fi
    
    log_info "Creating/updating tfvars file: $tfvars_file"
    
    # Write the tfvars content
    cat > "$tfvars_file" << EOF
{
  "environment_name": "$env_name",
  "location": "$location"
}
EOF
    log_success "Updated $tfvars_file"
}

# Main execution
main() {
    echo "========================================================================="
    echo "🏗️  Terraform Remote State Storage Setup"
    echo "========================================================================="
    
    check_dependencies
    
    # Get environment values
    local env_name=$(get_azd_env "AZURE_ENV_NAME")
    local location=$(get_azd_env "AZURE_LOCATION")
    local sub_id=$(az account show --query id -o tsv)
    
    if [[ -z "$env_name" ]]; then
        log_error "AZURE_ENV_NAME is not set in the azd environment."
        exit 1
    fi
    if [[ -z "$location" ]]; then
        log_error "AZURE_LOCATION is not set in the azd environment."
        exit 1
    fi

    # Check existing configuration
    local storage_account=$(get_azd_env "RS_STORAGE_ACCOUNT")
    local container=$(get_azd_env "RS_CONTAINER_NAME")
    local resource_group=$(get_azd_env "RS_RESOURCE_GROUP")
    
    # If not configured or doesn't exist, create new
    if [[ -z "$storage_account" ]] || ! storage_exists "$storage_account" "$resource_group"; then
        log_info "Setting up new Terraform remote state storage..."
        read storage_account container resource_group <<< $(generate_names "$env_name" "$sub_id")
        create_storage "$storage_account" "$container" "$resource_group" "$location"
        
        # Set azd environment variables
        azd env set RS_STORAGE_ACCOUNT "$storage_account"
        azd env set RS_CONTAINER_NAME "$container"
        azd env set RS_RESOURCE_GROUP "$resource_group"
        azd env set RS_STATE_KEY "$env_name.tfstate"
    else
        log_success "Using existing remote state configuration"
    fi

    # Ensure current IP is allowed if storage isn't reachable
    ensure_storage_ip_whitelisted "$storage_account" "$resource_group" || true
    
    # Update tfvars file (only if empty or doesn't exist)
    update_tfvars "$env_name" "$location"
    

    
    log_success "✅ Terraform remote state setup completed!"
    echo ""
    echo "📋 Configuration:"
    echo "   Storage Account: $storage_account"
    echo "   Container: $container"
    echo "   Resource Group: $resource_group"
    echo ""
    echo "📁 Files created/updated:"
    echo "   - infra/terraform/provider.conf.json"
    echo "   - infra/terraform/main.tfvars.json (only if empty/new)"
}

# Handle script interruption
trap 'log_error "Script interrupted"; exit 130' INT

# Run main function
main "$@"