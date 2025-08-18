# �️ Real-Time Voice Agent Frontend

A React-based real-time voice application that provides an intelligent voice agent interface with WebRTC capabilities, backend health monitoring, and Azure Communication Services integration.

## 🚀 Quick Start

### Prerequisites
- Node.js 18+ and npm
- Backend service running on `http://localhost:8000`

### Installation & Run
```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173`

### Production Build
```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

## 📁 Essential File Structure

The frontend uses a **monolithic architecture** with all functionality consolidated into a single component:

```
frontend/
├── public/                    # Static assets
├── src/
│   ├── main.jsx              # React app entry point
│   ├── App.jsx               # Main app wrapper
│   ├── App.css               # Background styles
│   ├── index.css             # Global styles
│   └── components/
│       └── RealTimeVoiceApp.jsx 
├── package.json              # Dependencies & scripts
├── vite.config.js           # Vite configuration
├── .env                     # Environment variables
└── index.html               # HTML template
```

### 🎯 Core Files (Required)

| File | Purpose | Status |
|------|---------|--------|
| `src/main.jsx` | React DOM entry point | ✅ Required |
| `src/App.jsx` | App wrapper, imports main component | ✅ Required |
| `src/components/RealTimeVoiceApp.jsx` | **Complete voice agent application** | ✅ Required |
| `src/App.css` | Background styling | ✅ Required |
| `src/index.css` | Global CSS reset | ✅ Required |
| `package.json` | Dependencies & npm scripts | ✅ Required |
| `vite.config.js` | Build configuration | ✅ Required |

## 🏗️ Architecture Overview

### Monolithic Design
The app uses a **single-file architecture** where all components are defined inline within `RealTimeVoiceAppOriginal.jsx`:

- **BackendIndicator**: Health monitoring with connection status
- **WaveformVisualization**: Audio-reactive visual feedback  
- **ChatBubble**: Message display with styling
- **HelpButton**: User assistance modal
- **BackendStatisticsButton**: Backend metrics display

### Key Features
- 🎯 **Real-time Voice Processing**: WebAudio API integration
- 🔄 **WebSocket Communication**: Live backend connectivity
- 📞 **Phone Call Integration**: Azure Communication Services
- 📊 **Backend Health Monitoring**: Real-time status indicators
- 🎨 **Fixed-width Design**: 768px professional interface
- 🔍 **Debug Tools**: Component and connection diagnostics

### Environment Configuration
```bash
# .env file
VITE_BACKEND_BASE_URL=http://localhost:8000
```

### Dependencies
- **React 19**: Core framework
- **Vite**: Build tool and dev server
- **Azure Communication Services**: Voice calling
- **Microsoft Cognitive Services**: Speech SDK
- **ReactFlow**: Visualization components
- **Lucide React**: Icons

## 🔧 Development

### Commands
```bash
npm run dev      # Development server (localhost:5173)
npm run build    # Production build
npm run preview  # Preview production build
```

### Browser Support
- WebAudio API compatibility required
- WebSocket support needed

### Backend Integration
The frontend connects to backend APIs:
- Health endpoint: `/api/v1/readiness`
- WebSocket: `/api/v1/ws/call/{call_id}`
- Phone calls: `/api/v1/calls/`

## 🎨 UI Components

All components are **inline within RealTimeVoiceAppOriginal.jsx**:

- **Main Interface**: 768px fixed width with professional styling
- **Voice Controls**: Start/stop recording, phone call buttons
- **Visual Feedback**: Real-time waveform animation
- **Chat Display**: Message bubbles with timestamps
- **Status Indicators**: Backend health and connection status
- **Help System**: Contextual assistance modals

## 🚀 Production Deployment

The app builds to static files and can be deployed to any static hosting service:

```bash
npm run build   # Generates /dist folder
```

Deploy the `/dist` folder to your preferred hosting platform.


