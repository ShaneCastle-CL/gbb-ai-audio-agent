#!/bin/bash

set -euo pipefail

# ========================================================================
# 🚀 Enhanced Azure App Service Deployment with azd Integration
# ========================================================================

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Configuration
AGENT="${1:-RTAgent}"
MODE="${2:-both}"  # frontend, backend, or both

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
echo "🚀 Deploying $AGENT ($MODE) to App Service"
echo "========================================================================="

# Validate azd environment
validate_azd_environment() {
    log_info "Validating azd environment..."
    
    local required_vars=("AZURE_RESOURCE_GROUP" "FRONTEND_APP_SERVICE_NAME" "BACKEND_APP_SERVICE_NAME" "AZURE_ENV_NAME")
    local missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if ! azd env get-value "$var" &>/dev/null; then
            missing_vars+=("$var")
        fi
    done
    
    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        log_error "Missing required azd environment variables: ${missing_vars[*]}"
        log_error "Please run 'azd provision' first or set the missing variables."
        exit 1
    fi
    
    log_success "azd environment validated."
}

# Get AZD variables with validation
get_azd_vars() {
    RG=$(azd env get-value AZURE_RESOURCE_GROUP)
    FRONTEND_APP=$(azd env get-value FRONTEND_APP_SERVICE_NAME)
    BACKEND_APP=$(azd env get-value BACKEND_APP_SERVICE_NAME)
    AZD_ENV=$(azd env get-value AZURE_ENV_NAME)
    
    log_info "Using azd environment: $AZD_ENV"
    log_info "Resource Group: $RG"
    log_info "Frontend App: $FRONTEND_APP"
    log_info "Backend App: $BACKEND_APP"
}

# Deploy Frontend with azd-style build process
deploy_frontend() {
    log_info "📱 Deploying frontend..."
    
    local project_dir="rtagents/$AGENT/frontend"
    local temp_dir=".azure/$AZD_ENV/frontend"
    
    # Validate project directory
    if [[ ! -d "$project_dir" ]]; then
        log_error "Frontend project directory not found: $project_dir"
        return 1
    fi
    
    # Clean and create temp deployment directory
    rm -rf "$temp_dir"
    mkdir -p "$temp_dir"
    
    # Copy frontend code excluding node_modules and build artifacts
    log_info "Copying frontend code..."
    rsync -av --exclude='node_modules' --exclude='dist' --exclude='.git' \
        --exclude='*.log' --exclude='.DS_Store' --exclude='coverage' \
        "$project_dir/" "$temp_dir/"
    
    # Build frontend (similar to what azd would do)
    log_info "Building frontend..."
    (
        cd "$temp_dir"
        
        # Install dependencies if package.json exists
        if [[ -f "package.json" ]]; then
            npm ci --production
            
            # Run build if build script exists
            if npm run build --if-present; then
                log_success "Frontend build completed."
            else
                log_warning "No build script found or build failed."
            fi
        fi
        
        # Create deployment package
        zip -r frontend.zip . \
            -x "node_modules/*" \
            -x "dist/*" \
            -x ".git/*" \
            -x "*.log" \
            -x ".DS_Store"
    )
    
    # Deploy to App Service
    log_info "Deploying to App Service..."
    az webapp deploy \
        --resource-group "$RG" \
        --name "$FRONTEND_APP" \
        --src-path "$temp_dir/frontend.zip" \
        --type zip
    
    # Clean up
    rm -f "$temp_dir/frontend.zip"
    
    log_success "Frontend deployment completed."
}

# Deploy Backend with azd-style Python deployment
deploy_backend() {
    log_info "⚙️ Deploying backend..."
    
    local project_dir="rtagents/$AGENT/backend"
    local temp_dir=".azure/$AZD_ENV/backend"
    
    # Validate project directory
    if [[ ! -d "$project_dir" ]]; then
        log_error "Backend project directory not found: $project_dir"
        return 1
    fi
    
    # Clean and create temp deployment directory
    rm -rf "$temp_dir"
    mkdir -p "$temp_dir/rtagents/$AGENT/backend"
    
    # Copy backend code structure (preserving azd conventions)
    log_info "Copying backend code..."
    rsync -av --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
        --exclude='*.log' --exclude='.coverage' --exclude='htmlcov' \
        "$project_dir/" "$temp_dir/rtagents/$AGENT/backend/"
    
    # Copy shared modules
    rsync -av --exclude='__pycache__' --exclude='*.pyc' \
        src/ "$temp_dir/src/"
    
    rsync -av --exclude='__pycache__' --exclude='*.pyc' \
        utils/ "$temp_dir/utils/"
    
    # Copy requirements
    cp requirements.txt "$temp_dir/requirements.txt"
    
    # Create deployment package
    log_info "Creating deployment package..."
    (
        cd "$temp_dir"
        zip -r backend.zip . \
            -x "__pycache__" \
            -x "*/__pycache__/*" \
            -x "*.pyc" \
            -x ".DS_Store" \
            -x "*.log"
    )
    
    # Deploy to App Service
    log_info "Deploying to App Service..."
    az webapp deploy \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --src-path "$temp_dir/backend.zip" \
        --type zip
    
    # Configure startup and environment (azd-compatible)
    log_info "Configuring App Service..."
    az webapp config set \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --startup-file "python -m uvicorn rtagents.$AGENT.backend.main:app --host 0.0.0.0 --port 8000"
    
    # Set environment variables (including azd injected ones)
    az webapp config appsettings set \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --settings \
            "PYTHONPATH=/home/site/wwwroot" \
            "SCM_DO_BUILD_DURING_DEPLOYMENT=true" \
            "ENABLE_ORYX_BUILD=true" \
            "AZURE_ENV_NAME=$AZD_ENV" \
        --output none
    
    # Clean up
    rm -f "$temp_dir/backend.zip"
    
    log_success "Backend deployment completed."
}

# Health check post-deployment
health_check() {
    log_info "Performing health checks..."
    
    local backend_url frontend_url
    
    # Get app URLs
    backend_url=$(az webapp show --resource-group "$RG" --name "$BACKEND_APP" --query "defaultHostName" -o tsv)
    frontend_url=$(az webapp show --resource-group "$RG" --name "$FRONTEND_APP" --query "defaultHostName" -o tsv)
    
    # Simple health check for backend
    if curl -f -s "https://$backend_url/health" >/dev/null; then
        log_success "Backend health check passed: https://$backend_url"
    else
        log_warning "Backend health check failed or endpoint not available."
    fi
    
    # Simple check for frontend
    if curl -f -s "https://$frontend_url" >/dev/null; then
        log_success "Frontend health check passed: https://$frontend_url"
    else
        log_warning "Frontend health check failed or endpoint not available."
    fi
}

# Main execution
main() {
    validate_azd_environment
    get_azd_vars
    
    # Deploy based on mode
    case "$MODE" in
        frontend) 
            deploy_frontend 
            ;;
        backend) 
            deploy_backend 
            ;;
        both) 
            deploy_backend
            deploy_frontend 
            ;;
        *) 
            log_error "Invalid mode. Use: frontend, backend, or both"
            exit 1 
            ;;
    esac
    
    # Post-deployment validation
    health_check
    
    echo "========================================================================="
    log_success "Deployment completed successfully!"
    echo ""
    echo "📝 Deployed apps:"
    if [[ "$MODE" == "backend" || "$MODE" == "both" ]]; then
        echo "   Backend:  https://$(az webapp show --resource-group "$RG" --name "$BACKEND_APP" --query "defaultHostName" -o tsv)"
    fi
    if [[ "$MODE" == "frontend" || "$MODE" == "both" ]]; then
        echo "   Frontend: https://$(az webapp show --resource-group "$RG" --name "$FRONTEND_APP" --query "defaultHostName" -o tsv)"
    fi
    echo "========================================================================="
}

# Handle script interruption
trap 'log_error "Deployment interrupted by user"; exit 130' INT

# Run main function
main "$@"
