// app.js
const toggleButton = document.getElementById('toggleButton');
const statusMessage = document.getElementById('statusMessage');
const reportDiv = document.getElementById('report');

let isRecording = false;
let websocket = null;
let audioContext = null;
let mediaStream = null;
let mediaProcessor = null;
let audioQueueTime = 0;

// Optional: client-side VAD parameters (currently not in active use)
let speaking = false;
const VAD_THRESHOLD = 0.01; // Adjust this threshold as needed

// Start continuous recording and stream audio buffers to the backend.
async function startRecording() {
  isRecording = true;
  toggleButton.textContent = 'Stop Conversation';
  statusMessage.textContent = 'Recording...';

  // Initialize AudioContext if not already done.
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    audioQueueTime = audioContext.currentTime;
  }

  // Open WebSocket connection to the backend's continuous audio endpoint.
  const mainHost = window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  websocket = new WebSocket(`${protocol}//${mainHost}/realtime`);

  websocket.onopen = () => {
    console.log('WebSocket connection opened');
    // Send an initial session update if desired.
    const sessionUpdate = {
      type: 'session.update',
      session: {
        turn_detection: {
          type: 'server_vad',
          threshold: 0.7,          // Adjust if necessary
          prefix_padding_ms: 300,  // Adjust if necessary
          silence_duration_ms: 500 // Adjust as needed
        }
      }
    };
    websocket.send(JSON.stringify(sessionUpdate));
  };

  websocket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    console.log('Received message:', message);
    handleWebSocketMessage(message);
  };

  websocket.onerror = (event) => {
    console.error('WebSocket error:', event);
  };

  websocket.onclose = () => {
    console.log('WebSocket connection closed');
    if (isRecording) {
      stopRecording();
    }
  };

  // Start capturing audio from the microphone.
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const source = audioContext.createMediaStreamSource(mediaStream);

  // Create a ScriptProcessor for capturing audio data. Note that the buffer size (4096) may be tuned.
  mediaProcessor = audioContext.createScriptProcessor(4096, 1, 1);
  source.connect(mediaProcessor);
  mediaProcessor.connect(audioContext.destination);

  mediaProcessor.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0);
    // Convert the Float32Array audio data to an Int16Array
    const int16Data = float32ToInt16(inputData);
    // Convert the Int16Array to a Base64 string
    const base64Audio = int16ToBase64(int16Data);
    // Build an audio message to send to the backend.
    const audioCommand = {
      type: 'input_audio_buffer.append',
      audio: base64Audio
    };
    websocket.send(JSON.stringify(audioCommand));

    // Optional: if you’d like to detect voice activity on the client side, uncomment and adjust:
    // const isUserSpeaking = detectSpeech(inputData);
    // if (isUserSpeaking && !speaking) {
    //     speaking = true;
    //     console.log('User started speaking');
    //     // Optionally, stop any playback from the assistant.
    // } else if (!isUserSpeaking && speaking) {
    //     speaking = false;
    //     console.log('User stopped speaking');
    // }
  };
}

function stopRecording() {
  isRecording = false;
  toggleButton.textContent = 'Start Conversation';
  statusMessage.textContent = 'Stopped';

  if (mediaProcessor) {
    mediaProcessor.disconnect();
    mediaProcessor.onaudioprocess = null;
  }

  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }

  if (websocket) {
    websocket.close();
    websocket = null;
  }
}

function onToggleListening() {
  if (!isRecording) {
    startRecording();
  } else {
    stopRecording();
  }
}

// No phone call functionality is included in this MVP
// Remove or comment out the onCallButton function and its event listener if not used.
// function onCallButton() { ... }

toggleButton.addEventListener('click', onToggleListening);
// If call button exists in your HTML and is not needed, remove the following line:
// callButton.addEventListener('click', onCallButton);

// Handler for incoming WebSocket messages from the backend.
function handleWebSocketMessage(message) {
  switch (message.type) {
    case 'response.audio.delta':
      if (message.delta) {
        playAudio(message.delta);
      }
      break;
    case 'response.done':
      console.log('Response done');
      break;
    case 'extension.middle_tier_tool_response':
      if (message.tool_name === 'generate_report') {
        const report = JSON.parse(message.tool_result);
        displayReport(report);
      }
      break;
    case 'input_audio_buffer.speech_started':
      // If user starts speaking during playback, stop the assistant's audio.
      stopAssistantAudio();
      break;
    case 'error':
      console.error('Error message from server:', JSON.stringify(message, null, 2));
      break;
    default:
      console.log('Unhandled message type:', message.type);
  }
}

let assistantAudioSources = [];

function playAudio(base64Audio) {
  const binary = atob(base64Audio);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const int16Array = new Int16Array(bytes.buffer);
  const float32Array = int16ToFloat32(int16Array);

  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    audioQueueTime = audioContext.currentTime;
  }

  const audioBuffer = audioContext.createBuffer(1, float32Array.length, 24000);
  audioBuffer.copyToChannel(float32Array, 0);

  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);

  const currentTime = audioContext.currentTime;
  const startTime = Math.max(audioQueueTime, currentTime + 0.1);
  source.start(startTime);

  assistantAudioSources.push(source);
  audioQueueTime = startTime + audioBuffer.duration;

  source.onended = () => {
    assistantAudioSources = assistantAudioSources.filter(s => s !== source);
  };
}

function stopAssistantAudio() {
  assistantAudioSources.forEach(source => {
    try {
      source.stop();
    } catch (e) {
      console.error('Error stopping audio source:', e);
    }
  });
  assistantAudioSources = [];
  audioQueueTime = audioContext ? audioContext.currentTime : 0;
}

function displayReport(report) {
  reportDiv.textContent = JSON.stringify(report, null, 2);
}

function float32ToInt16(float32Array) {
  const int16Array = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16Array;
}

function int16ToBase64(int16Array) {
  const byteArray = new Uint8Array(int16Array.buffer);
  let binary = '';
  for (let i = 0; i < byteArray.byteLength; i++) {
    binary += String.fromCharCode(byteArray[i]);
  }
  return btoa(binary);
}

function int16ToFloat32(int16Array) {
  const float32Array = new Float32Array(int16Array.length);
  for (let i = 0; i < int16Array.length; i++) {
    const intVal = int16Array[i];
    const floatVal = intVal < 0 ? intVal / 0x8000 : intVal / 0x7FFF;
    float32Array[i] = floatVal;
  }
  return float32Array;
}

function detectSpeech(inputData) {
  let sumSquares = 0;
  for (let i = 0; i < inputData.length; i++) {
    sumSquares += inputData[i] * inputData[i];
  }
  const rms = Math.sqrt(sumSquares / inputData.length);
  return rms > VAD_THRESHOLD;
}

function updateVoiceOnServer(selectedVoice) {
  fetch('/update-voice', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ voice: selectedVoice }),
  });
}
