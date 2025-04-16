/*******************************************************
 *  app.js
 *  -----------------------------------------
 *  1) Uses Azure Speech SDK in the browser for STT.
 *  2) Sends recognized text to FastAPI via WebSocket.
 *  3) Receives GPT text + TTS audio from FastAPI.
 *  4) Plays TTS audio in the browser.
 ******************************************************/

// -- DOM Elements -------------------------------------
const toggleButton = document.getElementById('toggleButton');
const statusMessage = document.getElementById('statusMessage');
const transcriptDiv = document.getElementById('transcript');
const reportDiv = document.getElementById('report');

// -- Azure Speech Credentials (Replace with your actual keys/region!) --
const AZURE_SPEECH_KEY = 'F6zQV5w4v1of7O3a2aP2ewo0O9S20shpfNuyDRGuZvnddB5p6x20JQQJ99BCACYeBjFXJ3w3AAAYACOGyNZA';
const AZURE_SPEECH_REGION = 'eastus'; // e.g. "eastus"

// -- App State ----------------------------------------
let isRecording = false;
let speechRecognizer = null; // Will hold Azure STT recognizer
let websocket = null;        // Will hold the WebSocket to server

// We'll create an AudioContext for playing TTS from server
let audioContext = null;
let audioQueueTime = 0;
let assistantAudioSources = []; // Keep track of audio sources for TTS playback

// -- Event Listeners -----------------------------------
toggleButton.addEventListener('click', onToggleListening);

// =====================================================
// 1) Start or Stop the Conversation
// =====================================================
async function onToggleListening() {
  if (!isRecording) {
    await startRecognition();
  } else {
    stopRecognition();
  }
}

// =====================================================
// 2) Start Client-Side STT & Open WebSocket
// =====================================================
async function startRecognition() {
  if (isRecording) return; // prevent double-start
  isRecording = true;

  toggleButton.textContent = 'Stop Conversation';
  statusMessage.textContent = 'Starting lol...';

  // ----------------------------------------------------
  // A) Initialize Azure Speech in the Browser
  // ----------------------------------------------------
  // This portion uses the Microsoft Cognitive Services Speech SDK
  // that's loaded from the CDN. The global object is `window.SpeechSDK`.
  const speechConfig = window.SpeechSDK.SpeechConfig.fromSubscription(
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION
  );
  speechConfig.speechRecognitionLanguage = 'en-US';

  // Use the default microphone
  const audioConfig = window.SpeechSDK.AudioConfig.fromDefaultMicrophoneInput();

  // Create the recognizer
  speechRecognizer = new window.SpeechSDK.SpeechRecognizer(speechConfig, audioConfig);

  // Partial results
  speechRecognizer.recognizing = (s, e) => {
    statusMessage.textContent = 'Recognizing: ' + e.result.text;
  };

  // Final recognized results
  speechRecognizer.recognized = (s, e) => {
    if (!e.result.text) return;
    const userText = e.result.text.trim();
    appendTranscript('User', userText);
    console.log('Final recognized:', userText);

    // Send recognized text to backend
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({ text: userText }));
    }
  };

  // Actually start continuous recognition
  speechRecognizer.startContinuousRecognitionAsync(
    () => {
      console.log('Azure STT started.');
      statusMessage.textContent = 'Recording...';
    },
    (err) => {
      console.error('Azure STT error:', err);
      statusMessage.textContent = 'Error starting Azure STT.';
    }
  );

  // ----------------------------------------------------
  // B) Open WebSocket to your FastAPI "/realtime" route
  // ----------------------------------------------------
  setupWebSocket();
}

// =====================================================
// 3) Stop Client-Side STT & Close WebSocket
// =====================================================
function stopRecognition() {
  isRecording = false;
  toggleButton.textContent = 'Start Conversation';
  statusMessage.textContent = 'Stopped';

  // Stop STT
  if (speechRecognizer) {
    speechRecognizer.stopContinuousRecognitionAsync(
      () => console.log('Azure STT stopped.'),
      (err) => console.error('Error stopping STT:', err)
    );
    speechRecognizer = null;
  }

  // Close WS
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.close();
  }
  websocket = null;
}

// =====================================================
// 4) Setup WebSocket to FastAPI
// =====================================================
function setupWebSocket() {
  if (websocket) {
    // If there's an existing WebSocket, close it
    try {
      websocket.close();
    } catch (e) {
      console.error('Error closing existing WebSocket:', e);
    }
  }

  const mainHost = window.location.host; // e.g. localhost:8010
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${mainHost}/realtime`;

  websocket = new WebSocket(wsUrl);

  websocket.onopen = () => {
    console.log('WebSocket connected to:', wsUrl);
  };

  websocket.onerror = (err) => {
    console.error('WebSocket error:', err);
  };

  websocket.onclose = () => {
    console.log('WebSocket closed');
    // If user is still "recording", stop
    if (isRecording) {
      stopRecognition();
    }
  };

  websocket.onmessage = (event) => {
    // Our server sends JSON messages. Let's parse them:
    handleServerMessage(event.data);
  };
}

// =====================================================
// 5) Handle Inbound Messages from Server
// =====================================================
function handleServerMessage(rawData) {
  let msg;
  try {
    msg = JSON.parse(rawData);
  } catch (err) {
    console.warn('Got non-JSON data from server:', rawData);
    return;
  }

  switch (msg.type) {
    case 'status':
      // e.g. greeting from server
      appendTranscript('Assistant', msg.message);
      break;

    case 'assistant':
      // GPT text
      appendTranscript('Assistant', msg.content);
      break;

    case 'exit':
      // Server instructs to stop
      appendTranscript('Assistant', msg.message);
      stopRecognition();
      break;

    case 'tts_base64':
      // TTS audio data
      if (msg.audio) {
        playBase64Audio(msg.audio);
      }
      break;

    case 'tool_result':
      // If the server returns a tool result or a "report"
      displayReport(msg.result);
      break;

    default:
      console.log('Unhandled message type:', msg.type, msg);
  }
}

// =====================================================
// 6) Display Transcript & Reports in the DOM
// =====================================================
function appendTranscript(speaker, text) {
  const newLine = document.createElement('div');
  newLine.textContent = speaker + ': ' + text;
  transcriptDiv.appendChild(newLine);
}

function displayReport(report) {
  if (typeof report === 'object') {
    reportDiv.textContent = JSON.stringify(report, null, 2);
  } else {
    reportDiv.textContent = String(report);
  }
}

// =====================================================
// 7) Play TTS Audio from Base64
// =====================================================
function playBase64Audio(base64Audio) {
  // Convert base64 => bytes
  const binary = atob(base64Audio);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary.charCodeAt(i);
  }

  // Create or reuse AudioContext
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: 24000, // depends on what your TTS returns
    });
    audioQueueTime = audioContext.currentTime;
  }

  // decodeAudioData can handle WAV or other audio formats if properly encoded
  audioContext.decodeAudioData(bytes.buffer).then((audioBuffer) => {
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);

    // Queue up so multiple TTS chunks don't overlap
    const currentTime = audioContext.currentTime;
    const startTime = Math.max(audioQueueTime, currentTime + 0.05);
    source.start(startTime);

    assistantAudioSources.push(source);
    audioQueueTime = startTime + audioBuffer.duration;

    source.onended = () => {
      assistantAudioSources = assistantAudioSources.filter((s) => s !== source);
    };
  }).catch((err) => {
    console.error('Error decoding audio:', err);
  });
}

// If you want a method to stop TTS playback mid-sentence:
function stopAssistantAudio() {
  assistantAudioSources.forEach((source) => {
    try {
      source.stop();
    } catch (err) {
      console.error('Error stopping audio source:', err);
    }
  });
  assistantAudioSources = [];
  audioQueueTime = audioContext ? audioContext.currentTime : 0;
}
