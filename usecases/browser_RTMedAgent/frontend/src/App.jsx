import React, { useEffect, useRef, useState } from 'react';
import {
  AudioConfig,
  SpeechConfig,
  SpeechRecognizer,
  PropertyId,
} from 'microsoft-cognitiveservices-speech-sdk';

const AZURE_SPEECH_KEY = import.meta.env.VITE_AZURE_SPEECH_KEY;
const AZURE_REGION    = import.meta.env.VITE_AZURE_REGION;
const API_BASE_URL = import.meta.env.VITE_BACKEND_BASE_URL;
const WS_URL = API_BASE_URL.replace(/^https?/, 'wss');

export default function RealTimeVoiceApp() {

  /* ------------------------------------------------------------------ *
   *  STATE & REFS
   * ------------------------------------------------------------------ */
  const [messages, setMessages] = useState([]);
  const [log, setLog] = useState('');
  const [recording, setRecording] = useState(false);

  const socketRef = useRef(null);
  const recognizerRef = useRef(null);
  const [targetPhoneNumber, setTargetPhoneNumber] = useState(''); // State for phone number input

  const startACSCall = async () => {
    // Validate the phone number input
    if (!targetPhoneNumber || !/^\+\d+$/.test(targetPhoneNumber)) {
      alert('Please enter a valid phone number in E.164 format (e.g., +15551234567)');
      return;
    }

    try {
      // Initiate the call via the backend API
      const response = await fetch(`${API_BASE_URL}/api/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_number: targetPhoneNumber }),
      });

      const result = await response.json();

      if (!response.ok) {
        appendLog(`Error initiating call: ${result.detail || response.statusText}`);
        return;
      }

      appendLog(`Call initiated successfully: ${result.message}`);

      // Establish a WebSocket connection for relaying messages
      const relaySocket = new WebSocket(`${WS_URL}/relay`);

      relaySocket.onopen = () => appendLog('Relay WebSocket connected');
      relaySocket.onclose = () => appendLog('Relay WebSocket disconnected');
      relaySocket.onerror = (error) => appendLog(`Relay WebSocket error: ${error.message}`);

      relaySocket.onmessage = (event) => {
        try {
          const parsedMessage = JSON.parse(event.data);
          const { sender, message } = parsedMessage;

          if (sender !== 'User' && sender !== 'Assistant') {
            appendLog(`[Relay WS] Received message from ${sender}: ${message}`);
            return;
          }

          setMessages((prevMessages) => [
            ...prevMessages,
            { speaker: sender, text: message },
          ]);
          appendLog(`Relay message: ${message}`);
        } catch (error) {
          appendLog('Error parsing relay WebSocket message');
          console.error('Relay WebSocket message error:', error);
        }
      };
    } catch (error) {
      appendLog(`Network or fetch error initiating call: ${error.message}`);
      console.error('Error initiating call:', error);
    }
  };
    
  const chatRef = useRef(null);

  /* ------------------------------------------------------------------ *
   *  HELPERS
   * ------------------------------------------------------------------ */
  const appendLog = (m) =>
    setLog((p) => `${p}\n${new Date().toLocaleTimeString()} - ${m}`);

  const renderContent = (txt) =>
    txt.split('\n').map((p, i) => (
      <p key={i} style={{ margin: '4px 0' }}>
        {p}
      </p>
    ));

  /* ------------------------------------------------------------------ *
   *  EFFECTS
   * ------------------------------------------------------------------ */
  useEffect(() => {
    if (chatRef.current)
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => () => stopRecognition(), []);

  /* ------------------------------------------------------------------ *
   *  BACKEND COMMUNICATION
   * ------------------------------------------------------------------ */
  const startRecognition = async () => {
    /* === Speech recognizer === */
    const speechCfg = SpeechConfig.fromSubscription(
      AZURE_SPEECH_KEY,
      AZURE_REGION,
    );
    speechCfg.speechRecognitionLanguage = 'en-US';

    const recognizer = new SpeechRecognizer(
      speechCfg,
      AudioConfig.fromDefaultMicrophoneInput(),
    );
    recognizer.properties.setProperty(
      PropertyId.Speech_SegmentationSilenceTimeoutMs,
      '800',
    );
    recognizer.properties.setProperty(
      PropertyId.Speech_SegmentationStrategy,
      'Semantic',
    );
    recognizerRef.current = recognizer;

    let lastInterrupt = Date.now();
    recognizer.recognizing = (_, e) => {
      const partial = e.result.text.trim();
      if (
        partial &&
        socketRef.current?.readyState === WebSocket.OPEN &&
        Date.now() - lastInterrupt > 1000
      ) {
        socketRef.current.send(JSON.stringify({ type: 'interrupt' }));
        appendLog('→ Sent interrupt');
        lastInterrupt = Date.now();
      }
    };

    recognizer.recognized = (_, e) => {
      if (e.result.reason !== 0) {
        const text = e.result.text.trim();
        if (text) {
          setMessages((p) => [...p, { speaker: 'User', text }]);
          appendLog(`User said: ${text}`);
          sendToBackend(text);
        }
      }
    };

    recognizer.startContinuousRecognitionAsync();
    setRecording(true);
    appendLog('🎤 Recognition started');

    /* === WebSocket === */
    const socket = new WebSocket(`${WS_URL}/realtime`);
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;

    socket.onopen = () => appendLog('🔌 WebSocket open');
    socket.onclose = () => appendLog('🔌 WebSocket closed');

    socket.onmessage = async (event) => {
      /* ----- binary branch (TTS audio) ----- */
      if (typeof event.data !== 'string') {
        try {
          const ctx = new AudioContext();
          const buf = await event.data.arrayBuffer();
          const audioBuf = await ctx.decodeAudioData(buf);
          const src = ctx.createBufferSource();
          src.buffer = audioBuf;
          src.connect(ctx.destination);
          src.start();
          appendLog('🔊 Audio played');
        } catch {
          appendLog('⚠️ audio error');
        }
        return;
      }

      /* ----- JSON branch ----- */
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        appendLog('Ignored non-JSON frame');
        return;
      }
      const { type, content = '', message = '' } = payload;
      const txt = content || message;

      /* streaming chunks */
      if (type === 'assistant_streaming') {
        setMessages((prev) => {
          if (prev.length && prev.at(-1).streaming) {
            const u = [...prev];
            u[u.length - 1].text = txt;
            return u;
          }
          return [...prev, { speaker: 'Assistant', text: txt, streaming: true }];
        });
        return;
      }

      /* final assistant */
      if (type === 'assistant' || type === 'status') {
        setMessages((prev) => {
          if (prev.length && prev.at(-1).streaming) {
            const u = [...prev];
            u[u.length - 1] = { speaker: 'Assistant', text: txt };
            return u;
          }
          return [...prev, { speaker: 'Assistant', text: txt }];
        });
        appendLog('🤖 Assistant responded');
        return;
      }

      /* -------- TOOL EVENTS -------- */
      if (type === 'tool_start') {
        setMessages((prev) => [
          ...prev,
          { speaker: 'Assistant', text: `⚙️ ${payload.tool} started` },
        ]);
        appendLog(`⚙️ ${payload.tool} started`);
        return;
      }

      if (type === 'tool_progress') {
        setMessages((prev) =>
          prev.map((m, i, arr) =>
            i === arr.length - 1 && m.text.startsWith(`⚙️ ${payload.tool}`)
              ? { ...m, text: `⚙️ ${payload.tool} ${payload.pct}%` }
              : m,
          ),
        );
        appendLog(`⚙️ ${payload.tool} ${payload.pct}%`);
        return;
      }

      if (type === 'tool_end') {
        const finalText =
          payload.status === 'success'
            ? `⚙️ ${payload.tool} completed\n${JSON.stringify(
                payload.result,
                null,
                2,
              )}`
            : `⚙️ ${payload.tool} failed ❌\n${payload.error}`;
        setMessages((prev) =>
          prev.map((m, i, arr) =>
            i === arr.length - 1 && m.text.startsWith(`⚙️ ${payload.tool}`)
              ? { ...m, text: finalText }
              : m,
          ),
        );
        appendLog(
          `⚙️ ${payload.tool} ${payload.status} (${payload.elapsedMs} ms)`,
        );
        return;
      }
    };
  };

  const stopRecognition = () => {
    recognizerRef.current?.stopContinuousRecognitionAsync();
    socketRef.current?.readyState === WebSocket.OPEN && socketRef.current.close();
    setRecording(false);
    appendLog('🛑 Recognition stopped');
  };

  const sendToBackend = (text) => {
    socketRef.current?.readyState === WebSocket.OPEN &&
      socketRef.current.send(JSON.stringify({ text }));
  };

  /* ------------------------------------------------------------------ *
   *  RENDER
   * ------------------------------------------------------------------ */
  return (
    <div
      style={{
        fontFamily: 'Segoe UI, Roboto, sans-serif',
        background: '#1F2933',
        color: '#E5E7EB',
        minHeight: '100vh',
        padding: 32,
      }}
    >
      {/* ------------ Title ------------ */}
      <div
        style={{ maxWidth: 800, margin: '0 auto 40px', textAlign: 'center' }}
      >
        <h1 style={{ fontSize: '2.75rem', fontWeight: 700, marginBottom: 20 }}>
          🎙️ RTMedAgent
        </h1>
        <p style={{ fontSize: '1.15rem', color: '#9CA3AF' }}>
          Transforming patient care with real-time, intelligent voice
          interactions
        </p>
      </div>

      {/* ------------ Chat Pane ------------ */}
      <div
        style={{
          maxWidth: 800,
          margin: '0 auto',
          background: '#263238',
          borderRadius: 12,
          padding: 16,
          height: 400,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          ref={chatRef}
          style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}
        >
          {messages.map((msg, idx) => {
            const isUser = msg.speaker === 'User';
            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  justifyContent: isUser ? 'flex-end' : 'flex-start',
                  marginBottom: 16,
                }}
              >
                <div
                  style={{
                    background: isUser ? '#0078D4' : '#394B59',
                    color: '#fff',
                    padding: '12px 16px',
                    borderRadius: 20,
                    maxWidth: '75%',
                    lineHeight: 1.5,
                    boxShadow: '0 2px 6px rgba(0,0,0,.2)',
                  }}
                >
                  <span style={{ opacity: msg.streaming ? 0.7 : 1 }}>
                    {renderContent(msg.text)}
                    {msg.streaming && <em style={{ marginLeft: 4 }}>▌</em>}
                  </span>
                  <span
                    style={{
                      display: 'block',
                      fontSize: '0.8rem',
                      color: '#B0BEC5',
                      marginTop: 8,
                      textAlign: isUser ? 'right' : 'left',
                    }}
                  >
                    {isUser ? '👤 User' : '🤖 Assistant'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ------------ Controls ------------ */}
      <div style={{ textAlign: 'center', margin: 24 }}>
        <button
          onClick={recording ? stopRecognition : startRecognition}
          style={{
            padding: '12px 32px',
            fontSize: '1rem',
            borderRadius: 8,
            border: 'none',
            cursor: 'pointer',
            background: recording ? '#D13438' : '#107C10',
            color: '#fff',
            fontWeight: 600,
          }}
        >
          {recording ? '⏹ End Conversation' : '▶ Start Conversation'}
        </button>
      </div>
      {/* Control for ACS Outbound Call */}
      <div style={{ textAlign: 'center', marginBottom: '2.5rem', padding: '1rem', background: '#2C3E50', borderRadius: '8px' }}>
         <h2 style={{marginTop: 0, marginBottom: '1rem', color: '#ECF0F1'}}>📞 Initiate Phone Call (ACS)</h2>
         <input
            type="tel"
            placeholder="+15551234567"
            value={targetPhoneNumber}
            onChange={(e) => setTargetPhoneNumber(e.target.value)}
            style={{ padding: '10px', marginRight: '10px', borderRadius: '5px', border: '1px solid #566573', background: '#34495E', color: '#ECF0F1' }}
         />
        <button
            onClick={startACSCall}
            style={{ /* ... style similar to the other button ... */
                padding: '10px 20px',
                fontSize: '1rem',
                borderRadius: '5px',
                border: 'none',
                cursor: 'pointer',
                backgroundColor: '#3498DB',
                color: '#fff',
                fontWeight: 'bold',
                boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                transition: 'all 0.3s ease-in-out'
            }}
        >
            Call Number
        </button>
      </div>
      {/* Logs */}
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 8 }}>System Logs</h2>
        <pre
          style={{
            background: '#17202A',
            padding: 12,
            borderRadius: 8,
            fontSize: '0.9rem',
            maxHeight: 200,
            overflowX: 'auto',
          }}
        >
          {log}
        </pre>
      </div>
    </div>
  );
}
