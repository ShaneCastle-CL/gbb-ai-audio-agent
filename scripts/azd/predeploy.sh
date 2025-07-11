#!/bin/bash

set -euo pipefail

# ========================================================================
# 🔧 Azure Developer CLI Pre-deployment Artifact Preparation
# ========================================================================
# This script organizes build artifacts for azd native deployment
# Compatible with azd service definitions in azure.yaml

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Configuration
AGENT="${1:-RTAgent}"

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
echo "🔧 Preparing deployment artifacts for $AGENT"
echo "========================================================================="

# Get azd environment name
get_azd_env() {
    local env_name
    if env_name=$(azd env get-value AZURE_ENV_NAME 2>/dev/null); then
        echo "$env_name"
    else
        log_warning "Could not get AZURE_ENV_NAME from azd, using 'dev' as default"
        echo "dev"
    fi
}

AZD_ENV=$(get_azd_env)
log_info "Using azd environment: $AZD_ENV"

# Prepare frontend artifacts for azd deployment
prepare_frontend() {
    log_info "📱 Preparing frontend artifacts..."
    
    local source_dir="rtagents/$AGENT/frontend"
    local target_dir="rtagents/$AGENT/frontend"
    local temp_dir=".azure/$AZD_ENV/frontend-build"
    
    # Validate source directory
    if [[ ! -d "$source_dir" ]]; then
        log_error "Frontend source directory not found: $source_dir"
        return 1
    fi
    
    # Clean previous build artifacts
    rm -rf "$temp_dir"
    mkdir -p "$temp_dir"
    
    # Copy frontend source excluding build artifacts and dependencies
    log_info "Copying frontend source..."
    rsync -av \
        --exclude='node_modules' \
        --exclude='dist' \
        --exclude='build' \
        --exclude='.git' \
        --exclude='*.log' \
        --exclude='.DS_Store' \
        --exclude='coverage' \
        --exclude='.next' \
        --exclude='.vite' \
        "$source_dir/" "$temp_dir/"
    
    # Check if package.json exists and install dependencies
    if [[ -f "$temp_dir/package.json" ]]; then
        log_info "Installing frontend dependencies..."
        (
            cd "$temp_dir"
            
            # Install dependencies
            if command -v npm &> /dev/null; then
                npm ci --production
                
                # Run build if build script exists
                if npm run build --if-present; then
                    log_success "Frontend build completed successfully."
                    
                    # Copy built assets to source directory for azd
                    if [[ -d "dist" ]]; then
                        rsync -av dist/ "$target_dir/dist/"
                        log_info "Copied dist/ to source directory"
                    elif [[ -d "build" ]]; then
                        rsync -av build/ "$target_dir/build/"
                        log_info "Copied build/ to source directory"
                    elif [[ -d ".next" ]]; then
                        rsync -av .next/ "$target_dir/.next/"
                        log_info "Copied .next/ to source directory"
                    fi
                else
                    log_warning "No build script found or build failed."
                fi
            else
                log_error "npm not found. Please install Node.js and npm."
                return 1
            fi
        )
    else
        log_warning "No package.json found in frontend directory."
    fi
    
    # Create deployment configuration for azd
    if [[ ! -f "$target_dir/.deployment" ]]; then
        cat > "$target_dir/.deployment" << 'EOF'
[config]
SCM_DO_BUILD_DURING_DEPLOYMENT=true
ENABLE_ORYX_BUILD=true
EOF
        log_info "Created .deployment file for Oryx build"
    fi
    
    log_success "Frontend artifacts prepared successfully."
}

# Prepare backend artifacts for azd deployment
prepare_backend() {
    log_info "⚙️ Preparing backend artifacts..."
    
    local source_dir="rtagents/$AGENT/backend"
    local TEMP_DIR=".azure/$AZD_ENV/backend"

    # Clean and create temp deployment directory
    rm -rf "$TEMP_DIR"
    mkdir -p "$TEMP_DIR/rtagents/$AGENT/backend"
    
    local target_dir="$TEMP_DIR/rtagents/$AGENT/backend"
    
    # Validate source directory
    if [[ ! -d "$source_dir" ]]; then
        log_error "Backend source directory not found: $source_dir"
        return 1
    fi
    
    log_info "Organizing backend workspace structure..."
    
    # Copy shared modules to backend directory for self-contained deployment
    if [[ -d "src" ]]; then
        rsync -av --exclude='__pycache__' --exclude='*.pyc' \
            src/ "$target_dir/src/"
        log_info "Copied shared src/ modules"
    fi
    
    if [[ -d "utils" ]]; then
        rsync -av --exclude='__pycache__' --exclude='*.pyc' \
            utils/ "$target_dir/utils/"
        log_info "Copied shared utils/ modules"
    fi
    
    # Copy root requirements.txt to backend directory
    if [[ -f "requirements.txt" ]]; then
        cp requirements.txt "$target_dir/requirements.txt"
        log_info "Copied root requirements.txt"
    fi
    
#     # Create oryx build configuration
#     cat > "$target_dir/oryx.ini" << EOF
# [build]
# # Enable Python build
# enablePythonBuild=true

# [python]
# # Python version (will be auto-detected from runtime.txt or latest stable)
# pythonVersion=3.11

# # Install command
# installCommand=pip install -r requirements.txt

# # Startup command
# startupCommand=python -m uvicorn rtagents.$AGENT.backend.main:app --host 0.0.0.0 --port 8000
# EOF
    
#     # Create deployment configuration for azd
#     cat > "$target_dir/.deployment" << 'EOF'
# [config]
# SCM_DO_BUILD_DURING_DEPLOYMENT=true
# ENABLE_ORYX_BUILD=true
# EOF
    
#     log_info "Created Oryx build configuration"
    log_success "Backend artifacts prepared successfully."
}

# Update azure.yaml with dynamic project paths
update_azure_yaml() {
    log_info "📝 Updating azure.yaml with dynamic project paths..."
    
    local temp_azure_yaml=".azure/$AZD_ENV/azure.yaml"
    local backend_project_path=".azure/$AZD_ENV/backend"
    
    # Create backup of original azure.yaml
    if [[ ! -f "azure.yaml.backup" ]]; then
        cp azure.yaml azure.yaml.backup
        log_info "Created backup of original azure.yaml"
    fi
    
    # Create temp azure.yaml with updated paths
    mkdir -p ".azure/$AZD_ENV"
    
    # Use sed to replace the backend project path
    sed "s|project: rtagents/$AGENT/backend|project: $backend_project_path|g" azure.yaml > "$temp_azure_yaml"
    
    # Replace the original azure.yaml with the updated one
    cp "$temp_azure_yaml" azure.yaml
    
    log_success "Updated azure.yaml with dynamic project paths"
    log_info "Backend project path: $backend_project_path"
}

# Restore original azure.yaml
restore_azure_yaml() {
    log_info "🔄 Restoring original azure.yaml..."
    
    if [[ -f "azure.yaml.backup" ]]; then
        cp azure.yaml.backup azure.yaml
        log_success "Restored original azure.yaml"
    else
        log_warning "No backup found, azure.yaml not restored"
    fi
}

# Validate azd environment
validate_azd_environment() {
    log_info "Validating azd environment..."
    
    if ! command -v azd &> /dev/null; then
        log_error "azd command not found. Please install Azure Developer CLI."
        return 1
    fi
    
    # Check if we can get azd environment values
    if ! azd env get-values &> /dev/null; then
        log_warning "azd environment not found or not accessible."
        log_info "Make sure you have run 'azd provision' or are in the correct environment."
    fi
    
    log_success "azd environment validation completed."
}

# Clean up temporary build directories
cleanup() {
    log_info "Cleaning up temporary build directories..."
    rm -rf ".azure/$AZD_ENV/frontend-build"
    log_success "Cleanup completed."
}

# Main execution
main() {
    validate_azd_environment
    
    # Prepare artifacts
    prepare_frontend
    prepare_backend
    
    # Update azure.yaml with dynamic paths
    update_azure_yaml
    
    # Clean up temporary directories
    cleanup
    
    echo "========================================================================="
    log_success "Pre-deployment preparation completed successfully!"
    echo ""
    echo "📝 Ready for azd deployment:"
    echo "   Frontend: rtagents/$AGENT/frontend/ (with build artifacts)"
    echo "   Backend:  .azure/$AZD_ENV/backend/ (with workspace structure)"
    echo ""
    echo "💡 Next steps:"
    echo "   1. Run 'azd deploy' to deploy both services"
    echo "   2. Or run 'azd up' for full provision + deploy"
    echo "   3. azure.yaml will be automatically restored after deployment"
    echo "========================================================================="
}

# Handle script interruption
trap 'log_error "Pre-deployment preparation interrupted by user"; exit 130' INT

# Run main function
main "$@"
