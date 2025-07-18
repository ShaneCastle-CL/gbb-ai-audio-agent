// src/RealTimeVoiceApp.jsx
import React, { useEffect, useRef, useState } from 'react';
import {
  AudioConfig,
  SpeechConfig,
  SpeechRecognizer,
  PropertyId,
} from 'microsoft-cognitiveservices-speech-sdk';
import VoiceSphere from './components/VoiceSphere';
import "reactflow/dist/style.css";
import { useHealthMonitor } from "./hooks/useHealthMonitor";
import HealthStatusIndicator from "./components/HealthStatusIndicator";

/* ------------------------------------------------------------------ *
 *  ENV VARS
 * ------------------------------------------------------------------ */
const {
  VITE_AZURE_SPEECH_KEY: AZURE_SPEECH_KEY,
  VITE_AZURE_REGION:     AZURE_REGION,
  VITE_BACKEND_BASE_URL: API_BASE_URL,
} = import.meta.env;


const WS_URL = API_BASE_URL.replace(/^https?/, "wss");

/* ------------------------------------------------------------------ *
 *  STYLES
 * ------------------------------------------------------------------ */
const styles = {
  root: {
    width: "768px",
    maxWidth: "768px", // Expanded from iPad width
    fontFamily: "Segoe UI, Roboto, sans-serif",
    background: "#f8fafc",
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    color: "#1e293b",
    position: "relative",
    alignItems: "center",
    justifyContent: "center",
    padding: "10px",
    border: "5px solid #0e4bf3ff",
  },
  
  // Main iPad-sized container
  mainContainer: {
    width: "100%",
    maxWidth: "100%", // Expanded from iPad width
    height: "90vh",
    maxHeight: "900px", // Adjusted height
    background: "white",
    borderRadius: "20px",
    boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
    border: "5px solid #ce1010ff",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  
  // Waveform section (top third)
  waveformSection: {
    backgroundColor: "#dbeafe",
    padding: "40px 20px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    borderBottom: "1px solid #e2e8f0",
    height: "25%",
    minHeight: "200px",
    position: "relative",
  },
  
  waveformSectionTitle: {
    fontSize: "14px",
    fontWeight: "600",
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "10px",
  },
  
  // Section divider line
  sectionDivider: {
    position: "absolute",
    bottom: "-1px",
    left: "15%",
    right: "15%",
    height: "3px",
    backgroundColor: "#3b82f6",
    borderRadius: "1px",
  },
  
  waveformContainer: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    height: "100%",
  },
  
  waveformSvg: {
    width: "100%",
    height: "60px",
    maxWidth: "400px",
  },
  
  // Chat section (middle section)
  chatSection: {
    flex: 1,
    padding: "40px 60px",
    width: "100%",
    overflowY: "auto",
    backgroundColor: "#ffffff",
    borderBottom: "1px solid #e2e8f0",
    display: "flex",
    flexDirection: "column",
    position: "relative",
  },
  
  chatSectionHeader: {
    textAlign: "center",
    marginBottom: "30px",
    paddingBottom: "20px",
    borderBottom: "1px solid #f1f5f9",
  },
  
  chatSectionTitle: {
    fontSize: "14px",
    fontWeight: "600",
    color: "#64748b",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    marginBottom: "5px",
  },
  
  chatSectionSubtitle: {
    fontSize: "12px",
    color: "#94a3b8",
    fontStyle: "italic",
  },
  
  // Chat section visual indicator
  chatSectionIndicator: {
    position: "absolute",
    left: "0",
    top: "0",
    bottom: "0",
    width: "3px",
    backgroundColor: "#3b82f6",
  },
  
  messageContainer: {
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    flex: 1,
    overflowY: "auto",
  },
  
  // User message (right aligned - gray bubble)
  userMessage: {
    alignSelf: "flex-end",
    maxWidth: "75%",
  },
  
  userBubble: {
    background: "#f1f5f9",
    color: "#1e293b",
    padding: "12px 16px",
    borderRadius: "20px",
    fontSize: "14px",
    lineHeight: "1.5",
    border: "1px solid #e2e8f0",
    boxShadow: "0 2px 8px rgba(0,0,0,0.05)",
  },
  
  // Assistant message (left aligned - teal bubble)
  assistantMessage: {
    alignSelf: "flex-start",
    maxWidth: "80%",
  },
  
  assistantBubble: {
    background: "#67d8ef",
    color: "white",
    padding: "12px 16px",
    borderRadius: "20px",
    fontSize: "14px",
    lineHeight: "1.5",
    boxShadow: "0 2px 8px rgba(103,216,239,0.3)",
  },
  
  // Control section (bottom third)
  controlSection: {
    padding: "20px",
    backgroundColor: "#dbeafe",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    height: "15%",
    minHeight: "100px",
    borderTop: "1px solid #e2e8f0",
    position: "relative",
  },
  
  controlContainer: {
    display: "flex",
    gap: "16px",
    background: "white",
    padding: "12px 20px",
    borderRadius: "50px",
    boxShadow: "0 8px 32px rgba(0,0,0,0.12)",
    border: "1px solid #e2e8f0",
    width: "fit-content",
  },
  
  controlButton: (isActive, variant = 'default') => ({
    width: "56px",
    height: "56px",
    borderRadius: "50%",
    border: "none",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
    fontSize: "20px",
    transition: "all 0.2s ease",
    background: variant === 'phone' ? "#67d8ef" : 
                variant === 'close' ? "#f1f5f9" :
                isActive ? "#67d8ef" : "#f1f5f9",
    color: variant === 'phone' || isActive ? "white" : "#64748b",
    transform: isActive ? "scale(1.05)" : "scale(1)",
    boxShadow: isActive ? "0 4px 16px rgba(103,216,239,0.4)" : "0 2px 8px rgba(0,0,0,0.05)",
  }),
  
  // Health indicator in top right
  healthIndicator: {
    position: "absolute",
    top: "20px",
    right: "20px",
    zIndex: 10,
  },
  
  // Input section for phone calls
  phoneInputSection: {
    position: "absolute",
    bottom: "140px",
    right: "20px",
    background: "white",
    padding: "20px",
    borderRadius: "16px",
    boxShadow: "0 8px 32px rgba(0,0,0,0.12)",
    border: "1px solid #e2e8f0",
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    minWidth: "240px",
    zIndex: 90,
  },
  
  phoneInput: {
    padding: "12px 16px",
    border: "1px solid #d1d5db",
    borderRadius: "8px",
    fontSize: "14px",
    outline: "none",
    transition: "border-color 0.2s ease",
  },
  
  phoneButton: (isActive) => ({
    padding: "12px 20px",
    background: isActive ? "#ef4444" : "#67d8ef",
    color: "white",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
    fontSize: "14px",
    fontWeight: "600",
    transition: "all 0.2s ease",
  }),
};

/* ------------------------------------------------------------------ *
 *  WAVEFORM COMPONENT
 * ------------------------------------------------------------------ */
const WaveformVisualization = ({ isActive, speaker }) => {
  const [waveOffset, setWaveOffset] = useState(0);
  const [amplitude, setAmplitude] = useState(5);
  const animationRef = useRef();
  
  useEffect(() => {
    if (!isActive) {
      setAmplitude(3);
      return;
    }
    
    // Higher amplitude when active
    setAmplitude(15 + Math.random() * 10);
    
    // Animate the wave
    const animate = () => {
      setWaveOffset(prev => (prev + 2) % 360);
      setAmplitude(prev => {
        const baseAmplitude = isActive ? 15 : 3;
        const variation = isActive ? Math.random() * 15 : Math.random() * 2;
        return baseAmplitude + variation;
      });
      animationRef.current = requestAnimationFrame(animate);
    };
    
    animationRef.current = requestAnimationFrame(animate);
    
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [isActive]);
  
  // Generate wave path
  const generateWavePath = () => {
    const width = 750;
    const height = 100;
    const centerY = height / 2;
    const frequency = 0.02; // How many waves across the width
    const points = 200; // Number of points to draw
    
    let path = `M 0 ${centerY}`;
    
    for (let i = 0; i <= points; i++) {
      const x = (i / points) * width;
      const y = centerY + Math.sin((x * frequency + waveOffset * 0.1)) * amplitude;
      path += ` L ${x} ${y}`;
    }
    
    return path;
  };
  
  // Generate multiple wave layers for richer visualization
  const generateMultipleWaves = () => {
    const waves = [];
    const baseColor = isActive ? "#67d8ef" : "#11d483ff";
    const opacity = isActive ? 0.8 : 0.4;
    
    // Main wave
    waves.push(
      <path
        key="wave1"
        d={generateWavePath()}
        stroke={baseColor}
        strokeWidth="3"
        fill="none"
        opacity={opacity}
        strokeLinecap="round"
      />
    );
    
    // Secondary wave (slightly offset and smaller)
    const secondaryPath = generateSecondaryWave();
    waves.push(
      <path
        key="wave2"
        d={secondaryPath}
        stroke={baseColor}
        strokeWidth="2"
        fill="none"
        opacity={opacity * 0.6}
        strokeLinecap="round"
      />
    );
    
    return waves;
  };
  
  const generateSecondaryWave = () => {
    const width = 750;
    const height = 100;
    const centerY = height / 2;
    const frequency = 0.025; // Slightly different frequency
    const points = 200;
    
    let path = `M 0 ${centerY}`;
    
    for (let i = 0; i <= points; i++) {
      const x = (i / points) * width;
      const y = centerY + Math.sin((x * frequency + waveOffset * 0.15)) * (amplitude * 0.6);
      path += ` L ${x} ${y}`;
    }
    
    return path;
  };
  
  return (
    <div style={styles.waveformContainer}>
      <svg style={styles.waveformSvg} viewBox="0 0 500 80" preserveAspectRatio="xMidYMid meet">
        {generateMultipleWaves()}
      </svg>
    </div>
  );
};

/* ------------------------------------------------------------------ *
 *  CHAT BUBBLE
 * ------------------------------------------------------------------ */
const ChatBubble = ({ message }) => {
  const { speaker, text, isTool, streaming } = message;
  const isUser = speaker === "User";
  
  if (isTool) {
    return (
      <div style={{ ...styles.assistantMessage, alignSelf: "center" }}>
        <div style={{
          ...styles.assistantBubble,
          background: "#8b5cf6",
          textAlign: "center",
          fontSize: "14px",
        }}>
          {text}
        </div>
      </div>
    );
  }
  
  return (
    <div style={isUser ? styles.userMessage : styles.assistantMessage}>
      <div style={isUser ? styles.userBubble : styles.assistantBubble}>
        {text.split("\n").map((line, i) => (
          <div key={i}>{line}</div>
        ))}
        {streaming && <span style={{ opacity: 0.7 }}>▌</span>}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ *
 *  MAIN COMPONENT
 * ------------------------------------------------------------------ */
export default function RealTimeVoiceApp() {
  /* ---------- state ---------- */
  const [messages, setMessages] = useState([
    { speaker: "User", text: "What's the weather like today?" },
    { speaker: "Assistant", text: "The weather is sunny and 72 degrees." }
  ]);
  const [log, setLog]                 = useState("");
  const [recording, setRecording]     = useState(false);
  const [targetPhoneNumber, setTargetPhoneNumber] = useState("");
  const [callActive, setCallActive]   = useState(false);
  const [activeSpeaker, setActiveSpeaker] = useState(null);
  const [showPhoneInput, setShowPhoneInput] = useState(false);

  /* ---------- health monitoring ---------- */
  const { 
    healthStatus = { isHealthy: null, lastChecked: null, responseTime: null, error: null },
    readinessStatus = { status: null, timestamp: null, responseTime: null, checks: [], lastChecked: null, error: null },
    overallStatus = { isHealthy: false, hasWarnings: false, criticalErrors: [] },
    refresh = () => {} 
  } = useHealthMonitor({
    baseUrl: API_BASE_URL,
    healthInterval: 30000,
    readinessInterval: 15000,
    enableAutoRefresh: true,
  });


  /* ---------- mind‑map state ---------- */
  // const rootUser      = { id:"user-root",      data:{label:"👤 User"},      position:{x:-220,y:0},
  //                         style:{background:"#0F766E",color:"#fff"} };
  // const rootAssistant = { id:"assistant-root", data:{label:"🤖 Assistant"}, position:{x: 220,y:0},
  //                         style:{background:"#4338CA",color:"#fff"} };

  // const [nodes, setNodes] = useState([rootUser, rootAssistant]);
  // const [edges, setEdges] = useState([]);

  // all of our former “mind-map” state now lives here:
  const [functionCalls, setFunctionCalls] = useState([]);
  const [callResetKey, setCallResetKey]   = useState(0);

  /* ---------- refs ---------- */
  // const idRef        = useRef(0);
  const chatRef      = useRef(null);
  const socketRef    = useRef(null);
  const recognizerRef= useRef(null);

  const appendLog = m => setLog(p => `${p}\n${new Date().toLocaleTimeString()} - ${m}`);

  /* ---------- scroll chat on new message ---------- */
  useEffect(()=>{
    if(chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  },[messages]);

  /* ---------- teardown on unmount ---------- */
  useEffect(()=>()=> stopRecognition(),[]);

  /* ---------- derive callActive from logs ---------- */
  useEffect(()=>{
    if (log.includes("Call connected"))  setCallActive(true);
    if (log.includes("Call ended"))      setCallActive(false);
  },[log]);

  /* ---------- STOP & RESET on end ---------- */
  const stopRecognition = () => {
    recognizerRef.current?.stopContinuousRecognitionAsync();
    if (socketRef.current?.readyState === WebSocket.OPEN)
      socketRef.current.close();

    setRecording(false);
    setActiveSpeaker(null);

    // reset all sphere state
    setFunctionCalls([]);
    setCallResetKey(k=>k+1);

    appendLog("🛑 Recognition stopped");
  };

  /* ------------------------------------------------------------------ *
   *  SEND USER SPEECH → BACKEND
   * ------------------------------------------------------------------ */
  const sendToBackend = text => {
    if (socketRef.current?.readyState === WebSocket.OPEN)
      socketRef.current.send(JSON.stringify({ text }));
  };

  const handleUserSpeech = userText => {
    setMessages(ms => [...ms, { speaker:"User", text:userText }]);
    setActiveSpeaker("User");
    appendLog(`User: ${userText}`);
    sendToBackend(userText);
  };

  /* ------------------------------------------------------------------ *
   *  START RECOGNITION + WS
   * ------------------------------------------------------------------ */
  const startRecognition = () => {
    setMessages([]);
    setFunctionCalls([]);
    setCallResetKey(k=>k+1);

    /* Azure Speech config */
    const cfg = SpeechConfig.fromSubscription(AZURE_SPEECH_KEY, AZURE_REGION);
    cfg.speechRecognitionLanguage = "en-US";
    const rec = new SpeechRecognizer(cfg, AudioConfig.fromDefaultMicrophoneInput());
    rec.properties.setProperty(PropertyId.Speech_SegmentationSilenceTimeoutMs, "800");
    rec.properties.setProperty(PropertyId.Speech_SegmentationStrategy, "Semantic");
    recognizerRef.current = rec;

    let lastInterrupt = Date.now();
    rec.recognizing = (_, e) => {
      if (e.result.text.trim() &&
          socketRef.current?.readyState === WebSocket.OPEN &&
          Date.now()-lastInterrupt > 1000)
      {
        socketRef.current.send(JSON.stringify({ type:"interrupt" }));
        appendLog("→ Sent interrupt");
        lastInterrupt = Date.now();
      }
    };
    rec.recognized = (_, e) => {
      const txt = e.result.text.trim();
      if (txt) handleUserSpeech(txt);
    };

    rec.startContinuousRecognitionAsync();
    setRecording(true);
    appendLog("🎤 Recognition started");

    /* WebSocket for assistant streaming */
    const socket = new WebSocket(`${WS_URL}/realtime`);
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;
    socket.onopen  = () => appendLog("🔌 WS open");
    socket.onclose = () => appendLog("🔌 WS closed");
    socket.onmessage = handleSocketMessage;
  };

  /* ------------------------------------------------------------------ *
   *  HANDLE INCOMING SOCKET MESSAGES
   * ------------------------------------------------------------------ */
  const handleSocketMessage = async event => {
    // audio
    if (typeof event.data !== "string") {
      const ctx = new AudioContext();
      const buf = await event.data.arrayBuffer();
      const audioBuf = await ctx.decodeAudioData(buf);
      const src = ctx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(ctx.destination);
      src.start();
      appendLog("🔊 Audio played");
      return;
    }

    // JSON frames
    let payload;
    try { payload = JSON.parse(event.data); }
    catch { appendLog("Ignored non-JSON frame"); return; }

    const { type, content="", message="", tool, pct, status, elapsedMs, result, error } = payload;
    const txt = content || message;

    // streaming assistant
    if (type==="assistant_streaming") {
      setActiveSpeaker("Assistant");
      setMessages(prev => {
        if (prev.at(-1)?.streaming) {
          return prev.map((m,i) =>
            i===prev.length-1 ? { ...m, text: txt } : m
          );
        }
        return [...prev, { speaker:"Assistant", text: txt, streaming:true }];
      });
      return;
    }

    // final assistant
    if (type==="assistant"||type==="status") {
      setActiveSpeaker("Assistant");
      setMessages(prev => {
        if (prev.at(-1)?.streaming) {
          return prev.map((m,i) =>
            i===prev.length-1 ? { speaker:"Assistant", text: txt } : m
          );
        }
        return [...prev, { speaker:"Assistant", text: txt }];
      });
      appendLog("🤖 Assistant responded");
      return;
    }

    // tool start
    if (type==="tool_start") {
      const callId = `${tool}-${Date.now()}`;
      setFunctionCalls(fc => [...fc, { id:callId, name:tool, status:"running" }]);
      setMessages(prev => [
        ...prev,
        { speaker:"Assistant", isTool:true, text:`🛠️ tool ${tool} started 🔄` }
      ]);
      appendLog(`⚙️ ${tool} started`);
      return;
    }

    // tool progress
    if (type==="tool_progress") {
      setMessages(prev =>
        prev.map((m,i,arr) =>
          i===arr.length-1 && m.text.startsWith(`🛠️ tool ${tool}`)
            ? { ...m, text:`🛠️ tool ${tool} ${pct}% 🔄` }
            : m
        )
      );
      appendLog(`⚙️ ${tool} ${pct}%`);
      return;
    }

    // tool end
    if (type==="tool_end") {
      setFunctionCalls(fc =>
        fc.map(f =>
          f.name===tool
            ? { ...f, status: status==="success" ? "completed" : "error" }
            : f
        )
      );
      // float out then remove
      setTimeout(() => {
        setFunctionCalls(fc => fc.filter(f => f.name!==tool));
      }, 2000);

      const finalText = status==="success"
        ? `🛠️ tool ${tool} completed ✔️\n${JSON.stringify(result, null,2)}`
        : `🛠️ tool ${tool} failed ❌\n${error}`;
      setMessages(prev =>
        prev.map((m,i,arr) =>
          i===arr.length-1 && m.text.startsWith(`🛠️ tool ${tool}`)
            ? { ...m, text: finalText }
            : m
        )
      );
      appendLog(`⚙️ ${tool} ${status} (${elapsedMs}ms)`);
      return;
    }
  };

  /* ------------------------------------------------------------------ *
   *  OUTBOUND ACS CALL
   * ------------------------------------------------------------------ */
  const startACSCall = async () => {
    if (!/^\+\d+$/.test(targetPhoneNumber)) {
      alert("Enter phone in E.164 format e.g. +15551234567");
      return;
    }
    try {
      const res = await fetch(`${API_BASE_URL}/api/call`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ target_number: targetPhoneNumber }),
      });
      const json = await res.json();
      if (!res.ok) {
        appendLog(`Call error: ${json.detail||res.statusText}`);
        return;
      }
      // show in chat
      setMessages(m => [
        ...m,
        { speaker:"Assistant", text:`📞 Call started → ${targetPhoneNumber}` }
      ]);
      appendLog("📞 Call initiated");

      // relay WS
      const relay = new WebSocket(`${WS_URL}/relay`);
      relay.onopen = () => appendLog("Relay WS connected");
      relay.onmessage = ({data}) => {
        try {
          const obj = JSON.parse(data);
          if (obj.type?.startsWith("tool_")) {
            handleSocketMessage({ data: JSON.stringify(obj) });
            return;
          }
          const { sender, message } = obj;
          setMessages(m => [...m, { speaker: sender, text: message }]);
          setActiveSpeaker(sender);
          appendLog(`[Relay] ${sender}: ${message}`);
        } catch {
          appendLog("Relay parse error");
        }
      };
      relay.onclose = () => {
        appendLog("Relay WS disconnected");
        setCallActive(false);
        setActiveSpeaker(null);
        setFunctionCalls([]);
        setCallResetKey(k=>k+1);
      };
    } catch(e) {
      appendLog(`Network error starting call: ${e.message}`);
    }
  };

  /* ------------------------------------------------------------------ *
   *  RENDER
   * ------------------------------------------------------------------ */
  return (
    <div style={styles.root}>
      {/* Health Status - Top Right */}
      <div style={styles.healthIndicator}>
        <HealthStatusIndicator
          healthStatus={healthStatus}
          readinessStatus={readinessStatus}
          overallStatus={overallStatus}
          onRefresh={refresh}
          compact={true}
        />
      </div>

      {/* Main iPad-sized Container */}
      <div style={styles.mainContainer}>
        {/* Waveform Section */}
        <div style={styles.waveformSection}>
          <WaveformVisualization 
            isActive={recording || !!activeSpeaker} 
            speaker={activeSpeaker} 
          />
        </div>

        {/* Chat Messages */}
        <div style={styles.chatSection} ref={chatRef}>
          <div style={styles.chatSectionIndicator}></div>
          <div style={styles.messageContainer}>
            {messages.map((message, index) => (
              <ChatBubble key={index} message={message} />
            ))}
          </div>
        </div>

        {/* Control Buttons */}
        <div style={styles.controlSection}>
          <div style={styles.controlContainer}>
            {/* Microphone Button */}
            <button
              style={styles.controlButton(recording)}
              onClick={recording ? stopRecognition : startRecognition}
              title={recording ? "Stop Recording" : "Start Recording"}
            >
              🎤
            </button>
            
            {/* Phone Call Button */}
            <button
              style={styles.controlButton(false, 'phone')}
              onClick={() => setShowPhoneInput(!showPhoneInput)}
              title="Phone Call"
            >
              📞
            </button>
            
            {/* Close Button */}
            <button
              style={styles.controlButton(false, 'close')}
              onClick={stopRecognition}
              title="End Session"
            >
              ✕
            </button>
          </div>
        </div>
      </div>

      {/* Phone Input Panel */}
      {showPhoneInput && (
        <div style={styles.phoneInputSection}>
          <input
            type="tel"
            value={targetPhoneNumber}
            onChange={(e) => setTargetPhoneNumber(e.target.value)}
            placeholder="+15551234567"
            style={styles.phoneInput}
            disabled={callActive}
          />
          <button
            onClick={callActive ? stopRecognition : startACSCall}
            style={styles.phoneButton(callActive)}
          >
            {callActive ? "End Call" : "Call"}
          </button>
        </div>
      )}
    </div>
  );
}
