#!/bin/bash

# Function to display usage
usage() {
    echo "Usage: $0 <deployment_target>"
    echo "  deployment_target: appservice, containerapp, staticwebapp, or function"
    exit 1
}

# Check if argument is provided
if [ $# -ne 1 ]; then
    echo "Error: Deployment target argument is required"
    usage
fi

AGENT="${1:-RTAgent}"  # Default agent name
DEPLOYMENT_TARGET="${2:-appservice}"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate the deployment target argument
case "$DEPLOYMENT_TARGET" in
    "appservice")
        echo "App Service deployment detected"
        echo "Running pre-deploy setup for App Service..."
        
        # Build frontend assets for App Service deployment
        if [ -d "rtagents" ]; then
            echo "Building temporary deployment artifacts..."
            
            # Invoke the App Service preparation helper script
            if [ -f "${SCRIPT_DIR}/helpers/appsvc-prep.sh" ]; then
                echo "Running App Service preparation script..."
                bash "${SCRIPT_DIR}/helpers/appsvc-prep.sh"
            else
                echo "Warning: appsvc-prep.sh not found in helpers directory"
            fi
        fi
        ;;
    "containerapp")
        echo "Container App deployment detected"
        echo "Running pre-deploy setup for Container Apps..."
        
        # Container-specific build logic could go here
        ;;
        
    "staticwebapp")
        echo "Static Web App deployment detected"
        echo "Running pre-deploy setup for Static Web Apps..."
        
        # Build frontend assets for Static Web Apps
        if [ -d "rtagents" ]; then
            echo "Building frontend applications for Static Web Apps..."
            
            for agent_dir in rtagents/*/; do
                if [ -d "${agent_dir}frontend" ]; then
                    agent_name=$(basename "$agent_dir")
                    echo "Building static assets for $agent_name..."
                    
                    cd "${agent_dir}frontend" || continue
                    
                    if [ -f "package.json" ]; then
                        npm install --silent
                        npm run build
                        
                        # Ensure dist/build directory exists for SWA
                        if [ ! -d "dist" ] && [ -d "build" ]; then
                            ln -sf build dist
                        fi
                    fi
                    
                    cd - > /dev/null
                fi
            done
        fi
        ;;
        
    "function")
        echo "Azure Functions deployment detected"
        echo "Running pre-deploy setup for Azure Functions..."
        
        # Validate function app structure
        if [ -f "host.json" ]; then
            echo "Azure Functions host.json found"
        fi
        
        if [ -f "requirements.txt" ]; then
            echo "Python requirements found for Functions runtime"
        fi
        ;;
        
    *)
        echo "Error: Invalid deployment target '$DEPLOYMENT_TARGET'"
        echo "Supported targets: appservice, containerapp, staticwebapp, function"
        usage
        ;;
esac

echo "Pre-deploy setup completed for $DEPLOYMENT_TARGET"