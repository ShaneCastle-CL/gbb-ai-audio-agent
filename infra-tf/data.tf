# ============================================================================
# AZURE STORAGE ACCOUNT
# ============================================================================

resource "azurerm_storage_account" "main" {
  name                     = local.resource_names.storage
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"

  public_network_access_enabled = true
  allow_nested_items_to_be_public = false

  # Enable blob properties
  blob_properties {
    delete_retention_policy {
      days = 7
    }
  }

  tags = local.tags
}

# Storage containers
resource "azurerm_storage_container" "audioagent" {
  name                  = "audioagent"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "prompt" {
  name                  = "prompt"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

# RBAC assignments for Storage
resource "azurerm_role_assignment" "storage_backend_contributor" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.backend.principal_id
}

resource "azurerm_role_assignment" "storage_principal_reader" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = local.principal_id
}

resource "azurerm_role_assignment" "storage_principal_contributor" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.principal_id
}

# ============================================================================
# COSMOS DB (MONGODB API)
# ============================================================================
# # Cosmos DB vCore MongoDB Cluster (M30 with 128GB disk)
# resource "azurerm_mongo_cluster" "main" {
#     name                = local.resource_names.cosmos
#     resource_group_name = azurerm_resource_group.main.name
#     location            = azurerm_resource_group.main.location
    
#     administrator_username = "adminuser"
#     administrator_password = random_password.cosmos_admin.result
    
#     compute_tier    = "M30"
#     high_availability_mode = "Disabled"
#     public_network_access = "Enabled"
#     shard_count     = 1
#     storage_size_in_gb = 128
#     version         = "5.0"

    

#     tags = local.tags
# }

/*
Note: This is a generated HCL content from the JSON input which is based on the latest API version available.
To import the resource, please run the following command:
terraform import azapi_resource.mongoCluster /subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/rg-rtaudioagent-tfdev/providers/Microsoft.DocumentDB/mongoClusters/cosmosjzarqvbp?api-version=2023-03-01-preview

Or add the below config:
import {
  id = "/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/rg-rtaudioagent-tfdev/providers/Microsoft.DocumentDB/mongoClusters/cosmosjzarqvbp?api-version=2023-03-01-preview"
  to = azapi_resource.mongoCluster
}
*/
/*
Note: This is a generated HCL content from the JSON input which is based on the latest API version available.
To import the resource, please run the following command:
terraform import azapi_resource.mongoCluster /subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/rg-rtaudioagent-tfdev/providers/Microsoft.DocumentDB/mongoClusters/cosmosjzarqvbp?api-version=2025-04-01-preview

Or add the below config:
import {
  id = "/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/rg-rtaudioagent-tfdev/providers/Microsoft.DocumentDB/mongoClusters/cosmosjzarqvbp?api-version=2025-04-01-preview"
  to = azapi_resource.mongoCluster
}
*/
resource "azapi_resource" "mongoCluster" {
  type      = "Microsoft.DocumentDB/mongoClusters@2025-04-01-preview"
  parent_id = azurerm_resource_group.main.id
  name      = local.resource_names.cosmos
  location  = var.location
  body = {
    properties = {
      administrator = {
        userName = "adminuser"
        password = random_password.cosmos_admin.result
      }
      authConfig = {
        # Ensure the order is always the same and matches the API's default
        allowedModes = [
          "MicrosoftEntraID",
          "NativeAuth"
        ]
      }
      backup = {}
      compute = {
        tier = "M30"
      }
      createMode = "Default"
      dataApi = {
        mode = "Disabled"
      }
      highAvailability = {
        targetMode = "Disabled"
      }
      publicNetworkAccess = "Enabled"
      serverVersion       = "5.0"
      sharding = {
        shardCount = 1
      }
      storage = {
        sizeGb = 128
        type   = "PremiumSSD"
      }
    }
  }
  tags = local.tags

  # Suppress diffs for allowedModes array ordering
  lifecycle {
    ignore_changes = [
      body["properties"]["authConfig"]["allowedModes"],
      output["properties"]["authConfig"]["allowedModes"],
      output["properties"]["backup"]["earliestRestoreTime"],
      output["properties"]["clusterStatus"],
      output["properties"]["connectionString"],
      output["properties"]["infrastructureVersion"],
      output["properties"]["provisioningState"],
      output["properties"]["replica"]["replicationState"],
      output["properties"]["replica"]["role"],
      output["tags"]
    ]
  }
}




# Store Entra ID connection string in Key Vault
resource "azurerm_key_vault_secret" "cosmos_entra_connection_string" {
  name         = "cosmos-entra-connection-string"
  value        = "${data.azapi_resource.mongo_cluster_info.output.properties.connectionString}?authSource=%24external&authMechanism=MONGODB-OIDC"
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_role_assignment.keyvault_admin, data.azapi_resource.mongo_cluster_info]
}

# Generate random password for Cosmos DB admin
resource "random_password" "cosmos_admin" {
    length  = 16
    special = true
}

# Store Cosmos DB admin password in Key Vault
resource "azurerm_key_vault_secret" "cosmos_admin_password" {
    name         = "cosmos-admin-password"
    value        = random_password.cosmos_admin.result
    key_vault_id = azurerm_key_vault.main.id

    depends_on = [azurerm_role_assignment.keyvault_admin]
}
# RBAC assignments for Cosmos DB vCore cluster
resource "azapi_resource" "cosmos_backend_db_user" {
  type = "Microsoft.DocumentDB/mongoClusters/users@2025-04-01-preview"
  name = azurerm_user_assigned_identity.backend.principal_id
  parent_id = azapi_resource.mongoCluster.id
  body = {
    properties = {
      identityProvider = {
        properties = {
          principalType = "ServicePrincipal"
        }
        type = "MicrosoftEntraID"
        // For remaining properties, see IdentityProvider objects
      }
      roles = [
        {
          db = "admin"
          role = "dbOwner"
        }
      ]
    }
  }

  # Suppress diffs for output and principalType casing
  lifecycle {
    ignore_changes = [
      body["properties"]["identityProvider"]["properties"]["principalType"],
      output["properties"]["provisioningState"],
      output["properties"]["roles"],
      output["id"],
      output["type"],
      output,
    ]
  }
}

# RBAC assignments for Cosmos DB vCore cluster
resource "azapi_resource" "cosmos_principal_user" {
  type = "Microsoft.DocumentDB/mongoClusters/users@2025-04-01-preview"
  name = data.azuread_client_config.current.object_id
  parent_id = azapi_resource.mongoCluster.id
  body = {
    properties = {
      identityProvider = {
        properties = {
          principalType = "User"
        }
        type = "MicrosoftEntraID"
        // For remaining properties, see IdentityProvider objects
      }
      roles = [
        {
          db = "admin"
          role = "dbOwner"
        }
      ]
    }
  }
}

# Data sources to retrieve MongoDB cluster information
data "azapi_resource" "mongo_cluster_info" {
  type      = "Microsoft.DocumentDB/mongoClusters@2025-04-01-preview"
  parent_id = azurerm_resource_group.main.id
  name      = azapi_resource.mongoCluster.name
  
  depends_on = [azapi_resource.mongoCluster]
}


# Store MongoDB connection details in Key Vault
resource "azurerm_key_vault_secret" "cosmos_connection_string" {
  name         = "cosmos-connection-string"
  value        = data.azapi_resource.mongo_cluster_info.output.properties.connectionString
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_role_assignment.keyvault_admin, data.azapi_resource.mongo_cluster_info]
}
