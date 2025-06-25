# Infrastructure README - Work in Progress

⚠️ **This infrastructure is currently a work in progress** ⚠️

This document outlines the core Azure infrastructure components required for the real-time voice agent application. The infrastructure supports multiple voice agent scenarios including health claims, benefits lookup, and billing inquiries.

## 🚧 Current Status

This is an **active development project** with several manual configuration steps still required before full functionality is achieved. Please review the manual setup requirements carefully before deployment.

## Core Infrastructure Components

### 🔊 Azure Communication Services (ACS) - Voice Gateway
- **Call Management**: Handles incoming and outgoing PSTN voice calls
- **Real-time Communication**: WebSocket connections for live audio streaming
- **Media Processing**: Audio codec handling and real-time media transport
- **Call State Management**: Session lifecycle and call routing

**Manual Requirements:**
- **Phone Number Purchase**: Must manually purchase phone number through Azure portal with voice capabilities
- **Session Border Controller (SBC)**: Manual configuration required for PSTN connectivity and call routing
- **Public Domain Requirement**: ACS requires backend services to be accessible via publicly verifiable domain (not localhost or private IPs)

### 🎤 Speech Services - Transcription Engine
- **Speech-to-Text (STT)**: Real-time transcription with streaming support
- **Text-to-Speech (TTS)**: Natural voice synthesis for AI responses
- **Custom Voice Models**: Support for domain-specific voice customization
- **Multi-language Support**: Configurable language detection and processing

**Manual Requirements:**
- **Custom Domain**: Required for ACS integration - must register and configure custom domain for Speech Services
- **DNS Configuration**: Proper CNAME/A record setup for domain verification

### 🤖 AI & Intelligence Layer
- **Azure OpenAI**: GPT-4o and other models for conversational AI
- **API Management**: Load balancing and gateway for OpenAI endpoints
- **Event Grid**: Event-driven architecture for call state and transcript events
- **Application Insights**: Real-time monitoring and performance analytics

### 💾 Data & State Management
- **Azure Redis Cache**: Session state, active call data, and real-time transcript storage
- **Cosmos DB**: Persistent conversation history and call analytics
- **Azure Blob Storage**: Audio recordings, call logs, and media artifacts
- **Key Vault**: Secure storage for API keys, certificates, and connection strings

### 🌐 Networking & Load Balancing
- **Application Gateway**: Layer 7 load balancer with SSL termination and WAF
- **Virtual Network**: Private networking with dedicated subnets for each service tier
- **Private Endpoints**: Secure private connectivity to Azure PaaS services
- **Public IP & DNS**: Required for ACS webhook callbacks and external accessibility

**Critical Requirement:**
- **Publicly Verifiable Domain**: ACS requires the backend to be reachable through a domain that can be publicly verified (HTTPS with valid SSL certificate). This necessitates:
  - Application Gateway with public IP
  - Custom domain with valid SSL certificate
  - Proper DNS resolution from public internet

### 🔐 Security & Identity
- **Managed Identity**: Service-to-service authentication without stored credentials
- **Azure AD Integration**: Optional user authentication for admin interfaces
- **Network Security Groups**: Traffic filtering and access control
- **SSL/TLS Termination**: End-to-end encryption for all communications

## Manual Setup Requirements

### 1. 📞 Phone Number & PSTN Configuration
```bash
# Navigate to Azure Communication Services in portal
# 1. Go to ACS resource > Phone numbers
# 2. Purchase phone number with "Make and receive calls" capability
# 3. Note the phone number for configuration
```

**SBC Configuration Steps:**
- Configure Session Border Controller settings in ACS
- Set up call routing rules for inbound/outbound traffic
- Configure media processing and supported codecs
- Test PSTN connectivity and call flow

### 2. 🌍 Custom Domain Setup for Speech Services
```bash
# Required for ACS + Speech Services integration for real-time transcription
# 1. Register custom domain on the speech service
# 2. Update .env for the backend configuration for AZURE_SPEECH_ENDPOINT with the domain configured endpoint
```

### 3. 🔗 Public Domain & Application Gateway
The ACS service requires webhook callbacks to a publicly accessible HTTPS endpoint:

```bash
# Domain requirements:
# - Must be accessible from public internet
# - Must have valid SSL certificate
# - Must resolve to Application Gateway public IP
# - Cannot use localhost, private IPs, or self-signed certificates
```

**Application Gateway Configuration:**
- WAF_v2 SKU for security and performance
- SSL certificate management via Key Vault
- Backend pool pointing to Container Apps
- Health probes for high availability

### 4. 🔧 Container Apps Backend Configuration
The FastAPI backend must be configured for:
- WebSocket support for real-time communication
- CORS configuration for frontend access
- Environment variables from Key Vault
- Managed identity for Azure service access

## Deployment Architecture

```
Internet → Application Gateway → Container Apps (Backend/Frontend)
    ↓              ↓                      ↓
PSTN Calls → ACS → Speech Services → Azure OpenAI
    ↓              ↓                      ↓
Call Events → Event Grid → Redis Cache → Cosmos DB
```

## Current Deployment Status

- [x] Core infrastructure (VNet, subnets, security groups)
- [x] Private DNS zones for private endpoint resolution
- [x] Container Apps platform with auto-scaling
- [x] Redis Cache for session management
- [x] Cosmos DB for persistent storage
- [x] Key Vault for secrets management
- [x] Azure OpenAI with API Management
- [x] Event Grid for event-driven architecture
- [ ] Application Gateway with WAF protection
- [ ] **Phone number purchase and configuration**
- [ ] **Custom domain setup for Speech Services**
- [ ] **SBC configuration for PSTN calling**
- [ ] **SSL certificate provisioning and binding**
- [ ] **End-to-end call flow testing**
- [ ] **Production security hardening**
- [ ] **Monitoring and alerting setup**

## Known Limitations & TODOs

### High Priority
1. **Manual Phone Number Setup**: Azure doesn't support Bicep/ARM templates for phone number purchase
2. **Custom Domain Verification**: Speech Services custom domain requires manual DNS verification
3. **SBC Configuration**: PSTN calling setup requires manual configuration in ACS portal
4. **SSL Certificate Management**: Certificate provisioning and renewal process needs automation

### Medium Priority
- Load testing and performance optimization
- Advanced monitoring and alerting rules
- Disaster recovery and backup strategies
- Multi-region deployment support

## Local Development Notes

For local development, the infrastructure includes:
- Container Apps with development-friendly settings
- Redis Cache accessible via private endpoint
- Key Vault integration for local secret access
- Application Gateway bypass for direct Container App access

## Support & Troubleshooting

Common issues and solutions:
- **ACS Webhook Failures**: Ensure backend is accessible via public HTTPS endpoint
- **Speech Service Integration**: Verify custom domain is properly configured
- **Call Quality Issues**: Check SBC configuration and network routing
- **Authentication Errors**: Verify managed identity permissions

For additional help, check the [Architecture.md](../docs/Architecture.md) and [Integration-Points.md](../docs/Integration-Points.md) documentation.