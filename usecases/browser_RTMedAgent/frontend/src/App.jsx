import React, { useEffect, useRef, useState } from 'react';
import {
  AudioConfig,
  SpeechConfig,
  SpeechRecognizer,
  PropertyId
} from 'microsoft-cognitiveservices-speech-sdk';

const AZURE_SPEECH_KEY = import.meta.env.VITE_AZURE_SPEECH_KEY;
const AZURE_REGION    = import.meta.env.VITE_AZURE_REGION;
const WS_URL          = import.meta.env.VITE_WS_URL;
const API_BASE_URL    = import.meta.env.VITE_API_BASE_URL;
export default function RealTimeVoiceApp() {
  const [transcript, setTranscript] = useState('');
  const [log, setLog]             = useState('');
  const [recording, setRecording] = useState(false);
  const socketRef   = useRef(null);
  const recognizerRef = useRef(null);
  const containerRef  = useRef(null);
  const [targetPhoneNumber, setTargetPhoneNumber] = useState(''); // State for phone number input

  const startACSCall = async () => {
    // Basic validation for the phone number input
    if (!targetPhoneNumber || !/^\+\d+$/.test(targetPhoneNumber)) {
      alert('Please enter a valid phone number in E.164 format (e.g., +15551234567)');
      return;
    }
    try {
      const response = await fetch(`${API_BASE_URL}/api/call`, {
        method: 'POST',
        body: JSON.stringify({ target_number: targetPhoneNumber }), // Use 'target_number' and the state variable
        headers: { 'Content-Type': 'application/json' },
      });
      const result = await response.json();
      if (response.ok) {
        appendLog(`Call initiated successfully: ${result.message}`);
      } else {
        appendLog(`Error initiating call: ${result.detail || response.statusText}`);
      }
    } catch (error) {
      appendLog(`Network or fetch error initiating call: ${error}. Please check if the backend server is running at ${API_BASE_URL} and CORS is enabled.`);
      console.error("Error initiating call:", error);
    }
  };
    
  useEffect(() => {
    return () => stopRecognition();
  }, []);

  useEffect(() => {
    // auto‑scroll chat
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [transcript]);

  const appendLog = (message) => {
    setLog(prev => prev + `\n${new Date().toLocaleTimeString()} - ${message}`);
  };

  const startRecognition = async () => {
    // Initialize speech recognizer
    const speechConfig = SpeechConfig.fromSubscription(AZURE_SPEECH_KEY, AZURE_REGION);
    speechConfig.speechRecognitionLanguage = 'en-US';
    const audioConfig = AudioConfig.fromDefaultMicrophoneInput();
    const recognizer = new SpeechRecognizer(speechConfig, audioConfig);
    recognizerRef.current = recognizer;

    // —— VAD / Segmentation tuning ——
    recognizer.properties.setProperty(
      PropertyId.Speech_SegmentationSilenceTimeoutMs,
      '800'      // end utterance after 0.8 s of silence
    );
    recognizer.properties.setProperty(
      PropertyId.Speech_SegmentationStrategy,
      'Semantic'
    );

    // interim‐results: user started speaking → interrupt in-flight TTS
    let lastInterrupt = 0;
    recognizer.recognizing = (_, e) => {
      const text = e.result.text.trim();
      if (text && socketRef.current?.readyState === WebSocket.OPEN) {
        const now = Date.now();
        if (now - lastInterrupt > 1000) {
          socketRef.current.send(JSON.stringify({ type: 'interrupt' }));
          appendLog('→ Sent interrupt to backend');
          lastInterrupt = now;
        }
      }
    };

    // final (end‑of‑utterance) results
    recognizer.recognized = (s, e) => {
      if (e.result.reason !== 0) {
        const text = e.result.text.trim();
        if (text) {
          setTranscript(prev => prev + `\nUser: ${text}`);
          appendLog('Final transcript: ' + text);
          sendToBackend(text);
        }
      }
    };

    recognizer.startContinuousRecognitionAsync();
    setRecording(true);
    appendLog('Recognition started');

    // Open WebSocket once recognition begins
    const socket = new WebSocket(WS_URL);
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;

    socket.onopen = () => appendLog('WebSocket open');
    socket.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        try {
          const { type, content, message } = JSON.parse(event.data);
          if (type === 'assistant' || type === 'status') {
            const txt = content || message;
            setTranscript(prev => prev + `\nAssistant: ${txt}`);
            appendLog('Assistant responded');
          }
        } catch {
          appendLog('Ignored non-JSON');
        }
      } else {
        // binary = audio buffer
        const audioCtx = new AudioContext();
        const buf = await event.data.arrayBuffer();
        const audioBuffer = await audioCtx.decodeAudioData(buf);
        const src = audioCtx.createBufferSource();
        src.buffer = audioBuffer;
        src.connect(audioCtx.destination);
        src.start();
        appendLog('Audio played');
      }
    };
  };

  const stopRecognition = () => {
    if (recognizerRef.current) {
      recognizerRef.current.stopContinuousRecognitionAsync();
    }
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      // send the same interrupt to cut off TTS
      socketRef.current.send(JSON.stringify({ type: 'interrupt' }));
      socketRef.current.close();
    }
    setRecording(false);
    appendLog('Recognition stopped');
  };

  const sendToBackend = (text) => {
    window.speechSynthesis?.cancel();
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ text }));
    }
  };

  // format transcript lines
  const messages = transcript
    .split('\n')
    .filter(line => line.startsWith('User:') || line.startsWith('Assistant:'));

  return (
    <div style={{
      fontFamily: 'Segoe UI, Roboto, sans-serif',
      background: '#1F2933',
      color: '#E5E7EB',
      minHeight: '100vh',
      padding: 32,
      borderRadius: 12,
      boxShadow: '0 4px 12px rgba(0,0,0,0.2)'
    }}>
      {/* Title */}
      <div style={{ maxWidth: 800, margin: '0 auto 40px', textAlign: 'center' }}>
        <h1 style={{ fontSize: '2.75rem', fontWeight: 700, marginBottom: 20 }}>
          🎙️ RTMedAgent
        </h1>
        <p style={{ fontSize: '1.15rem', lineHeight: 1.8, color: '#9CA3AF' }}>
          Transforming patient care with real-time, intelligent voice interactions powered by Azure AI.
        </p>
      </div>

      {/* Chat Pane */}
      <div style={{
        maxWidth: 800,
        margin: '0 auto',
        background: '#263238',
        borderRadius: 12,
        padding: 16,
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        height: 400,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column'
      }}>
        <div
          ref={containerRef}
          style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', paddingBottom: 16 }}
        >
          {messages.map((msg, idx) => {
            const isUser = msg.startsWith('User:');
            const text   = msg.replace(isUser ? 'User: ' : 'Assistant: ', '').trim();
            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  justifyContent: isUser ? 'flex-end' : 'flex-start',
                  marginBottom: 16
                }}
              >
                <div style={{
                  background: isUser ? '#0078D4' : '#394B59',
                  color: '#fff',
                  padding: '12px 16px',
                  borderRadius: 20,
                  maxWidth: '75%',
                  lineHeight: 1.5,
                  boxShadow: '0 2px 6px rgba(0,0,0,0.2)'
                }}>
                  {text}
                  <span style={{
                    display: 'block',
                    fontSize: '0.8rem',
                    color: '#B0BEC5',
                    marginTop: 8,
                    textAlign: isUser ? 'right' : 'left'
                  }}>
                    {isUser ? '👤 User' : '🤖 Assistant'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Controls */}
      <div style={{ textAlign: 'center', margin: '24px 0' }}>
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
            boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
            transition: 'background 0.3s ease'
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
        <h2 style={{ color: '#fff', marginBottom: 8 }}>System Logs</h2>
        <pre style={{
          background: '#17202A',
          padding: 12,
          borderRadius: 8,
          color: '#BFC9CA',
          fontSize: '0.9rem',
          overflowX: 'auto',
          maxHeight: 200
        }}>
          {log}
        </pre>
      </div>
    </div>
  );
}
