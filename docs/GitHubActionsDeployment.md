# GitHub Actions Deployment to Azure

This document explains how to use the GitHub Actions workflow to automatically deploy the ARTVoice Accelerator to Azure.

## Overview

The repository includes a streamlined GitHub Actions workflow (`azure-deploy.yml`) that automatically deploys your application to Azure using Azure Developer CLI (azd) and Terraform.

## Deployment Workflow

**Workflow File**: `.github/workflows/azure-deploy.yml`

### Features

- ✅ **Automatic Deployment**: Automatically deploys on push to `main` branch
- 🔘 **Manual Trigger**: Deploy to any environment on-demand via workflow_dispatch
- 🔐 **Secure Authentication**: Uses OpenID Connect (OIDC) for passwordless authentication
- 🏗️ **Infrastructure as Code**: Provisions infrastructure using Terraform
- 📦 **Container Deployment**: Builds and deploys Docker containers to Azure Container Apps
- 📊 **Deployment Summary**: Provides clear deployment status and URLs
- 🔄 **Multiple Environments**: Supports dev, staging, and prod environments

### Automatic Deployment Triggers

The workflow automatically runs when you push changes to the `main` branch that affect:
- Source code (`src/**`)
- Application code (`apps/**`)
- Infrastructure (`infra/terraform/**`)
- Azure configuration (`azure.yaml`)
- Python dependencies (`requirements.txt`)
- The workflow itself (`.github/workflows/azure-deploy.yml`)

### Manual Deployment

You can manually trigger a deployment from the GitHub Actions UI:

1. Go to **Actions** tab in your repository
2. Select **🚀 Deploy to Azure** workflow
3. Click **Run workflow**
4. Select the environment (dev, staging, or prod)
5. Click **Run workflow**

## Prerequisites

### 1. Azure Resources

You need the following Azure resources set up:

- **Azure Subscription** with appropriate permissions
- **Service Principal** or **Managed Identity** for authentication
- **Storage Account** for Terraform state (remote backend)

### 2. GitHub Secrets

Configure the following secrets in your GitHub repository:

#### Required Secrets

Navigate to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Description | How to Get It |
|------------|-------------|---------------|
| `AZURE_CLIENT_ID` | Azure Service Principal Application (Client) ID | From Azure Portal → Azure Active Directory → App registrations |
| `AZURE_TENANT_ID` | Azure Active Directory Tenant ID | From Azure Portal → Azure Active Directory → Overview |
| `AZURE_SUBSCRIPTION_ID` | Azure Subscription ID | From Azure Portal → Subscriptions |

#### How to Create a Service Principal

```bash
# Create service principal with contributor role
az ad sp create-for-rbac \
  --name "github-actions-deploy" \
  --role contributor \
  --scopes /subscriptions/{subscription-id} \
  --sdk-auth

# Configure OIDC federation (for passwordless auth)
az ad app federated-credential create \
  --id {app-id} \
  --parameters '{
    "name": "github-actions",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:YOUR-ORG/YOUR-REPO:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

### 3. GitHub Variables

Configure the following variables for Terraform state storage:

Navigate to **Settings** → **Secrets and variables** → **Actions** → **Variables**

| Variable Name | Description | Example |
|--------------|-------------|---------|
| `RS_RESOURCE_GROUP` | Resource group containing the storage account for Terraform state | `tfstate-rg` |
| `RS_STORAGE_ACCOUNT` | Storage account name for Terraform state | `tfstatestorage123` |
| `RS_CONTAINER_NAME` | Container name within the storage account | `tfstate` |

### 4. GitHub Environments

Set up environments for deployment approvals (optional but recommended):

1. Go to **Settings** → **Environments**
2. Create environments: `dev`, `staging`, `prod`
3. Configure protection rules:
   - **Required reviewers**: Add team members who must approve deployments
   - **Wait timer**: Add delay before deployment starts
   - **Deployment branches**: Restrict which branches can deploy to this environment

## Workflow Structure

### Jobs

The workflow contains a single job: `deploy`

### Steps

1. **Setup**
   - Checkout code
   - Azure login (OIDC)
   - Install Azure Developer CLI
   - Install Terraform

2. **Authentication**
   - Login to Azure Developer CLI using OIDC

3. **Configuration**
   - Configure azd environment
   - Setup Terraform parameters
   - Configure Terraform backend for remote state

4. **Deployment**
   - Run `azd up` to provision infrastructure and deploy application

5. **Post-Deployment**
   - Extract deployment information (URLs, resource groups)
   - Display deployment status

6. **Cleanup**
   - Logout from Azure

7. **Summary**
   - Generate deployment summary in GitHub Actions UI

## Environment Configuration

### Environment-Specific Parameters

Each environment (dev, staging, prod) should have its own Terraform parameters file:

- `infra/terraform/params/main.tfvars.dev.json`
- `infra/terraform/params/main.tfvars.staging.json`
- `infra/terraform/params/main.tfvars.prod.json`

Example structure:
```json
{
  "location": "eastus",
  "environment": "dev",
  "principal_id": "your-principal-id",
  "app_gateway_sku": "Standard_v2"
}
```

### Dynamic Parameters

The workflow automatically adds these parameters:
- `environment_name`: Environment being deployed (dev/staging/prod)
- `principal_type`: Set to "ServicePrincipal" for CI/CD
- `deployed_by`: GitHub username who triggered the deployment

## Monitoring Deployments

### During Deployment

1. Go to **Actions** tab in GitHub
2. Click on the running workflow
3. Monitor real-time logs for each step

### After Deployment

The workflow generates a summary showing:
- Deployment status
- Environment name
- Resource group name
- Frontend URL
- Backend URL

You can also:
- Check Azure Portal for resource status
- Run `azd monitor` locally to see application logs
- Use Application Insights for detailed monitoring

## Troubleshooting

### Common Issues

#### 1. Authentication Failed

**Error**: `Failed to authenticate with Azure`

**Solution**:
- Verify `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` are correct
- Ensure OIDC federation is configured for your service principal
- Check service principal has necessary permissions

#### 2. Terraform State Backend Issues

**Error**: `Failed to configure backend`

**Solution**:
- Verify storage account exists and is accessible
- Check `RS_RESOURCE_GROUP`, `RS_STORAGE_ACCOUNT`, and `RS_CONTAINER_NAME` variables
- Ensure service principal has Storage Blob Data Contributor role on the storage account

#### 3. Terraform Parameters File Not Found

**Error**: `Terraform parameters file not found`

**Solution**:
- Ensure the parameters file exists for your environment
- Check file naming: `infra/terraform/params/main.tfvars.{environment}.json`

#### 4. Deployment Timeout

**Error**: `The job running on runner has exceeded the maximum execution time`

**Solution**:
- Azure deployments can take 15-20 minutes
- Consider splitting into separate provision and deploy steps
- Check for stuck resources in Azure Portal

### Getting Help

If you encounter issues:

1. Check the **workflow logs** in GitHub Actions for detailed error messages
2. Review the **Deployment Guide** (`docs/DeploymentGuide.md`) for infrastructure requirements
3. Check **Troubleshooting Guide** (`docs/Troubleshooting.md`) for common issues
4. Open an issue in the repository with:
   - Workflow run URL
   - Error messages from logs
   - Environment details

## Advanced Configuration

### Customizing the Workflow

You can customize the workflow by:

1. **Adding additional environments**: Update the `environment` input options
2. **Changing trigger paths**: Modify the `paths` array in the `push` trigger
3. **Adding deployment checks**: Include additional validation steps before deployment
4. **Integrating testing**: Add test jobs that run before deployment

### Using Different Deployment Actions

The workflow uses `azd up` which provisions infrastructure and deploys code in one step.

Alternative approaches:
- Use `azd provision` followed by `azd deploy` for separate steps
- Use the existing `deploy-azd.yml` workflow for more control
- Use Terraform directly for infrastructure-only changes

### Deployment Notifications

Add notifications by including additional steps:

```yaml
- name: Notify Slack
  if: always()
  uses: slackapi/slack-github-action@v1
  with:
    payload: |
      {
        "text": "Deployment ${{ job.status }}: ${{ github.repository }}"
      }
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

## Related Documentation

- [Deployment Guide](DeploymentGuide.md) - Detailed infrastructure deployment guide
- [CI/CD Guide](CICDGuide.md) - CI/CD configuration and best practices
- [Troubleshooting](Troubleshooting.md) - Common issues and solutions
- [Azure Developer CLI Documentation](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
- [GitHub Actions Documentation](https://docs.github.com/actions)

## Security Best Practices

1. **Never commit secrets**: Use GitHub Secrets for sensitive data
2. **Use OIDC authentication**: Avoid long-lived credentials
3. **Implement environment protection**: Require approvals for production deployments
4. **Limit permissions**: Follow principle of least privilege for service principals
5. **Rotate credentials regularly**: Update service principal credentials periodically
6. **Enable branch protection**: Prevent direct pushes to main branch
7. **Review deployment logs**: Ensure no sensitive data is logged

## Next Steps

After setting up the deployment workflow:

1. ✅ Test deployment to dev environment
2. ✅ Configure staging environment with approval requirements
3. ✅ Set up production environment with strict protection rules
4. ✅ Enable branch protection rules
5. ✅ Configure monitoring and alerting
6. ✅ Document your deployment process for your team
