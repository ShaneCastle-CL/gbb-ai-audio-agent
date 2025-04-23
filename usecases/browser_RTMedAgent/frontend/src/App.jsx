import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  AudioConfig,
  SpeechConfig,
  SpeechRecognizer,
  PropertyId
} from 'microsoft-cognitiveservices-speech-sdk';

const AZURE_SPEECH_KEY = import.meta.env.VITE_AZURE_SPEECH_KEY;
const AZURE_REGION    = import.meta.env.VITE_AZURE_REGION;
const WS_URL          = import.meta.env.VITE_WS_URL;

export default function RealTimeVoiceApp() {
  const [transcript, setTranscript] = useState('');
  const [log, setLog]             = useState('');
  const [recording, setRecording] = useState(false);
  const socketRef   = useRef(null);
  const recognizerRef = useRef(null);
  const containerRef  = useRef(null);

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

  // render paragraphs and lists from a block of text
  const renderContent = (text) => {
    const lines = text.split('\n');
    const elements = [];
    let listItems = [];

    lines.forEach((line, i) => {
      if (line.trim().startsWith('- ')) {
        listItems.push(line.trim().substring(2));
      } else {
        if (listItems.length) {
          elements.push(
            <ul key={`ul-${i}`} style={{ margin: '8px 0 8px 20px' }}>
              {listItems.map((item, j) => <li key={j}>{item}</li>)}
            </ul>
          );
          listItems = [];
        }
        elements.push(
          <p key={`p-${i}`} style={{ margin: '4px 0' }}>
            {line}
          </p>
        );
      }
    });

    if (listItems.length) {
      elements.push(
        <ul key="ul-final" style={{ margin: '8px 0 8px 20px' }}>
          {listItems.map((item, j) => <li key={j}>{item}</li>)}
        </ul>
      );
    }

    return elements;
  };

  const startRecognition = async () => {
    const speechConfig = SpeechConfig.fromSubscription(AZURE_SPEECH_KEY, AZURE_REGION);
    speechConfig.speechRecognitionLanguage = 'en-US';
    const audioConfig = AudioConfig.fromDefaultMicrophoneInput();
    const recognizer = new SpeechRecognizer(speechConfig, audioConfig);
    recognizerRef.current = recognizer;

    recognizer.properties.setProperty(PropertyId.Speech_SegmentationSilenceTimeoutMs, '800');
    recognizer.properties.setProperty(PropertyId.Speech_SegmentationStrategy, 'Semantic');

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
    recognizerRef.current?.stopContinuousRecognitionAsync();
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: 'interrupt' }));
      socketRef.current.close();
    }
    setRecording(false);
    appendLog('Recognition stopped');
  };

  const sendToBackend = (text) => {
    window.speechSynthesis?.cancel();
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ text }));
    }
  };

  // group multi-line assistant messages and list items
  const messages = useMemo(() => {
    const lines = transcript.split('\n');
    const result = [];
    let current = null;

    lines.forEach(line => {
      if (line.startsWith('User:')) {
        current && result.push(current);
        current = { speaker: 'User', text: line.replace(/^User:\s*/, '') };
      } else if (line.startsWith('Assistant:')) {
        current && result.push(current);
        current = { speaker: 'Assistant', text: line.replace(/^Assistant:\s*/, '') };
      } else if (current) {
        current.text += '\n' + line;
      }
    });
    current && result.push(current);
    return result;
  }, [transcript]);

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
            const isUser = msg.speaker === 'User';
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
                  {renderContent(msg.text)}
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
