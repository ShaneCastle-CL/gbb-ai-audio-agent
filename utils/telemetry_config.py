# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License in the project root for
# license information.
# --------------------------------------------------------------------------
import os
import logging
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.core.exceptions import HttpResponseError

# Set up logger for this module
logger = logging.getLogger(__name__)

def setup_azure_monitor(logger_name: str = None):
    """
    Configure Azure Monitor / Application Insights if connection string is available.
    Implements fallback authentication and graceful degradation for live metrics.

    Args:
        logger_name (str, optional): Name for the Azure Monitor logger. Defaults to environment variable or 'default'.
    """
    connection_string = os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')
    logger_name = logger_name or os.getenv('AZURE_MONITOR_LOGGER_NAME', 'default')
    
    # Check if we should disable live metrics due to permission issues
    disable_live_metrics = os.getenv('AZURE_MONITOR_DISABLE_LIVE_METRICS', 'false').lower() == 'true'

    if not connection_string:
        logger.info("ℹ️ APPLICATIONINSIGHTS_CONNECTION_STRING not found, skipping Azure Monitor configuration")
        return

    try:
        # Try to get appropriate credential
        credential = _get_azure_credential()
        
        # Configure with live metrics initially disabled if environment variable is set
        # or if we're in a development environment
        enable_live_metrics = not disable_live_metrics and _should_enable_live_metrics()
        
        configure_azure_monitor(
            
            logger_name=logger_name,
            credential=credential,
            connection_string=connection_string,
            enable_live_metrics=enable_live_metrics,
            disable_logging=False,
            disable_tracing=False,
            disable_metrics=False,
            instrumentation_options={
                "azure_sdk": {"enabled": True},
                "aiohttp": {"enabled": True},
                "fastapi": {"enabled": True},
                "flask": {"enabled": True},
                "requests": {"enabled": True},
                "urllib3": {"enabled": True},
            }
        )
        
        status_msg = "✅ Azure Monitor configured successfully"
        if not enable_live_metrics:
            status_msg += " (live metrics disabled)"
        logger.info(status_msg)
        
    except ImportError:
        logger.warning("⚠️ Azure Monitor OpenTelemetry not available. Install azure-monitor-opentelemetry package.")
    except HttpResponseError as e:
        if "Forbidden" in str(e) or "permissions" in str(e).lower():
            logger.warning("⚠️ Insufficient permissions for Application Insights. Retrying with live metrics disabled...")
            _retry_without_live_metrics(logger_name, connection_string)
        else:
            logger.error(f"⚠️ HTTP error configuring Azure Monitor: {e}")
    except Exception as e:
        logger.error(f"⚠️ Failed to configure Azure Monitor: {e}")

def _get_azure_credential():
    """
    Get the appropriate Azure credential based on the environment.
    Prioritizes managed identity in Azure-hosted environments.
    """
    try:
        # Try managed identity first if we're in Azure
        if os.getenv('WEBSITE_SITE_NAME') or os.getenv('CONTAINER_APP_NAME'):
            logger.debug("Using ManagedIdentityCredential for Azure-hosted environment")
            return ManagedIdentityCredential()
    except Exception as e:
        logger.debug(f"ManagedIdentityCredential not available: {e}")
    
    # Fall back to DefaultAzureCredential
    logger.debug("Using DefaultAzureCredential")
    return DefaultAzureCredential()

def _should_enable_live_metrics():
    """
    Determine if live metrics should be enabled based on environment.
    """
    # Disable in development environments by default
    if os.getenv('ENVIRONMENT', '').lower() in ['dev', 'development', 'local']:
        return False
    
    # Enable in production environments
    if os.getenv('ENVIRONMENT', '').lower() in ['prod', 'production']:
        return True
    
    # For other environments, check if we're in Azure
    return bool(os.getenv('WEBSITE_SITE_NAME') or os.getenv('CONTAINER_APP_NAME'))

def _retry_without_live_metrics(logger_name: str, connection_string: str):
    """
    Retry Azure Monitor configuration without live metrics if permission errors occur.
    """
    try:
        credential = _get_azure_credential()
        
        configure_azure_monitor(
            logger_name=logger_name,
            credential=credential,
            connection_string=connection_string,
            enable_live_metrics=False,  # Disable live metrics
            disable_logging=False,
            disable_tracing=False,
            disable_metrics=False,
            instrumentation_options={
                "azure_sdk": {"enabled": True},
                "aiohttp": {"enabled": True},
                "fastapi": {"enabled": True},
                "flask": {"enabled": True},
                "requests": {"enabled": True},
                "urllib3": {"enabled": True},
            }
        )
        logger.info("✅ Azure Monitor configured successfully (live metrics disabled due to permissions)")
        
        # Set environment variable to remember this setting
        os.environ['AZURE_MONITOR_DISABLE_LIVE_METRICS'] = 'true'
        
    except Exception as e:
        logger.error(f"⚠️ Failed to configure Azure Monitor even without live metrics: {e}")