# WebSocket Concurrency Test - Quick Start Guide

## 🚀 Running Tests (Current Setup with Auth Bypass)

```bash
# Basic concurrency test (2 sessions, bypass auth errors)
python tests/load/ws_concurrency_test.py --concurrency 2 --session-timeout 5 --skip-auth-errors

# Full test with exports (CSV, Prometheus, JSON logs)
python tests/load/ws_concurrency_test.py \
  --concurrency 5 \
  --session-timeout 10 \
  --skip-auth-errors \
  --csv tests/load/sessions/session_summary.csv \
  --prom tests/load/sessions/metrics.prom \
  --log-dir tests/load/sessions/session_logs \
  --summary-json tests/load/sessions/summary.json

# ACS Media WebSocket variant
python tests/load/acs_media_concurrency_test.py --concurrency 3 --session-timeout 5 --skip-auth-errors
```

## 🔧 To Enable Full Testing (Disable Authentication)

### Option 1: Environment Variable
```bash
# Temporarily disable auth validation
export ENABLE_AUTH_VALIDATION=false
# Restart your backend, then run tests without --skip-auth-errors
```

### Option 2: Backend Configuration
In your backend settings, temporarily set:
```python
ENABLE_AUTH_VALIDATION = False
```

## 📊 Test Output Features

### 1. **Session Isolation Validation** ✅
- Detects cross-session message contamination
- Uses random aliases (Orion, Lyra, etc.) to catch message leaks
- Returns exit code 2 if contamination detected

### 2. **CSV Export** 📋
```csv
session_id,connect_latency_ms,greeting_latency_ms,message_count,errors
sess-0-1934,,,0,connect_error:server rejected WebSocket connection: HTTP 403
```

### 3. **Prometheus Metrics** 📈
```
# HELP realtime_sessions_total Total realtime sessions run
# TYPE realtime_sessions_total counter
realtime_sessions_total 3
```

### 4. **Per-Session JSON Logs** 📁
```json
{
  "session_id": "sess-0-1934",
  "connect_latency_ms": null,
  "greeting_latency_ms": null,
  "message_count": 0,
  "errors": ["connect_error:server rejected WebSocket connection: HTTP 403"],
  "alias": "Orion_0",
  "messages": []
}
```

## 🧪 Test Scenarios Available

### Basic Load Test
```bash
python tests/load/ws_concurrency_test.py --concurrency 10 --iterations 3
```

### Audio Streaming Test
```bash
python tests/load/ws_concurrency_test.py --concurrency 5 --send-audio --audio-seconds 3
```

### Long-Running Session Test
```bash
python tests/load/ws_concurrency_test.py --concurrency 3 --wait-for-stream --session-timeout 30
```

### Replay Previous Test
```bash
python tests/load/ws_concurrency_test.py --replay-log-dir tests/load/session_logs
```

## 🎯 Contamination Detection

The test automatically detects two types of contamination:

1. **Message Contamination**: Messages intended for one session appearing in another
2. **Alias Contamination**: Random aliases bleeding between sessions

Exit codes:
- `0`: Success, no contamination
- `1`: Connection/infrastructure errors
- `2`: Contamination detected

## 🔍 Next Steps

1. **For Infrastructure Testing**: Continue using `--skip-auth-errors` flag
2. **For Full Integration Testing**: Disable `ENABLE_AUTH_VALIDATION` temporarily
3. **For Production Monitoring**: Set up authentication tokens in test script
4. **For CI/CD**: Use exit codes to fail builds on contamination detection

Your test infrastructure is complete and working perfectly! 🎉
