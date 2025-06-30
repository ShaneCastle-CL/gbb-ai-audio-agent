#!/bin/bash
# filepath: /Users/jinle/Repos/_AIProjects/gbb-ai-audio-agent/scripts/azd-postprovision.sh

# Exit immediately if a command exits with a non-zero status
set -e

# Load environment variables from .env file
# Check if ACS_SOURCE_PHONE_NUMBER already exists
EXISTING_ACS_PHONE_NUMBER="$(azd env get-value ACS_SOURCE_PHONE_NUMBER 2>/dev/null || echo "")"
if [ -n "$EXISTING_ACS_PHONE_NUMBER" ] && [ "$EXISTING_ACS_PHONE_NUMBER" != "null" ]; then
    # Check if the existing value is actually a phone number (starts with + and contains only digits)
    if [[ "$EXISTING_ACS_PHONE_NUMBER" =~ ^\+[0-9]+$ ]]; then
        echo "ACS_SOURCE_PHONE_NUMBER already exists: $EXISTING_ACS_PHONE_NUMBER"
        echo "Skipping phone number creation."
    else
        echo "ACS_SOURCE_PHONE_NUMBER exists but is not a valid phone number format: $EXISTING_ACS_PHONE_NUMBER"
        echo "Proceeding with phone number creation..."

        # Phone number creation with error handling
        {
            # Ensure Azure CLI communication extension is installed
            if ! az extension list --query "[?name=='communication']" -o tsv | grep -q communication; then
                echo "Adding Azure CLI communication extension..."
                az extension add --name communication
            else
                echo "Azure CLI communication extension is already installed."
            fi

            # Create a new ACS phone number
            # Get ACS endpoint from azd environment
            echo "Retrieving ACS_ENDPOINT from environment..."
            ACS_ENDPOINT="$(azd env get-value ACS_ENDPOINT)"
            if [ -z "$ACS_ENDPOINT" ]; then
                echo "Error: ACS_ENDPOINT is not set in the environment."
                exit 1
            fi

            echo "Creating a new ACS phone number..."
            # Install required Python packages for ACS phone number management
            echo "Installing required Python packages..."
            pip3 install azure-identity azure-communication-phonenumbers

            # Run the Python script and capture its output
            PHONE_NUMBER=$(python3 scripts/acs_phone_number_manager.py --endpoint "$ACS_ENDPOINT" purchase || echo "")
            if [ -z "$PHONE_NUMBER" ]; then
                echo "Error: Failed to create ACS phone number."
                exit 1
            fi

            echo "Successfully created ACS phone number: $PHONE_NUMBER"

            # Set the ACS_SOURCE_PHONE_NUMBER in azd environment
            azd env set ACS_SOURCE_PHONE_NUMBER "$PHONE_NUMBER"

            echo "Updated ACS_SOURCE_PHONE_NUMBER in .env file."

            echo "Post-provisioning script completed successfully."
        } || {
            echo "Warning: ACS phone number creation failed, but continuing with the rest of the script..."
        }
    fi
fi

# Add BACKEND_UAI_PRINCIPAL_ID to Azure Entra group
echo "Adding BACKEND_UAI_PRINCIPAL_ID to Azure Entra group..."

# Get required values from azd environment
BACKEND_UAI_PRINCIPAL_ID="$(azd env get-value BACKEND_UAI_PRINCIPAL_ID)"
AZURE_ENTRA_GROUP_ID="$(azd env get-value AZURE_ENTRA_GROUP_ID)"

if [ -z "$BACKEND_UAI_PRINCIPAL_ID" ]; then
    echo "Error: BACKEND_UAI_PRINCIPAL_ID is not set in the environment."
    exit 1
fi

if [ -z "$AZURE_ENTRA_GROUP_ID" ]; then
    echo "Error: AZURE_ENTRA_GROUP_ID is not set in the environment."
    exit 1
fi

# Check if the member is already in the group
# EXISTING_MEMBER=$(az ad group member list --group "$AZURE_ENTRA_GROUP_ID" --query "[?id=='$BACKEND_UAI_PRINCIPAL_ID'].id" -o tsv)

EXISTING_MEMBER=$(az rest --method get --url "https://graph.microsoft.com/v1.0/groups/$AZURE_ENTRA_GROUP_ID/members/microsoft.graph.servicePrincipal" --query "value[?id=='$BACKEND_UAI_PRINCIPAL_ID'].id" -o tsv)

if [ -n "$EXISTING_MEMBER" ]; then
    echo "BACKEND_UAI_PRINCIPAL_ID ($BACKEND_UAI_PRINCIPAL_ID) is already a member of the Azure Entra group."
else
    echo "Adding BACKEND_UAI_PRINCIPAL_ID to Azure Entra group..."
    if az ad group member add --group "$AZURE_ENTRA_GROUP_ID" --member-id "$BACKEND_UAI_PRINCIPAL_ID" 2>/dev/null; then
        echo "Successfully added BACKEND_UAI_PRINCIPAL_ID to Azure Entra group."
    else
        # Check if the error is because the member already exists
        echo "Failed to add member. Checking if member already exists..."
        EXISTING_MEMBER_RETRY=$(az ad group member list --group "$AZURE_ENTRA_GROUP_ID" --query "[?id=='$BACKEND_UAI_PRINCIPAL_ID'].id" -o tsv)
        if [ -n "$EXISTING_MEMBER_RETRY" ]; then
            echo "BACKEND_UAI_PRINCIPAL_ID is already a member of the Azure Entra group."
        else
            echo "Error: Failed to add BACKEND_UAI_PRINCIPAL_ID to Azure Entra group."
            exit 1
        fi
    fi
fi