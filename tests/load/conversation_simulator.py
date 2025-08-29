#!/usr/bin/env python3
"""
Realistic Conversation Simulator for Agent Flow Testing
=======================================================

Simulates realistic human-AI conversations based on actual speech patterns
observed in the server logs to enable proper load testing and agent evaluation.
"""

import asyncio
import json
import base64
import websockets
import struct
import math
import time
import random
import ssl
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
# No longer need audio generator - using pre-cached PCM files

def generate_silence_chunk(duration_ms: float = 100.0, sample_rate: int = 16000) -> bytes:
    """Generate a silent audio chunk with very low-level noise for VAD continuity."""
    samples = int((duration_ms / 1000.0) * sample_rate)
    # Generate very quiet background noise instead of pure silence
    # This is more realistic and helps trigger final speech recognition
    import struct
    audio_data = bytearray()
    for _ in range(samples):
        # Add very quiet random noise (-10 to +10 amplitude in 16-bit range)
        noise = random.randint(-10, 10)
        audio_data.extend(struct.pack('<h', noise))
    return bytes(audio_data)

class ConversationPhase(Enum):
    GREETING = "greeting"
    AUTHENTICATION = "authentication"  
    INQUIRY = "inquiry"
    CLARIFICATION = "clarification"
    RESOLUTION = "resolution"
    FAREWELL = "farewell"

@dataclass
class ConversationTurn:
    """Represents a single turn in a conversation."""
    speaker: str  # "user" or "agent"
    text: str
    phase: ConversationPhase
    delay_before_ms: int = 500  # Pause before speaking
    speech_duration_ms: Optional[int] = None  # Override calculated duration
    interruption_likely: bool = False  # Whether agent might interrupt

@dataclass
class ConversationTemplate:
    """Template for a complete conversation flow."""
    name: str
    description: str
    turns: List[ConversationTurn]
    expected_agent: str = "AuthAgent"
    success_indicators: List[str] = field(default_factory=list)

@dataclass
class ConversationMetrics:
    """Metrics collected during conversation simulation."""
    session_id: str
    template_name: str
    start_time: float
    end_time: float
    connection_time_ms: float
    
    # Turn-level metrics
    user_turns: int = 0
    agent_turns: int = 0
    total_speech_recognition_time_ms: float = 0
    total_agent_processing_time_ms: float = 0
    total_tts_time_ms: float = 0
    
    # Quality metrics
    successful_turns: int = 0
    failed_turns: int = 0
    interruptions_detected: int = 0
    barge_ins_detected: int = 0
    
    # Server responses
    server_responses: List[Dict[str, Any]] = field(default_factory=list)
    audio_chunks_received: int = 0
    errors: List[str] = field(default_factory=list)

class ProductionSpeechGenerator:
    """Streams pre-cached PCM audio files for load testing."""
    
    def __init__(self, cache_dir: str = "tests/load/audio_cache"):
        """Initialize with cached PCM files directory."""
        from pathlib import Path
        import os
        
        # Handle relative paths by making them relative to the script location
        if not os.path.isabs(cache_dir):
            script_dir = Path(__file__).parent
            self.cache_dir = script_dir / cache_dir.replace("tests/load/", "")
        else:
            self.cache_dir = Path(cache_dir)
        
        # Load all available PCM files
        self.pcm_files = list(self.cache_dir.glob("*.pcm"))
        self.current_file_index = 0
        
        print(f"📁 Found {len(self.pcm_files)} cached PCM files")
        if not self.pcm_files:
            print("⚠️  Warning: No PCM files found in audio cache directory")
    
    def get_next_audio(self) -> bytes:
        """Get the next available PCM audio file, cycling through available files."""
        if not self.pcm_files:
            print("❌ No PCM files available")
            return b""
        
        # Get current file and advance index (cycle through files)
        pcm_file = self.pcm_files[self.current_file_index]
        self.current_file_index = (self.current_file_index + 1) % len(self.pcm_files)
        
        try:
            audio_bytes = pcm_file.read_bytes()
            duration_s = len(audio_bytes) / (16000 * 2)  # 16kHz, 16-bit
            print(f"📄 Using cached audio: {pcm_file.name} ({len(audio_bytes)} bytes, {duration_s:.2f}s)")
            return audio_bytes
        except Exception as e:
            print(f"❌ Failed to read PCM file {pcm_file}: {e}")
            return b""
    

class ConversationTemplates:
    """Pre-defined conversation templates for different scenarios."""
    
    @staticmethod
    def get_insurance_inquiry() -> ConversationTemplate:
        """Standard insurance inquiry conversation."""
        return ConversationTemplate(
            name="insurance_inquiry",
            description="Customer calling to ask about insurance coverage",
            turns=[
                ConversationTurn("user", "Hello, my name is Alice Brown, my social is 1234, and my zip code is 60610", ConversationPhase.GREETING, delay_before_ms=1000),
                ConversationTurn("user", "I'm looking to learn about Madrid.", ConversationPhase.INQUIRY, delay_before_ms=2000),
                ConversationTurn("user", "Actually, I need help with my car insurance.", ConversationPhase.CLARIFICATION, delay_before_ms=1500),
                ConversationTurn("user", "What does my policy cover?", ConversationPhase.INQUIRY, delay_before_ms=800),
                ConversationTurn("user", "Thank you for the information.", ConversationPhase.FAREWELL, delay_before_ms=1200),
            ],
            expected_agent="AuthAgent",
            success_indicators=["insurance", "policy", "coverage", "help"]
        )
    
    @staticmethod
    def get_quick_question() -> ConversationTemplate:
        """Short, quick question scenario."""
        return ConversationTemplate(
            name="quick_question",
            description="Brief customer inquiry",
            turns=[
                ConversationTurn("user", "Hi there!", ConversationPhase.GREETING, delay_before_ms=500),
                ConversationTurn("user", "Can you help me with my account?", ConversationPhase.INQUIRY, delay_before_ms=800),
                ConversationTurn("user", "Thanks, that's all I needed.", ConversationPhase.FAREWELL, delay_before_ms=1000),
            ],
            expected_agent="AuthAgent",
            success_indicators=["account", "help"]
        )
    
    @staticmethod
    def get_confused_customer() -> ConversationTemplate:
        """Customer who starts confused but gets clarity."""
        return ConversationTemplate(
            name="confused_customer",
            description="Customer initially confused about what they need",
            turns=[
                ConversationTurn("user", "Um, hello?", ConversationPhase.GREETING, delay_before_ms=800),
                ConversationTurn("user", "I'm not sure what I need help with.", ConversationPhase.INQUIRY, delay_before_ms=1200),
                ConversationTurn("user", "Maybe something about my insurance?", ConversationPhase.CLARIFICATION, delay_before_ms=1000),
                ConversationTurn("user", "Yes, that's right. My auto insurance.", ConversationPhase.INQUIRY, delay_before_ms=900),
            ],
            expected_agent="AuthAgent", 
            success_indicators=["insurance", "auto", "help"]
        )
    
    @staticmethod
    def get_all_templates() -> List[ConversationTemplate]:
        """Get all available conversation templates."""
        return [
            ConversationTemplates.get_insurance_inquiry(),
            ConversationTemplates.get_quick_question(),
            ConversationTemplates.get_confused_customer(),
        ]

class ConversationSimulator:
    """Simulates realistic conversations for load testing and agent evaluation."""
    
    def __init__(self, ws_url: str = "ws://localhost:8010/api/v1/media/stream"):
        self.ws_url = ws_url
        self.speech_generator = ProductionSpeechGenerator()
    
    def preload_conversation_audio(self, template: ConversationTemplate):
        """No-op since we're using pre-cached files."""
        print(f"ℹ️  Using pre-cached PCM files, no preloading needed")
    
    async def simulate_conversation(
        self, 
        template: ConversationTemplate,
        session_id: Optional[str] = None,
        on_turn_complete: Optional[Callable[[ConversationTurn, List[Dict]], None]] = None,
        on_agent_response: Optional[Callable[[str, List[Dict]], None]] = None,
        preload_audio: bool = True
    ) -> ConversationMetrics:
        """Simulate a complete conversation using the given template."""
        
        if session_id is None:
            session_id = f"{template.name}-{int(time.time())}-{random.randint(1000, 9999)}"
        
        metrics = ConversationMetrics(
            session_id=session_id,
            template_name=template.name,
            start_time=time.time(),
            end_time=0,
            connection_time_ms=0
        )
        
        print(f"🎭 Starting conversation simulation: {template.name}")
        print(f"📞 Session ID: {session_id}")
        
        # Preload audio for better performance and recognition quality
        if preload_audio:
            print(f"🔄 Preloading production audio...")
            self.preload_conversation_audio(template)
        
        try:
            # Connect to WebSocket
            connect_start = time.time()
            # Configure connection parameters based on URL scheme
            connect_kwargs = {
                "additional_headers": {
                    "x-call-connection-id": session_id,
                    "x-session-id": session_id
                }
            }
            
            # Explicitly handle SSL based on URL scheme
            if self.ws_url.startswith("ws://"):
                # For plain WebSocket, explicitly disable SSL
                connect_kwargs["ssl"] = None
            elif self.ws_url.startswith("wss://"):
                # For secure WebSocket, create SSL context
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                connect_kwargs["ssl"] = ssl_context
            
            async with websockets.connect(
                f"{self.ws_url}?call_connection_id={session_id}",
                **connect_kwargs
            ) as websocket:
                metrics.connection_time_ms = (time.time() - connect_start) * 1000
                print(f"✅ Connected in {metrics.connection_time_ms:.1f}ms")
                
                # Send audio metadata
                metadata = {
                    "kind": "AudioMetadata",
                    "payload": {"format": "pcm", "rate": 16000}
                }
                await websocket.send(json.dumps(metadata))
                
                # Wait for system initialization
                await asyncio.sleep(1.0)
                
                # Process each conversation turn
                for turn_idx, turn in enumerate(template.turns):
                    if turn.speaker == "user":
                        print(f"\n👤 User turn {turn_idx + 1}: '{turn.text}' ({turn.phase.value})")
                        
                        # Wait before speaking (natural pause) - let previous response finish
                        pause_time = max(turn.delay_before_ms / 1000.0, 2.0)  # At least 2 seconds
                        print(f"    ⏸️  Waiting {pause_time:.1f}s for agent to finish speaking...")
                        await asyncio.sleep(pause_time)
                        
                        # Get next cached PCM audio file
                        audio_send_start = time.time()
                        
                        # Use next available cached PCM file
                        speech_audio = self.speech_generator.get_next_audio()
                        
                        if not speech_audio:
                            print(f"    ❌ No audio available, skipping turn")
                            metrics.failed_turns += 1
                            continue
                        
                        # Send audio more quickly to simulate natural speech timing
                        chunk_size = int(16000 * 0.1 * 2)  # Back to 100ms chunks for natural flow
                        audio_chunks_sent = 0
                        
                        print(f"    🎤 Streaming cached audio for turn: '{turn.text}'")
                        
                        for i in range(0, len(speech_audio), chunk_size):
                            chunk = speech_audio[i:i + chunk_size]
                            chunk_b64 = base64.b64encode(chunk).decode('utf-8')
                            
                            audio_msg = {
                                "kind": "AudioData",
                                "audioData": {
                                    "data": chunk_b64,
                                    "silent": False,
                                    "timestamp": time.time()
                                }
                            }
                            
                            await websocket.send(json.dumps(audio_msg))
                            audio_chunks_sent += 1
                            
                            # Natural speech timing
                            await asyncio.sleep(0.08)  # 80ms between chunks - more natural
                        
                        # Add a short pause after speech (critical for speech recognition finalization)
                        print(f"    🤫 Adding end-of-utterance silence...")
                        
                        for _ in range(5):  # Send 5 chunks of 100ms silence each
                            silence_msg = {
                                "kind": "AudioData", 
                                "audioData": {
                                    "data": base64.b64encode(generate_silence_chunk(100)).decode('utf-8'),
                                    "silent": False,  # Mark as non-silent to ensure VAD processes it
                                    "timestamp": time.time()
                                }
                            }
                            await websocket.send(json.dumps(silence_msg))
                            audio_chunks_sent += 1
                            await asyncio.sleep(0.1)  # 100ms between silence chunks
                        
                        audio_send_complete = time.time()
                        print(f"    📤 Sent {audio_chunks_sent} audio chunks ({len(speech_audio)} bytes total)")
                        print(f"    🎵 Audio duration: {len(speech_audio)/(16000*2):.2f}s")
                        print(f"    ⏱️  Audio send time: {(audio_send_complete - audio_send_start)*1000:.1f}ms")
                        
                        metrics.user_turns += 1
                        
                        # Wait for complete agent response with proper timeout and latency measurement
                        response_start = time.time()
                        responses = []
                        agent_audio_chunks_this_turn = 0
                        last_audio_chunk_time = None
                        response_complete = False
                        turn_failed = False
                        
                        # Start streaming silence to maintain VAD continuity
                        silence_streaming_active = True
                        
                        async def stream_silence():
                            """Stream silent audio chunks during response wait to maintain VAD."""
                            silence_chunk = generate_silence_chunk(100)  # 100ms silence chunks
                            silence_chunk_b64 = base64.b64encode(silence_chunk).decode('utf-8')
                            
                            while silence_streaming_active:
                                try:
                                    # Send silence as non-silent to ensure VAD processes it
                                    # This mimics ambient/background noise during conversation pauses
                                    silence_msg = {
                                        "kind": "AudioData",
                                        "audioData": {
                                            "data": silence_chunk_b64,
                                            "silent": False,  # Mark as non-silent to keep VAD active
                                            "timestamp": time.time()
                                        }
                                    }
                                    await websocket.send(json.dumps(silence_msg))
                                    await asyncio.sleep(0.1)  # Send every 100ms
                                except Exception:
                                    break  # Exit if websocket is closed
                        
                        # Start background silence streaming task
                        silence_task = asyncio.create_task(stream_silence())
                        
                        # print(f"    ⏳ Waiting for complete agent response (max 10s timeout)...")
                        
                        try:
                            # Listen for the complete agent response with 20-second timeout
                            timeout_deadline = response_start + 20.0  # 20 second absolute timeout
                            audio_silence_timeout = 2.0  # Consider response complete after 2s of no audio chunks
                            
                            while time.time() < timeout_deadline and not response_complete:
                                try:
                                    # Dynamic timeout: shorter if we've received audio, longer initially
                                    if last_audio_chunk_time:
                                        # If we've been getting audio, use shorter timeout to detect end
                                        remaining_silence_time = audio_silence_timeout - (time.time() - last_audio_chunk_time)
                                        current_timeout = max(0.5, remaining_silence_time)
                                    else:
                                        # Initially, wait longer for first response
                                        current_timeout = min(3.0, timeout_deadline - time.time())
                                    
                                    if current_timeout <= 0:
                                        # We've waited long enough since last audio chunk
                                        if agent_audio_chunks_this_turn > 0:
                                            response_complete = True
                                            break
                                        else:
                                            # No audio received at all
                                            current_timeout = 0.5
                                    
                                    response = await asyncio.wait_for(websocket.recv(), timeout=current_timeout)
                                    response_data = json.loads(response)
                                    responses.append(response_data)
                                    metrics.server_responses.append(response_data)
                                    
                                    # Track audio responses (agent speech)
                                    if response_data.get('kind') == 'AudioData':
                                        metrics.audio_chunks_received += 1
                                        agent_audio_chunks_this_turn += 1
                                        last_audio_chunk_time = time.time()
                                        
                                        # Print progress for first few chunks
                                        if agent_audio_chunks_this_turn <= 3:
                                            print(f"      📨 Audio chunk {agent_audio_chunks_this_turn} received")
                                        elif agent_audio_chunks_this_turn == 10:
                                            print(f"      📨 {agent_audio_chunks_this_turn} audio chunks received...")
                                        elif agent_audio_chunks_this_turn % 50 == 0:
                                            print(f"      📨 {agent_audio_chunks_this_turn} audio chunks received...")
                                    
                                    # Also track other response types for debugging
                                    elif len(responses) <= 5:  # Only log first few non-audio responses
                                        resp_type = response_data.get('kind', response_data.get('type', 'unknown'))
                                        print(f"      📨 {resp_type} response received")
                                        
                                except asyncio.TimeoutError:
                                    if last_audio_chunk_time and (time.time() - last_audio_chunk_time) >= audio_silence_timeout:
                                        # We've had enough silence after receiving audio - response is complete
                                        if agent_audio_chunks_this_turn > 0:
                                            response_complete = True
                                            break
                                    elif time.time() >= timeout_deadline:
                                        # Absolute timeout reached
                                        break
                                    # Otherwise continue waiting
                            
                            # Check if we got a complete response or if it failed
                            response_end = time.time()
                            total_response_time_ms = (response_end - response_start) * 1000
                            end_to_end_latency_ms = (response_end - audio_send_start) * 1000
                            
                            if agent_audio_chunks_this_turn == 0:
                                # No audio received - mark as failure
                                turn_failed = True
                                error_msg = f"Turn {turn_idx + 1}: No audio response received within {audio_silence_timeout}s timeout"
                                metrics.errors.append(error_msg)
                                print(f"      ❌ {error_msg}")
                                metrics.failed_turns += 1
                            else:
                                # Success - we got audio response
                                metrics.agent_turns += 1
                                metrics.successful_turns += 1
                                response_complete = True
                                print(f"      ✅ Complete audio response received: {agent_audio_chunks_this_turn} chunks")
                            
                            # Record timing metrics
                            metrics.total_agent_processing_time_ms += total_response_time_ms
                            speech_recognition_time = (response_start - audio_send_complete) * 1000 if agent_audio_chunks_this_turn > 0 else 0
                            metrics.total_speech_recognition_time_ms += speech_recognition_time
                            
                            print(f"      ⏱️  Response time: {total_response_time_ms:.1f}ms")
                            print(f"      ⏱️  End-to-end latency: {end_to_end_latency_ms:.1f}ms")
                            if speech_recognition_time > 0:
                                print(f"      ⏱️  Speech recognition: {speech_recognition_time:.1f}ms")
                            
                        except Exception as e:
                            turn_failed = True
                            error_msg = f"Turn {turn_idx + 1}: {str(e)}"
                            metrics.errors.append(error_msg)
                            print(f"      ❌ Turn error: {error_msg}")
                            metrics.failed_turns += 1
                        finally:
                            # Stop silence streaming
                            silence_streaming_active = False
                            silence_task.cancel()
                            try:
                                await silence_task
                            except asyncio.CancelledError:
                                pass
                        
                        print(f"  🤖 Turn completed: {'✅ Success' if not turn_failed else '❌ Failed'}")
                        print(f"  📊 Audio chunks: {agent_audio_chunks_this_turn}, Total responses: {len(responses)}")
                        
                        # Callback for turn completion
                        if on_turn_complete:
                            try:
                                on_turn_complete(turn, responses)
                            except Exception as e:
                                print(f"  ⚠️ Turn callback error: {e}")
                        
                        # Callback for agent responses
                        if on_agent_response and responses:
                            try:
                                on_agent_response(turn.text, responses)
                            except Exception as e:
                                print(f"  ⚠️ Agent callback error: {e}")
                        
                        # Brief pause before next turn (only if not failed)
                        if not turn_failed:
                            await asyncio.sleep(1.0)  # Slightly longer pause for more realistic conversation
                
                print(f"\n✅ Conversation completed successfully")
                metrics.end_time = time.time()
                
        except Exception as e:
            print(f"❌ Conversation failed: {e}")
            metrics.errors.append(f"Conversation error: {str(e)}")
            metrics.end_time = time.time()
        
        return metrics

    def analyze_metrics(self, metrics: ConversationMetrics) -> Dict[str, Any]:
        """Analyze conversation metrics and return insights."""
        duration_s = metrics.end_time - metrics.start_time
        
        analysis = {
            "session_id": metrics.session_id,
            "template": metrics.template_name,
            "success": len(metrics.errors) == 0,
            "duration_s": duration_s,
            "connection_time_ms": metrics.connection_time_ms,
            
            # Turn metrics
            "user_turns": metrics.user_turns,
            "agent_turns": len([r for r in metrics.server_responses if r.get('kind') == 'AudioData']),
            "total_responses": len(metrics.server_responses),
            
            # Performance metrics
            "avg_speech_recognition_ms": metrics.total_speech_recognition_time_ms / max(1, metrics.user_turns),
            "avg_agent_processing_ms": metrics.total_agent_processing_time_ms / max(1, metrics.user_turns),
            "audio_chunks_received": metrics.audio_chunks_received,
            
            # Quality metrics
            "error_count": len(metrics.errors),
            "failed_turns": metrics.failed_turns,
            "errors": metrics.errors,
            
            # Response analysis
            "response_types": {},
        }
        
        # Analyze response types
        for response in metrics.server_responses:
            resp_type = response.get('kind', response.get('type', 'unknown'))
            analysis["response_types"][resp_type] = analysis["response_types"].get(resp_type, 0) + 1
        
        return analysis

# Example usage and testing
async def main():
    """Example of how to use the conversation simulator."""
    simulator = ConversationSimulator()
    
    # Get a conversation template
    template = ConversationTemplates.get_insurance_inquiry()
    
    # Define callbacks for monitoring
    def on_turn_complete(turn: ConversationTurn, responses: List[Dict]):
        print(f"  📋 Turn completed: '{turn.text}' -> {len(responses)} responses")
    
    def on_agent_response(user_text: str, responses: List[Dict]):
        audio_responses = len([r for r in responses if r.get('kind') == 'AudioData'])
        print(f"  🎤 Agent generated {audio_responses} audio responses to: '{user_text[:30]}...'")
    
    # Run simulation with production audio
    metrics = await simulator.simulate_conversation(
        template,
        on_turn_complete=on_turn_complete,
        on_agent_response=on_agent_response,
        preload_audio=True  # Use production TTS for better recognition
    )
    
    # Analyze results
    analysis = simulator.analyze_metrics(metrics)
    
    print(f"\n📊 CONVERSATION ANALYSIS")
    print(f"=" * 50)
    print(f"Success: {'✅' if analysis['success'] else '❌'}")
    print(f"Duration: {analysis['duration_s']:.2f}s")
    print(f"Connection: {analysis['connection_time_ms']:.1f}ms")
    print(f"User turns: {analysis['user_turns']}")
    print(f"Failed turns: {analysis['failed_turns']}")
    print(f"Agent responses: {analysis['audio_chunks_received']}")
    print(f"Avg recognition time: {analysis['avg_speech_recognition_ms']:.1f}ms")
    print(f"Avg agent processing: {analysis['avg_agent_processing_ms']:.1f}ms")
    
    if analysis['errors']:
        print(f"❌ Errors: {analysis['error_count']}")
        for error in analysis['errors']:
            print(f"  - {error}")

if __name__ == "__main__":
    asyncio.run(main())