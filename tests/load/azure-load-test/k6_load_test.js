import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';
import { Trend, Counter, Rate } from 'k6/metrics';

// Metrics
export let ConnectionTime = new Trend('connection_time_ms');
export let AgentResponseTime = new Trend('agent_response_time_ms');
export let SuccessRate = new Rate('success_rate');
export let Errors = new Counter('errors_total');
export let ConversationsCompleted = new Counter('conversations_completed');

// Configuration via environment variables
const WS_URL = __ENV.WS_URL || 'ws://localhost:8010/api/v1/media/stream';
const SCENARIO = __ENV.SCENARIO || 'light';
const DURATION = __ENV.DURATION || '120s';

// Optional PCM audio file support
// Place a PCM file (s16le, 24000 Hz, mono) next to the script or provide via PCM_PATH env var.
// Example to generate: ffmpeg -i sample.wav -f s16le -ar 24000 -ac 1 sample.pcm
const PCM_PATH = __ENV.PCM_PATH || 'tests/load/azure-load-test/sample.pcm';
let PCM_BINARY = null;
try {
  PCM_BINARY = open(PCM_PATH, 'b');
  console.log(`Loaded PCM file: ${PCM_PATH} (${PCM_BINARY.length} bytes)`);
} catch (e) {
  // It's normal in some runs to not have a pcm file; we fall back to text templates
  PCM_BINARY = null;
}

// Conversation templates - basic mapping to messages we send
const conversationTemplates = {
  quick_question: [{ type: 'input.text', text: 'I would like to check my policy details?' }],
  insurance_inquiry: [{ type: 'input.text', text: 'My name is alice brown, my social is 1234, and my zip code is 60610.' }],
  confused_customer: [{ type: 'input.text', text: 'I do not understand my bill.' }],
};

function performConversation(socket, templateName) {
  const template = conversationTemplates[templateName] || conversationTemplates.quick_question;
  // Send an initial open session message if protocol requires
  try {
    if (PCM_BINARY) {
      // Stream PCM in 20ms frames (s16le, 24000 Hz, mono)
      const sampleRate = 24000;
      const frameMs = 20;
      const samplesPerFrame = Math.floor((sampleRate * frameMs) / 1000); // 480
      const bytesPerSample = 2; // s16le
      const bytesPerFrame = samplesPerFrame * bytesPerSample; // 960

      let offset = 0;
      let seq = 0;
      while (offset + bytesPerFrame <= PCM_BINARY.length) {
        const chunk = PCM_BINARY.slice(offset, offset + bytesPerFrame);
        const b64 = encoding.b64encode(chunk);
        const payload = {
          type: 'input.audio.chunk',
          sequence: seq,
          encoding: 'pcm_s16le',
          sample_rate: sampleRate,
          channels: 1,
          data: b64,
        };
        socket.send(JSON.stringify(payload));
        seq += 1;
        offset += bytesPerFrame;
        // Sleep to simulate real-time streaming
        sleep(frameMs / 1000);
      }

      // Send EOF / end of audio marker
      try {
        socket.send(JSON.stringify({ type: 'input.audio.eof' }));
      } catch (e) {
        // ignore
      }

    } else {
      for (const msg of template) {
        socket.send(JSON.stringify(msg));
        // Wait briefly for server processing
        sleep(0.3);
      }
    }
  } catch (e) {
    Errors.add(1);
  }
}

export function setup() {
  return { ws_url: WS_URL };
}

// K6 options mapped to scenarios
export let options = (() => {
  const base = {
    scenarios: {},
    thresholds: {
      'errors_total': ['count<1'],
      'success_rate': ['rate>0.95'],
    },
  };

  // Scenario presets
  const presets = {
    light: {
      executor: 'constant-vus',
      vus: 3,
      duration: DURATION,
    },
    medium: {
      executor: 'constant-vus',
      vus: 10,
      duration: DURATION,
    },
    heavy: {
      executor: 'constant-vus',
      vus: 20,
      duration: DURATION,
    },
    stress: {
      executor: 'constant-vus',
      vus: 150,
      duration: DURATION,
    },
    endurance: {
      executor: 'constant-vus',
      vus: 15,
      duration: DURATION,
    },
  };

  const chosen = presets[SCENARIO] || presets.light;
  base.scenarios[SCENARIO] = chosen;

  return base;
})();

export default function (data) {
  const url = data.ws_url || WS_URL;
  const start = Date.now();

  const res = ws.connect(url, {}, function (socket) {
    socket.on('open', function () {
      const connMs = Date.now() - start;
      ConnectionTime.add(connMs);
      // Choose a template randomly (weighted)
      const templates = Object.keys(conversationTemplates);
      const template = templates[Math.floor(Math.random() * templates.length)];
      performConversation(socket, template);
    });

    socket.on('message', function (msg) {
      // Attempt to parse server timings if included
      try {
        const payload = JSON.parse(msg);
        if (payload.type && payload.type === 'response.agent.timing') {
          if (payload.agent_response_ms) {
            AgentResponseTime.add(payload.agent_response_ms);
          }
        }
      } catch (e) {
        // Non-JSON messages are ignored
      }
    });

    socket.on('close', function () {
      // Count as completed convo
      ConversationsCompleted.add(1);
      SuccessRate.add(1);
    });

    socket.on('error', function (e) {
      Errors.add(1);
      SuccessRate.add(0);
      socket.close();
    });

    // Keep connection alive for some seconds to simulate conversation
    socket.setTimeout(function () {
      try {
        socket.close();
      } catch (e) {
        // ignore
      }
    }, 5000);
  });

  // Basic validation for connect
  check(res, { 'ws_connected': (r) => r && r.status === 101 });

  // Small sleep to pace iterations
  sleep(1);
}
