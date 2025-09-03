# **Real-Time Voice Agent Backend**

**Real-time voice AI system** for enterprise phone calls via Azure Communication Services.

### **Core Flow**

```
Phone Call → ACS → WebSocket → STT → Multi-Agent AI → TTS → Audio Response
```

### **Key Design Decisions**

- **Multi-Agent Orchestration**: Specialized agents (Auth, Billing, Support) vs monolithic prompt. Easier debugging, better context handling.
- **Connection Pooling**: Pre-warmed Azure client pools. Avoid cold start latency on high-concurrency calls.
- **WebSocket Streaming**: Real-time audio requires streaming. HTTP too slow for natural conversation (<2s response time).
- **Session State in Redis**: Agents remember conversation context. Survives WebSocket disconnects.

### **Production Considerations**

- **Latency Optimized**: Buffer tuning, async pools, optimized Azure service calls
- **Resource Cleanup**: Robust WebSocket disconnect handling, no connection leaks  
- **Horizontal Scale**: Async architecture handles hundreds of concurrent calls
- **Observability**: OpenTelemetry traces, health endpoints, agent decision logging

### **Adding Features**
New agent: YAML config → Handler function → Register in orchestrator. Core audio/Azure integration untouched.

### **Project Structure**

```
backend/
├── main.py              # FastAPI app with connection pooling
├── api/v1/              # V1 API endpoints 
├── config/              # Environment configuration
└── src/                 # Core services and utilities
```

## **V1 API Endpoints**

### **WebSocket Endpoints**
- **`/api/v1/media/stream`** - ACS media streaming with audio processing
- **`/api/v1/realtime/conversation`** - Real-time conversation with STT/TTS
- **`/api/v1/realtime/dashboard`** - Dashboard relay with connection tracking

### **REST Endpoints**  
- **`/api/v1/calls/*`** - Call management (initiate, hangup, status)
- **`/api/v1/health`** - Health checks and system status

### **API Layer Structure**
```
api/v1/
├── endpoints/           # WebSocket and REST handlers
│   ├── calls.py         # ACS call management
│   ├── media.py         # Media streaming WebSocket
│   ├── realtime.py      # Real-time conversation WebSocket
│   └── health.py        # Health monitoring
├── handlers/            # Business logic handlers
├── schemas/             # Pydantic models
└── router.py            # Route registration
```

### **Environment Configuration**
```
config/
├── app_config.py        # Main application configuration
├── app_settings.py      # Agent and environment settings
├── connection_config.py # WebSocket and session limits
└── feature_flags.py     # Feature toggles
```

## **Core Application Architecture**

### **Agent System (ARTAgent Framework)**
```
src/agents/              # YAML-driven agent framework
├── base.py              # ARTAgent class for agent creation
├── agent_store/         # Agent YAML configurations
├── prompt_store/        # Jinja prompt templates
├── tool_store/          # Agent tool registry
└── README.md            # Agent creation guide
```

### **Orchestration Engine**
```
src/orchestration/       # Multi-agent routing and coordination
├── orchestrator.py      # Main routing entry point
├── registry.py          # Agent registration system
├── auth.py              # Authentication agent handler
├── specialists.py       # Specialist agent handlers
├── greetings.py         # Agent handoff management
├── gpt_flow.py          # GPT response processing
├── tools.py             # Tool execution framework
├── termination.py       # Session termination logic
├── latency.py           # Performance monitoring
└── README.md            # Orchestrator guide
```

### **Azure Services Integration**
```
src/services/            # External service integrations
├── speech_services.py   # Azure Speech STT/TTS
├── redis_services.py    # Session state management
├── openai_services.py   # Azure OpenAI integration
├── cosmosdb_services.py # CosmosDB document storage
└── acs/                 # Azure Communication Services
```

### **Session Management**
```
src/sessions/            # WebSocket session lifecycle
├── session_statistics.py # Session metrics and monitoring
└── __init__.py          # Session management utilities
```

### **WebSocket Utilities**
```
src/ws_helpers/          # WebSocket session management
├── shared_ws.py         # Shared WebSocket utilities
└── envelopes.py         # Message envelope handling
```

### **Core Utilities**
```
src/utils/               # Core utilities and helpers
├── tracing.py           # OpenTelemetry tracing
└── auth.py              # Authentication utilities
```

### **Connection Pools (Global)**
```
src/pools/               # Connection pooling (shared across apps)
├── async_pool.py        # Async connection pools
├── connection_manager.py # Thread-safe connections
├── session_manager.py   # Session lifecycle management
├── session_metrics.py   # Session monitoring
├── websocket_manager.py # WebSocket connection pooling
├── aoai_pool.py         # Azure OpenAI connection pool
└── dedicated_tts_pool.py # Dedicated TTS connection pool
```

## **Key Features**

- **Real-time WebSocket Streaming** - Low-latency audio and conversation processing
- **Azure Service Integration** - ACS, Speech Services, OpenAI native support
- **Connection Pooling** - Optimized for high-concurrency connections
- **Session Management** - Persistent state with Redis backend
- **Production Ready** - Comprehensive logging, tracing, health monitoring

## **WebSocket Flow**

```
Client → WebSocket → Handler → Azure Services → Response → Client
```

1. **WebSocket Connection** - Connect via `/api/v1/media/stream` or `/api/v1/realtime/conversation`
2. **Audio Processing** - Real-time STT with Azure Speech
3. **AI Response** - Azure OpenAI generates contextual responses  
4. **Speech Synthesis** - Azure Speech TTS for voice responses
5. **Real-time Streaming** - Audio/text streamed back to client


