#!/bin/bash

# Azure Load Test Deployment Script
# This script sets up and deploys the comprehensive load test to Azure Load Testing service

set -e

# Configuration
RESOURCE_GROUP="audio-agent-load-test-rg"
LOCATION="eastus"
LOAD_TEST_RESOURCE="audio-agent-load-test"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID}"
TEST_NAME="ai-audio-agent-comprehensive-test"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check if Azure CLI is installed
    if ! command -v az &> /dev/null; then
        print_error "Azure CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check if logged in
    if ! az account show &> /dev/null; then
        print_error "Not logged in to Azure. Please run 'az login' first."
        exit 1
    fi
    
    # Check if subscription is set
    if [ -z "$SUBSCRIPTION_ID" ]; then
        print_warning "AZURE_SUBSCRIPTION_ID environment variable not set. Using default subscription."
        SUBSCRIPTION_ID=$(az account show --query id -o tsv)
    fi
    
    print_success "Prerequisites check completed"
}

# Create resource group
create_resource_group() {
    print_status "Creating resource group: $RESOURCE_GROUP"
    
    if az group show --name "$RESOURCE_GROUP" &> /dev/null; then
        print_warning "Resource group $RESOURCE_GROUP already exists"
    else
        az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
        print_success "Resource group created successfully"
    fi
}

# Create Load Test resource
create_load_test_resource() {
    print_status "Creating Azure Load Testing resource: $LOAD_TEST_RESOURCE"
    
    # Check if the resource already exists
    if az load show --name "$LOAD_TEST_RESOURCE" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
        print_warning "Load Test resource $LOAD_TEST_RESOURCE already exists"
    else
        # Create the load test resource
        az load create \
            --name "$LOAD_TEST_RESOURCE" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$LOCATION"
        
        print_success "Load Test resource created successfully"
    fi
}

# Upload test files
upload_test_files() {
    print_status "Uploading test files..."
    
    # Create a temporary directory for test files
    TEST_DIR=$(mktemp -d)
    
    # Copy test files to temporary directory
    cp azure_comprehensive_load_test.py "$TEST_DIR/"
    cp azure_load_test_config.yaml "$TEST_DIR/"
    
    # Copy conversation simulator if it exists
    if [ -f "conversation_simulator.py" ]; then
        cp conversation_simulator.py "$TEST_DIR/"
    fi
    
    # Create requirements.txt for Python dependencies
    cat > "$TEST_DIR/requirements.txt" << EOF
asyncio
websockets>=10.0
aiohttp>=3.8.0
pydantic>=1.10.0
numpy>=1.21.0
scipy>=1.7.0
matplotlib>=3.5.0
seaborn>=0.11.0
python-json-logger>=2.0.0
azure-monitor-opentelemetry>=1.0.0
EOF
    
    print_success "Test files prepared in $TEST_DIR"
    echo "Test directory: $TEST_DIR"
}

# Create and run the load test
create_and_run_test() {
    print_status "Creating load test: $TEST_NAME"
    
    # Upload and create the test
    az load test create \
        --test-id "$TEST_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --load-test-resource "$LOAD_TEST_RESOURCE" \
        --test-plan azure_comprehensive_load_test.py \
        --env TEST_SCENARIOS="light,medium" \
        --env WEBSOCKET_URL="${WEBSOCKET_URL:-ws://localhost:8010/api/v1/media/stream}" \
        --env TEST_DURATION_MINUTES="${TEST_DURATION_MINUTES:-5}" \
        --env MAX_CONCURRENT_USERS="${MAX_CONCURRENT_USERS:-25}" \
        --env TARGET_RPS="${TARGET_RPS:-5.0}" \
        --description "Comprehensive conversation-based load test for AI Audio Agent" \
        --display-name "AI Audio Agent Load Test" 
    
    print_success "Load test created successfully"
}

# Run the load test
run_load_test() {
    print_status "Starting load test execution..."
    
    # Start the test run
    RUN_ID=$(az load test-run create \
        --test-id "$TEST_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --load-test-resource "$LOAD_TEST_RESOURCE" \
        --display-name "Load Test Run - $(date +%Y%m%d-%H%M%S)" \
        --description "Automated load test execution" \
        --query testRunId -o tsv)
    
    print_success "Load test started with Run ID: $RUN_ID"
    
    # Monitor the test run
    print_status "Monitoring test execution..."
    
    while true; do
        STATUS=$(az load test-run show \
            --test-run-id "$RUN_ID" \
            --resource-group "$RESOURCE_GROUP" \
            --load-test-resource "$LOAD_TEST_RESOURCE" \
            --query status -o tsv)
        
        case $STATUS in
            "ACCEPTED"|"PROVISIONING"|"CONFIGURING"|"EXECUTING")
                print_status "Test status: $STATUS - waiting..."
                sleep 30
                ;;
            "DONE")
                print_success "Load test completed successfully!"
                break
                ;;
            "FAILED"|"CANCELLED")
                print_error "Load test $STATUS"
                break
                ;;
            *)
                print_warning "Unknown status: $STATUS"
                sleep 30
                ;;
        esac
    done
    
    # Get test results
    print_status "Retrieving test results..."
    
    az load test-run download-files \
        --test-run-id "$RUN_ID" \
        --resource-group "$RESOURCE_GROUP" \
        --load-test-resource "$LOAD_TEST_RESOURCE" \
        --path "./load_test_results_$RUN_ID"
    
    print_success "Test results downloaded to: ./load_test_results_$RUN_ID"
    
    # Display summary
    az load test-run show \
        --test-run-id "$RUN_ID" \
        --resource-group "$RESOURCE_GROUP" \
        --load-test-resource "$LOAD_TEST_RESOURCE" \
        --query "{Status:status,StartTime:startDateTime,EndTime:endDateTime,VirtualUsers:virtualUsers}" \
        --output table
}

# Clean up resources (optional)
cleanup_resources() {
    print_warning "Cleaning up resources..."
    
    read -p "Do you want to delete the resource group and all resources? (y/N): " confirm
    
    if [[ $confirm =~ ^[Yy]$ ]]; then
        az group delete --name "$RESOURCE_GROUP" --yes --no-wait
        print_success "Resource cleanup initiated"
    else
        print_status "Resources preserved"
    fi
}

# Show help
show_help() {
    echo "Azure Load Test Deployment Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  setup       Create Azure resources"
    echo "  upload      Upload test files"
    echo "  create      Create load test"
    echo "  run         Run load test"
    echo "  deploy      Full deployment (setup + upload + create + run)"
    echo "  cleanup     Delete all resources"
    echo "  help        Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  AZURE_SUBSCRIPTION_ID      Azure subscription ID"
    echo "  WEBSOCKET_URL              WebSocket URL to test (default: ws://localhost:8010/api/v1/media/stream)"
    echo "  TEST_DURATION_MINUTES      Test duration in minutes (default: 5)"
    echo "  MAX_CONCURRENT_USERS       Maximum concurrent users (default: 25)"
    echo "  TARGET_RPS                 Target requests per second (default: 5.0)"
    echo ""
    echo "Examples:"
    echo "  $0 deploy"
    echo "  WEBSOCKET_URL=ws://my-service.com:8010/api/v1/media/stream $0 run"
}

# Main execution
main() {
    case "${1:-deploy}" in
        "setup")
            check_prerequisites
            create_resource_group
            create_load_test_resource
            ;;
        "upload")
            upload_test_files
            ;;
        "create")
            create_and_run_test
            ;;
        "run")
            run_load_test
            ;;
        "deploy")
            check_prerequisites
            create_resource_group
            create_load_test_resource
            upload_test_files
            create_and_run_test
            run_load_test
            ;;
        "cleanup")
            cleanup_resources
            ;;
        "help"|"-h"|"--help")
            show_help
            ;;
        *)
            print_error "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"