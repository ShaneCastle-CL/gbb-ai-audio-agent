#!/bin/bash

set -euo pipefail

# ========================================================================
# 🔄 Azure Developer CLI Post-deployment Cleanup
# ========================================================================
# This script restores the original azure.yaml after deployment

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ️  [INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}✅ [SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}⚠️  [WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}❌ [ERROR]${NC} $*" >&2
}

echo "========================================================================="
echo "🔄 Post-deployment cleanup"
echo "========================================================================="

# Restore original azure.yaml
restore_azure_yaml() {
    log_info "🔄 Restoring original azure.yaml..."
    
    if [[ -f "azure.yaml.backup" ]]; then
        cp azure.yaml.backup azure.yaml
        rm azure.yaml.backup
        log_success "Restored original azure.yaml and removed backup"
    else
        log_warning "No backup found, azure.yaml not restored"
    fi
}

# Clean up temporary deployment directories
cleanup_temp_dirs() {
    log_info "🧹 Cleaning up temporary deployment directories..."
    
    # Get azd environment name
    local env_name
    if env_name=$(azd env get-value AZURE_ENV_NAME 2>/dev/null); then
        rm -rf ".azure/$env_name/backend"
        rm -rf ".azure/$env_name/frontend-build"
        rm -rf ".azure/$env_name/azure.yaml"
        log_success "Cleaned up temporary directories for environment: $env_name"
    else
        log_warning "Could not determine azd environment, skipping cleanup"
    fi
}

# Main execution
main() {
    restore_azure_yaml
    cleanup_temp_dirs
    
    echo "========================================================================="
    log_success "Post-deployment cleanup completed successfully!"
    echo ""
    echo "📝 Cleanup summary:"
    echo "   ✅ Original azure.yaml restored"
    echo "   ✅ Temporary deployment directories cleaned"
    echo "========================================================================="
}

# Handle script interruption
trap 'log_error "Post-deployment cleanup interrupted by user"; exit 130' INT

# Run main function
main "$@"
