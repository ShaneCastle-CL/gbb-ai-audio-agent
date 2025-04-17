# 🧠 RTMedAgent – Real-Time Voice AI Assistant (Browser-Based)

Enable **real-time voice-to-voice healthcare interactions** using Azure Speech Services and GPT. This browser-based application listens to patient speech, interprets intent using AI, and responds with synthesized speech via Azure Text-to-Speech (TTS)—all in real time.

## 📂 Folder Structure

```
usecases/
└── browser_RTMedAgent/
    ├── backend/               # WebSocket server with GPT integration (Python)
    ├── frontend/              # React + Vite UI powered by Azure Speech SDK
    ├── test_cases_scenarios/  # Optional test scripts and scenarios
    └── README.md              # This file
```

## 🧪 Use Case Summary

> #### **📝 Real-Time Voice AI for Healthcare**
>
> RTMedAgent showcases how to deliver real-time, AI-driven healthcare conversations using Azure and OpenAI services. It transforms natural patient speech into actionable, structured outcomes through a seamless, interactive system.

## 🚀 Getting Started

### 1. 🔧 Start the Backend

Navigate to the `backend` folder and start the WebSocket server:

```bash
cd usecases/browser_RTMedAgent/backend
pip install -r requirements.txt
python server.py
```

✅ The WebSocket server will start at: `ws://localhost:8010/realtime`

### 2. 💻 Start the Frontend

In a new terminal, navigate to the `frontend` folder and start the UI:

```bash
cd usecases/browser_RTMedAgent/frontend
npm install
npm run dev
```

✅ The UI will be available at: `http://localhost:5173`

### 🔑 Environment Setup (Optional)

If supported, create a `.env` file in the `frontend` directory with the following variables:

```env
VITE_AZURE_SPEECH_KEY=your_speech_key
VITE_AZURE_REGION=your_region
VITE_WS_URL=ws://localhost:8010/realtime
```

If `.env` is not supported, manually update these constants in `App.jsx`.

## 🛠️ System Overview

- **🎤 Speech-to-Text (STT):** Azure Speech SDK
- **🧠 AI Reasoning:** Azure OpenAI GPT (via backend)
- **🔊 Text-to-Speech (TTS):** Azure Neural Voices
- **🔁 Real-Time Streaming:** WebSocket for bidirectional communication
- **🖥️ Frontend:** React + Vite

This system enables seamless, real-time voice interactions for healthcare applications.