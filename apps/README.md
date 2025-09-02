# **Real-Time Voice Agent Application**

**Enterprise AI voice system** - Multi-agent orchestration with Azure Communication Services integration.

## **🏗️ Architecture Overview**

```
Phone/Browser → ACS/WebSocket → FastAPI Backend → Multi-Agent AI → Azure Services
```

**Core Components:**
- **Frontend**: React client for voice interaction
- **Backend**: Websocket -> FastAPI + ARTAgent framework for multi-agent orchestration  
- **Infrastructure**: Azure ACS, Speech, OpenAI, Redis, CosmosDB

## **📁 Project Structure & Navigation**

```
apps/rtagent/
├── backend/                    # FastAPI + ARTAgent framework
│   ├── main.py                # 🚀 Backend entry point - START HERE
│   ├── src/
│   │   ├── agents/            # 🤖 Multi-agent system
│   │   │   ├── agent_store/   # Agent YAML configurations
│   │   │   ├── prompt_store/  # Agent prompt templates
│   │   │   └── orchestrator/  # Agent routing logic
│   │   ├── speech/            # 🎙️ Speech-to-Text/Text-to-Speech
│   │   ├── redis/             # 💾 Session state management
│   │   ├── aoai/              # 🧠 Azure OpenAI integration
│   │   └── api/               # 🌐 REST/WebSocket endpoints
│   ├── config/                # ⚙️ Configuration files
│   ├── requirements.txt       # Python dependencies
│   └── .env.sample           # Environment template
├── frontend/                  # React + WebSocket client
│   ├── src/
│   │   ├── components/        # 🎨 UI components
│   │   │   ├── App.jsx       # 🚀 Main React component - START HERE
│   │   │   └── wip/          # Work-in-progress components
│   │   ├── hooks/            # 🔗 WebSocket and audio hooks
│   │   │   └── index.js      # Custom React hooks
│   │   └── main.jsx          # React entry point
│   ├── package.json          # Node.js dependencies
│   └── .env.sample          # Frontend environment template
└── scripts/                  # 🛠️ Setup and deployment utilities
    ├── start_backend.py      # Backend startup with validation
    ├── start_frontend.sh     # Frontend development server
    └── start_devtunnel_host.sh  # ACS webhook tunnel
```

### **🎯 Where to Find What**

| **What You Need** | **File Location** | **Purpose** |
|-------------------|-------------------|-------------|
| **Start backend** | `backend/main.py` | FastAPI app entry point |
| **Start frontend** | `frontend/src/main.jsx` | React app entry point |
| **Add new agent** | `backend/src/agents/agent_store/` | YAML agent configs |
| **Modify prompts** | `backend/src/agents/prompt_store/` | Agent prompt templates |
| **ACS integration** | `backend/src/acs/` | Phone call handling |
| **WebSocket logic** | `backend/src/api/` | Real-time endpoints |
| **React components** | `frontend/src/components/` | UI and voice controls |
| **Audio processing** | `frontend/src/hooks/` | WebSocket and audio hooks |
| **Configuration** | `backend/config/` | Voice, feature flags, limits |
| **Environment setup** | `.env.sample` files | Required credentials |

### **🔍 Key Files**

**Backend Core:**
- `backend/main.py` - FastAPI app, WebSocket routes, startup logic
- `backend/src/agents/orchestrator/` - Multi-agent routing and decisions
- `backend/src/acs/call_handler.py` - Phone call lifecycle management
- `backend/src/api/websocket.py` - Real-time audio streaming endpoints

**Frontend Core:**
- `frontend/src/components/App.jsx` - Main UI, WebSocket connection, audio processing
- `frontend/src/hooks/index.js` - WebSocket management and audio capture hooks
- `frontend/package.json` - Dependencies including @azure/communication-calling

**Configuration:**
- `backend/config/voice_config.py` - TTS voices, STT parameters
- `backend/config/feature_flags.py` - Enable/disable functionality
- `backend/.env` - Azure credentials and service endpoints


## **📞 Azure Communication Services (ACS) Integration**

### **Phone Call Flow**
```
PSTN Call → ACS Phone Number → Bidirectional Streaming → Media Streaming → FastAPI Backend
```

**ACS Components:**
1. **Call Automation**: Handles incoming PSTN calls, call routing, and telephony controls
2. **Media Streaming**: Real-time audio stream from caller to backend via WebSocket
3. **Phone Numbers**: Azure-provisioned phone numbers for public access
4. **Callback Webhooks**: ACS sends call events to backend for processing

**Key Endpoints:**
- `POST /api/v1/acs/events` - Receives ACS call events (answer, hangup, etc.)
- `WS /api/v1/media/stream` - Real-time audio streaming from ACS to backend
- `GET /api/v1/acs/health` - ACS service connectivity status

### **Call Processing Pipeline**
1. **Caller dials Azure phone number** → ACS receives call
2. **ACS sends webhook** to backend with call details
3. **Backend answers call** via ACS Call Automation API
4. **Audio stream established** from ACS to backend WebSocket
5. **Real-time processing** - Speech-to-Text → Agent → Text-to-Speech
6. **Audio response** streamed back to caller via ACS

### **Frontend "Call Me" Feature**
- **Direct browser calling** using @azure/communication-calling SDK
- **No phone number required** - browser-to-backend voice connection
- **Same processing pipeline** as phone calls, different entry point

## **🔧 Technical Stack**

### **Frontend (React)**
- **WebSocket Client**: Real-time communication with FastAPI backend
- **Audio Processing**: Web Audio API for microphone capture and playback
- **ACS Integration**: @azure/communication-calling for direct browser calls
- **UI Components**: Real-time conversation display and voice controls

### **Backend (FastAPI)**
- **WebSocket Server**: Handles real-time audio streams and conversation management
- **ARTAgent Framework**: Multi-agent orchestration (Auth, FNOL, General, Billing)
- **ACS Integration**: Call Automation API for telephony, Media Streaming for audio
- **Azure Services**: Speech SDK, OpenAI, Redis, CosmosDB integration

### **Key Endpoints**
| **Endpoint** | **Purpose** | **Type** |
|--------------|-------------|----------|
| `WS /api/v1/realtime/conversation` | Frontend voice interaction | WebSocket |
| `WS /api/v1/media/stream` | ACS audio streaming | WebSocket |
| `POST /api/v1/acs/events` | ACS call event webhooks | REST |
| `GET /api/v1/health` | System health monitoring | REST |
| `GET /api/v1/agents` | Agent configuration | REST |

## **🚀 Quick Start**

### **Prerequisites**
- Python 3.11+, Node.js 18+
- Azure services provisioned (see Infrastructure section)

### **Backend Setup**
```bash
cd apps/rtagent/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env  # Configure Azure credentials
python main.py  # Starts on localhost:8010
```

### **Frontend Setup**  
```bash
cd apps/rtagent/frontend
npm install && npm run dev  # Starts on localhost:5173
```

### **Access Points**
- **Web UI**: http://localhost:5173
- **API Docs**: http://localhost:8010/docs
- **WebSocket**: ws://localhost:8010

## **⚙️ Configuration**

### **Environment Variables**
```bash
# Backend (.env)
AZURE_OPENAI_ENDPOINT=your-endpoint
AZURE_SPEECH_KEY=your-speech-key  
ACS_CONNECTION_STRING=your-acs-connection
REDIS_CONNECTION_STRING=your-redis-string

# Frontend (.env)
VITE_BACKEND_URL=ws://localhost:8010
```

## **🏭 Infrastructure Requirements**

### **Required Azure Services**
- **Azure Communication Services** - Phone numbers, call automation, media streaming
- **Azure Speech Services** - Real-time STT/TTS processing  
- **Azure OpenAI** - GPT models for agent responses
- **Azure Redis Cache** - Session state and conversation memory
- **Azure Cosmos DB** - Conversation history and persistent data

### **Deployment Options**
- **Terraform**: `infra/terraform/` - Automated provisioning
- **Azure Developer CLI**: `azd up` - Quick deployment
- **Manual**: Azure Portal setup

### **Local Development with ACS**
```bash
cd scripts/
./start_devtunnel_host.sh  # Exposes backend for ACS webhooks
```
Update `BASE_URL` environment variable with tunnel URL.

### **Useful Scripts**
- `scripts/start_backend.py` - Backend with validation
- `scripts/start_frontend.sh` - React dev server
- `scripts/start_devtunnel_host.sh` - ACS integration tunnel

**📖 Detailed Documentation**: [Deployment Guide](../../docs/DeploymentGuide.md)


