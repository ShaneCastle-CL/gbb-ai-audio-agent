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

# Deploy Frontend
deploy_frontend() {
    echo "📱 Deploying frontend..."
    cd "rtagents/$AGENT/frontend"
    
    # Create deployment zip
    zip -r ../frontend.zip . -x node_modules/\* dist/\* .git/\*
    
    az webapp deployment source config-zip \
        --resource-group "$RG" \
        --name "$FRONTEND_APP" \
        --src ../frontend.zip
    
    rm ../frontend.zip
    cd - > /dev/null
}

# Deploy Backend  
deploy_backend() {
    echo "⚙️ Deploying backend..."
    
    # Create deployment zip from workspace root (like launch.json working directory)
    # Include entire workspace but exclude unnecessary files
    # Create a zip that preserves the folder structure but only includes the specific agent
    # Get AZD environment name for proper temp directory
    AZD_ENV=$(azd env get-value AZURE_ENV_NAME)
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
    
#     # Create production requirements.txt (exclude problematic packages for App Service)
#     echo "📝 Creating production requirements.txt..."
#     if [ -f "requirements.txt" ]; then
#         # Filter out packages that cause build issues on App Service Linux
#         # These are typically local audio/UI packages not needed for web service
#         echo "🔧 Filtering out local/UI dependencies for App Service deployment:"
        
#         # Show what we're filtering out
#         echo "   Excluding: pyaudio, sounddevice, streamlit, gradio, plotly, yfinance, pipwin"
        
#         grep -v "^pyaudio" requirements.txt | \
#         grep -v "^pygame" | \
#         grep -v "^sounddevice" | \
#         grep -v "^portaudio" | \
#         grep -v "^streamlit" | \
#         grep -v "^gradio" | \
#         grep -v "^plotly" | \
#         grep -v "^yfinance" | \
#         grep -v "^pipwin" > temp_deploy/requirements.txt
        
#         # Ensure uvicorn is included (critical for App Service)
#         if ! grep -q "uvicorn" temp_deploy/requirements.txt; then
#             echo "uvicorn[standard]" >> temp_deploy/requirements.txt
#             echo "🔧 Added missing uvicorn to requirements"
#         fi
        
#         echo "✅ Production requirements.txt created"
#     else
#         # Create minimal requirements if none exists
#         cat > temp_deploy/requirements.txt << EOF
# fastapi>=0.104.0
# uvicorn[standard]>=0.24.0
# openai>=1.0.0
# azure-identity>=1.15.0
# azure-cognitiveservices-speech>=1.34.0
# azure-communication-calling>=1.0.0
# redis>=5.0.0
# pydantic>=2.5.0
# python-multipart>=0.0.6
# websockets>=12.0
# azure-cosmos>=4.5.0
# azure-storage-blob>=12.19.0
# azure-keyvault-secrets>=4.7.0
# python-dotenv>=1.0.0
# python-json-logger>=2.0.0
# EOF
#     fi
    
    # # Also create agent-specific requirements.txt
    # cp temp_deploy/requirements.txt temp_deploy/rtagents/$AGENT/backend/requirements.txt
    
#     # Create .deployment file to ensure Oryx builds properly
#     cat > temp_deploy/.deployment << EOF
# [config]
# SCM_DO_BUILD_DURING_DEPLOYMENT=true
# ENABLE_ORYX_BUILD=true
# PROJECT=.
# PYTHON_VERSION=3.11
# PRE_BUILD_COMMAND=""
# POST_BUILD_COMMAND=""
# EOF

#     # Create oryx manifest to force Python detection
#     cat > temp_deploy/oryx-manifest.toml << EOF
# [build]
# platform = "python"
# version = "3.11"
# requirements = "requirements.txt"
# EOF

#     # Create runtime.txt to explicitly specify Python version
#     echo "python-3.11" > temp_deploy/runtime.txt
    
    # cd temp_deploy
    # zip -r ../backend.zip . \
    #     -x "__pycache__" \
    #     -x "*/__pycache__/*" \
    #     -x "*.pyc" \
    #     -x ".DS_Store" \
    #     -x "*.log"
    # cd ..
    
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