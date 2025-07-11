#!/bin/bash

set -e

# ========================================================================
# 🚀 Simple Azure App Service Deployment
# ========================================================================

# Configuration
AGENT="${1:-RTAgent}"
MODE="${2:-both}"  # frontend, backend, or both

echo "🚀 Deploying $AGENT ($MODE) to App Service"

# Get AZD variables
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
FRONTEND_APP=$(azd env get-value FRONTEND_APP_SERVICE_NAME)
BACKEND_APP=$(azd env get-value BACKEND_APP_SERVICE_NAME)
AZD_ENV=$(azd env get-value AZURE_ENV_NAME)

# Deploy Frontend
deploy_frontend() {
    echo "📱 Deploying frontend..."
    
    # Get AZD environment name for proper temp directory
    TEMP_DIR=".azure/$AZD_ENV/frontend"

    # Clean and create temp deployment directory
    rm -rf "$TEMP_DIR"
    mkdir -p "$TEMP_DIR"

    # Copy frontend code excluding node_modules and build artifacts
    rsync -av --exclude='node_modules' --exclude='dist' --exclude='.git' \
        --exclude='*.log' --exclude='.DS_Store' --exclude='coverage' \
        "rtagents/$AGENT/frontend/" "$TEMP_DIR/"

    cd "$TEMP_DIR"
    zip -r frontend.zip . \
        -x "node_modules/*" \
        -x "dist/*" \
        -x ".git/*" \
        -x "*.log" \
        -x ".DS_Store"
    
    # Deploy using az webapp deploy instead of deprecated config-zip
    az webapp deploy \
        --resource-group "$RG" \
        --name "$FRONTEND_APP" \
        --src-path frontend.zip \
        --type zip
    
    rm frontend.zip
}


# Deploy Backend  
deploy_backend() {
    echo "⚙️ Deploying backend..."
    
    # Create deployment zip from workspace root (like launch.json working directory)
    # Include entire workspace but exclude unnecessary files
    # Create a zip that preserves the folder structure but only includes the specific agent
    # Get AZD environment name for proper temp directory
    TEMP_DIR=".azure/$AZD_ENV/backend"

    # Clean and create temp deployment directory
    rm -rf "$TEMP_DIR"
    mkdir -p "$TEMP_DIR/rtagents/$AGENT/backend"

    # Copy backend code excluding cache and artifacts
    rsync -av --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
        --exclude='*.log' --exclude='.coverage' --exclude='htmlcov' \
        "rtagents/$AGENT/backend/" "$TEMP_DIR/rtagents/$AGENT/backend/"

    # Copy src directory excluding cache
    rsync -av --exclude='__pycache__' --exclude='*.pyc' \
        src/ "$TEMP_DIR/src/"
    
    # Copy utils directory excluding cache
    rsync -av --exclude='__pycache__' --exclude='*.pyc' \
        utils/ "$TEMP_DIR/utils/"
    
    cp requirements.txt "$TEMP_DIR/requirements.txt"

    cd "$TEMP_DIR"
    zip -r backend.zip . \
        -x "__pycache__" \
        -x "*/__pycache__/*" \
        -x "*.pyc" \
        -x ".DS_Store" \
        -x "*.log"
    
    # Deploy using az webapp deploy instead of deprecated config-zip
    az webapp deploy \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --src-path backend.zip \
        --type zip        
        
    # Set startup command to mirror launch.json structure
    # Working directory is workspace root, run uvicorn with module path
    # Note: App Service expects port 8000, not 8010 (which is for local debugging)
    az webapp config set \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --startup-file "python -m uvicorn rtagents.$AGENT.backend.main:app --host 0.0.0.0 --port 8000"
    
    # Set PYTHONPATH to workspace root (like launch.json env)
    az webapp config appsettings set \
        --resource-group "$RG" \
        --name "$BACKEND_APP" \
        --settings "PYTHONPATH=/home/site/wwwroot" "SCM_DO_BUILD_DURING_DEPLOYMENT=true" "ENABLE_ORYX_BUILD=true" \
        --output none
    
    rm backend.zip
}

# Main deployment
case "$MODE" in
    frontend) deploy_frontend ;;
    backend) deploy_backend ;;
    both) deploy_backend && deploy_frontend ;;
    *) echo "❌ Invalid mode. Use: frontend, backend, or both" && exit 1 ;;
esac

echo "✅ Deployment complete!"