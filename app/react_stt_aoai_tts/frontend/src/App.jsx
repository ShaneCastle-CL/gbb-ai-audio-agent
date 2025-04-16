import React, { useEffect, useRef, useState } from 'react';
import { AudioConfig, SpeechConfig, SpeechRecognizer } from 'microsoft-cognitiveservices-speech-sdk';

const WS_URL = 'ws://localhost:8010/realtime';
const AZURE_SPEECH_KEY = 'F6zQV5w4v1of7O3a2aP2ewo0O9S20shpfNuyDRGuZvnddB5p6x20JQQJ99BCACYeBjFXJ3w3AAAYACOGyNZA';
const AZURE_REGION = 'eastus';

export default function RealTimeVoiceApp() {
  const [transcript, setTranscript] = useState('');
  const [log, setLog] = useState('');
  const [recording, setRecording] = useState(false);
  const socketRef = useRef(null);
  const recognizerRef = useRef(null);

  useEffect(() => {
    return () => stopRecognition();
  }, []);

  const appendLog = (message) => {
    setLog(prev => prev + `\n${new Date().toLocaleTimeString()} - ${message}`);
  };

  const startRecognition = async () => {
    const speechConfig = SpeechConfig.fromSubscription(AZURE_SPEECH_KEY, AZURE_REGION);
    speechConfig.speechRecognitionLanguage = 'en-US';
    const audioConfig = AudioConfig.fromDefaultMicrophoneInput();

    const recognizer = new SpeechRecognizer(speechConfig, audioConfig);
    recognizerRef.current = recognizer;

    recognizer.recognizing = (s, e) => {
      setTranscript(prev => prev + '\r' + e.result.text);
    };

    recognizer.recognized = (s, e) => {
      if (e.result.reason === 0) return;
      const text = e.result.text;
      setTranscript(prev => prev + '\nUser: ' + text);
      appendLog('Final transcript received: ' + text);
      sendToBackend(text);
    };

    recognizer.startContinuousRecognitionAsync();
    setRecording(true);
    appendLog('Microphone access and recognition started.');

    const socket = new WebSocket(WS_URL);
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;

    socket.onopen = () => appendLog('WebSocket connection established.');

    socket.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        if (event.data.includes('processing_started')) {
          appendLog('Backend started processing GPT response.');
        } else if (event.data.includes('interrupt')) {
          appendLog('Backend reported interrupt.');
        } else {
          setTranscript(prev => prev + '\n' + event.data);
          appendLog('Received GPT chunk.');
        }
      } else {
        const audioCtx = new AudioContext();
        const arrayBuffer = await event.data.arrayBuffer();
        const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
        const source = audioCtx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioCtx.destination);
        source.start();
        appendLog('Received and played audio chunk.');
      }
    };
  };

  const stopRecognition = () => {
    if (recognizerRef.current) {
      recognizerRef.current.stopContinuousRecognitionAsync();
    }
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send('__INTERRUPT__');
      socketRef.current.close();
      appendLog('Sent interrupt to backend and closed WebSocket.');
    }
    setRecording(false);
    appendLog('Stopped recognition.');
  };

  const sendToBackend = (text) => {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ cancel: true }));
      socketRef.current.send(JSON.stringify({ text }));
      appendLog('Sent transcript to backend.');
    }
  };

  return (
    <div style={{
      maxWidth: '1000px',
      margin: '0 auto',
      padding: '48px 32px',
      fontFamily: 'Segoe UI, Roboto, sans-serif',
      color: '#ECEFF1',
      background: 'linear-gradient(135deg, #2C3E50, #34495E)',
      borderRadius: '20px',
      boxShadow: '0 8px 32px rgba(0,0,0,0.45)',
      backdropFilter: 'blur(12px)',
      border: '1px solid rgba(255, 255, 255, 0.1)'
    }}>
      <h1 style={{
        fontSize: '2.75rem',
        textAlign: 'center',
        marginBottom: '2rem',
        fontWeight: 600,
        color: '#FFFFFF'
      }}>
        📲 Real-Time Voice MedAgent 
      </h1>

      <div style={{ textAlign: 'center', marginBottom: '2.5rem' }}>
        <button
          onClick={recording ? stopRecognition : startRecognition}
          style={{
            padding: '14px 36px',
            fontSize: '1.1rem',
            borderRadius: '10px',
            border: 'none',
            cursor: 'pointer',
            backgroundColor: recording ? '#E74C3C' : '#2ECC71',
            color: '#fff',
            fontWeight: 'bold',
            letterSpacing: '0.5px',
            boxShadow: '0 3px 12px rgba(0,0,0,0.3)',
            transition: 'all 0.3s ease-in-out'
          }}
        >
          {recording ? '⏹ End Conversation' : '▶ Begin Conversation'}
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <section>
          <h2 style={{ marginBottom: '0.75rem', fontSize: '1.5rem', fontWeight: 500, color: '#D0D3D4' }}>💬 Conversation</h2>
          <div style={{
            backgroundColor: '#1C2833',
            padding: '1.25em',
            borderRadius: '12px',
            border: '1px solid #566573',
            minHeight: '150px',
            fontSize: '0.95rem',
            color: '#ECF0F1',
            whiteSpace: 'pre-wrap'
          }}>
            {
              (() => {
                let lastAssistantMessage = '';
                return transcript.split('\n').map((line, idx) => {
                  const clean = line.trim();

                  // Try to parse assistant JSON
                  try {
                    const parsed = JSON.parse(clean);
                    if (parsed && (parsed.type === 'assistant' || parsed.type === 'status')) {
                      const msg = parsed.content || parsed.message;
                      if (msg === lastAssistantMessage) return null;
                      lastAssistantMessage = msg;
                      return (
                        <div key={idx} style={{ marginBottom: '1em', color: '#AED6F1' }}>
                          <strong>🤖 Assistant:</strong> {msg}
                        </div>
                      );
                    }
                  } catch (e) { }

                  if (clean.startsWith('User:')) {
                    return (
                      <div key={idx} style={{ marginBottom: '1em', color: '#F7DC6F' }}>
                        <strong>🧑‍💬 You:</strong> {clean.replace('User:', '').trim()}
                      </div>
                    );
                  }

                  if (clean.startsWith('GPT:')) {
                    const msg = clean.replace('GPT:', '').trim();
                    if (msg === lastAssistantMessage) return null;
                    lastAssistantMessage = msg;
                    return (
                      <div key={idx} style={{ marginBottom: '1em', color: '#AED6F1' }}>
                        <strong>🤖 Assistant:</strong> {msg}
                      </div>
                    );
                  }

                  if (clean.startsWith('🤖')) {
                    const msg = clean.replace('🤖', '').trim();
                    if (msg === lastAssistantMessage) return null;
                    lastAssistantMessage = msg;
                    return (
                      <div key={idx} style={{ marginBottom: '1em', color: '#AED6F1' }}>
                        <strong>🤖 Assistant:</strong> {msg}
                      </div>
                    );
                  }

                  return (
                    <div key={idx} style={{ marginBottom: '1em', color: '#BDC3C7' }}>
                      {clean}
                    </div>
                  );
                });
              })()
            }
          </div>
        </section>

        <section>
          <h2 style={{ marginBottom: '0.75rem', fontSize: '1.5rem', fontWeight: 500, color: '#D0D3D4' }}>🛠️ System Logs</h2>
          <pre style={{
            whiteSpace: 'pre-wrap',
            backgroundColor: '#17202A',
            padding: '1.25em',
            borderRadius: '12px',
            border: '1px solid #566573',
            minHeight: '100px',
            fontSize: '0.9rem',
            color: '#BFC9CA',
            overflowX: 'auto'
          }}>{log}</pre>
        </section>
      </div>
    </div>
  );
}
