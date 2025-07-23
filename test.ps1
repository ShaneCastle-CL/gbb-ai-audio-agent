    #Write-ColorOutput "Setting azd environment variables..." -Type Info
    
    azd env set RS_STORAGE_ACCOUNT "tfstatec74280bd"
    azd env set RS_CONTAINER_NAME "tfstate"
    azd env set RS_RESOURCE_GROUP "rg-tfstate-tfdev-c74280bd"
    azd env set RS_STATE_KEY "terraform.tfstate"
    
    #Write-ColorOutput "Environment variables set:" -Type Success
    
    Write-Host "  RS_STORAGE_ACCOUNT=tfstatec74280bd" -ForegroundColor White
    Write-Host "  RS_CONTAINER_NAME=tfstate" -ForegroundColor White
    Write-Host "  RS_RESOURCE_GROUP=rg-tfstate-tfdev-c74280bd" -ForegroundColor White
    Write-Host "  RS_STATE_KEY=terraform.tfstate" -ForegroundColor White