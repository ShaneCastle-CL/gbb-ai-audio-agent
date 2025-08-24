#!/usr/bin/env python3
"""
Baseline Performance Test for Media Orchestration System
========================================================

Comprehensive baseline test to measure current system performance before P0 optimizations.
Captures detailed metrics on:
- Connection establishment latency
- Audio processing throughput
- Memory usage patterns
- Resource pool utilization
- WebSocket handler performance
- Cross-connection isolation

This establishes our performance baseline for iterative improvements.

Usage:
    python -m tests.load.baseline_performance_test --concurrency 10 --duration 60
    python -m tests.load.baseline_performance_test --profile --output-dir ./baseline_results
"""

import argparse
import asyncio
import json
import os
import psutil
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import tracemalloc
import gc

import websockets
import aiohttp
import base64
import traceback
import struct


@dataclass
class ConnectionMetrics:
    """Metrics for a single WebSocket connection."""
    call_id: str
    connect_start_ms: float
    connect_end_ms: float
    connect_latency_ms: float
    first_message_ms: Optional[float] = None
    greeting_latency_ms: Optional[float] = None
    messages_received: int = 0
    audio_chunks_sent: int = 0
    errors: List[str] = field(default_factory=list)
    memory_peak_mb: float = 0.0
    disconnect_ms: Optional[float] = None
    barge_in_detected: Optional[bool] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SystemMetrics:
    """System-wide performance metrics."""
    timestamp: str
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    active_connections: int
    total_connections: int
    open_files: int
    tcp_connections: int
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BaselineResults:
    """Complete baseline test results."""
    test_config: Dict[str, Any]
    start_time: str
    end_time: str
    duration_seconds: float
    connections: List[ConnectionMetrics]
    system_metrics: List[SystemMetrics]
    summary_stats: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_config": self.test_config,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "connections": [c.to_dict() for c in self.connections],
            "system_metrics": [s.to_dict() for s in self.system_metrics],
            "summary_stats": self.summary_stats
        }


class BaselineProfiler:
    """Performance profiler for baseline measurements."""
    
    def __init__(self):
        self.start_time = None
        self.system_metrics = []
        self.monitoring_active = False
        
    async def start_monitoring(self):
        """Start system monitoring task."""
        self.monitoring_active = True
        self.start_time = time.time()
        tracemalloc.start()
        
        async def monitor_loop():
            while self.monitoring_active:
                try:
                    # Collect system metrics
                    process = psutil.Process()
                    
                    # Get memory info
                    memory_info = process.memory_info()
                    memory_percent = process.memory_percent()
                    
                    # Get CPU usage
                    cpu_percent = process.cpu_percent(interval=0.1)
                    
                    # Get file descriptor count
                    try:
                        open_files = len(process.open_files())
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        open_files = 0
                    
                    # Get connection count
                    try:
                        connections = process.net_connections()
                        tcp_connections = len([c for c in connections if c.type == 1])  # TCP
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        tcp_connections = 0
                    
                    metrics = SystemMetrics(
                        timestamp=datetime.utcnow().isoformat(),
                        cpu_percent=cpu_percent,
                        memory_percent=memory_percent,
                        memory_used_mb=memory_info.rss / (1024 * 1024),
                        active_connections=0,  # Will be updated by connection tracker
                        total_connections=0,   # Will be updated by connection tracker
                        open_files=open_files,
                        tcp_connections=tcp_connections
                    )
                    
                    self.system_metrics.append(metrics)
                    await asyncio.sleep(1.0)  # Sample every second
                    
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    await asyncio.sleep(1.0)
        
        asyncio.create_task(monitor_loop())
    
    def stop_monitoring(self):
        """Stop system monitoring."""
        self.monitoring_active = False
        tracemalloc.stop()


async def generate_audio_data() -> bytes:
    """Generate synthetic audio data for testing."""
    # Default: 100ms of silent PCM audio (16kHz, 16-bit)
    samples = 1600  # 100ms at 16kHz
    return b'\x00\x00' * samples


def synthesize_pcm_for_phrase(phrase: str, duration_s: float = 0.9, rate: int = 16000) -> bytes:
    """Synthesize a deterministic, short PCM waveform for a given phrase.

    This is NOT real speech synthesis. It produces a tone-sequence derived from
    the phrase so tests send actual PCM audio bytes (16kHz, 16-bit little-endian)
    encoded as base64 for the ACS handler. The recognizer may not transcribe
    this as text, but it exercises the audio path with realistic payloads.

    :param phrase: input phrase to synthesize (used to vary frequencies)
    :param duration_s: total duration in seconds
    :param rate: sample rate (Hz)
    :return: raw PCM bytes (int16 little-endian)
    """
    import math
    # Create a sequence of tones based on phrase character codes
    if not phrase:
        phrase = "silence"

    samples_total = int(duration_s * rate)
    pcm = bytearray()
    # Use up to 4 tone segments derived from phrase hash
    seg_count = min(4, max(1, len(phrase)))
    seg_len = samples_total // seg_count

    # Produce deterministic frequencies from phrase
    base = sum(ord(c) for c in phrase) % 400 + 200  # 200..599
    for i in range(seg_count):
        freq = base + (i * 80)
        for n in range(seg_len):
            t = (i * seg_len + n) / rate
            # simple amplitude envelope to avoid clicks
            amp = 0.2 * (1.0 - (n / seg_len) * 0.5)
            sample = int(amp * 32767.0 * math.sin(2.0 * math.pi * freq * t))
            # pack as little-endian signed 16-bit
            pcm += struct.pack('<h', sample)

    # If samples_total not matched due to integer division, pad with zeros
    current_samples = len(pcm) // 2
    if current_samples < samples_total:
        remaining = samples_total - current_samples
        pcm += b'\x00\x00' * remaining

    return bytes(pcm)


async def run_single_connection(
    connection_id: str, 
    ws_url: str, 
    test_config: Dict[str, Any],
    profiler: BaselineProfiler
) -> ConnectionMetrics:
    """Run a single WebSocket connection test."""
    
    call_id = f"baseline-{connection_id}-{int(time.time())}"
    metrics = ConnectionMetrics(
        call_id=call_id,
        connect_start_ms=time.perf_counter() * 1000,
        connect_end_ms=0,
        connect_latency_ms=0
    )
    
    headers = {"x-ms-call-connection-id": call_id}
    
    try:
        # Measure connection establishment
        connect_start = time.perf_counter()

        # Ensure call_connection_id is present as a query parameter so the server
        # can extract it immediately and create the media handler.
        from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl
        import logging

        parsed = urlparse(ws_url)
        qs = dict(parse_qsl(parsed.query))
        qs.update({"call_connection_id": call_id})
        new_query = urlencode(qs)
        connect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

        async with websockets.connect(
            connect_url,
            additional_headers=headers,
            max_size=2**22,  # 4MB max message size
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as websocket:
            
            connect_end = time.perf_counter()
            metrics.connect_end_ms = connect_end * 1000
            metrics.connect_latency_ms = (connect_end - connect_start) * 1000
            
            session_start = time.perf_counter()
            first_message_received = False
            
            # Start audio sender task
            async def send_audio():
                if not test_config.get('send_audio', True):
                    return

                nonlocal greeting_received_event, last_server_audio_time, barge_in_started_time, barge_in_detected_flag

                try:
                    audio_interval = test_config.get('audio_interval_ms', 100) / 1000.0
                    total_duration = test_config.get('connection_duration', 30)

                    # Send an initial AudioMetadata message to trigger recognizer start
                    metadata_msg = {"kind": "AudioMetadata", "payload": {"format": "pcm", "rate": 16000}}
                    await websocket.send(json.dumps(metadata_msg))

                    # Send a short warm-up chunk and sleep briefly to avoid racing recognizer.start()
                    try:
                        warm_pcm = synthesize_pcm_for_phrase("warmup", duration_s=0.12, rate=16000)
                        warm_b64 = base64.b64encode(warm_pcm).decode('utf-8')
                        warm_msg = {
                            "kind": "AudioData",
                            "audioData": {
                                "data": warm_b64,
                                "silent": False,
                                "timestamp": time.time()
                            }
                        }
                        await websocket.send(json.dumps(warm_msg))
                        # give server a short moment to start recognizer and attach push stream
                        await asyncio.sleep(0.18)
                    except Exception:
                        # best-effort warmup; don't fail the whole test if it errors
                        pass

                    # Wait for server greeting to begin (server may start TTS after metadata)
                    try:
                        await asyncio.wait_for(greeting_received_event.wait(), timeout=5.0)
                        # Lightweight per-module logger (safe to call multiple times)
                        logger = logging.getLogger("baseline_performance")
                        if not logger.handlers:
                            handler = logging.StreamHandler()
                            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
                            logger.addHandler(handler)
                            logger.setLevel(logging.DEBUG)

                        # Mark that greeting was received
                        logger.info(json.dumps({
                            "event": "greeting_received",
                            "call_id": call_id,
                            "connection_id": connection_id,
                            "time": time.time(),
                            "note": "server started speaking or sent greeting"
                        }))

                        # Probe: send a first, short non-silent audio chunk and log detailed diagnostics.
                        try:
                            probe_phrase = f"probe-{connection_id}-{int(time.time())}"
                            probe_pcm = synthesize_pcm_for_phrase(probe_phrase, duration_s=0.25, rate=16000)
                            probe_b64 = base64.b64encode(probe_pcm).decode("utf-8")
                            probe_msg = {
                                "kind": "AudioData",
                                "audioData": {
                                    "data": probe_b64,
                                    "silent": False,
                                    "timestamp": time.time()
                                }
                            }

                            # Log diagnostic info before send
                            logger.debug(json.dumps({
                                "event": "sending_probe_audio",
                                "call_id": call_id,
                                "connection_id": connection_id,
                                "probe_phrase": probe_phrase,
                                "probe_pcm_bytes": len(probe_pcm),
                                "probe_b64_len": len(probe_b64),
                                "websocket_open": getattr(websocket, "open", None),
                                "time": time.time()
                            }))

                            await websocket.send(json.dumps(probe_msg))
                            metrics.audio_chunks_sent += 1

                            # Confirm send in logs
                            logger.info(json.dumps({
                                "event": "probe_audio_sent",
                                "call_id": call_id,
                                "connection_id": connection_id,
                                "probe_pcm_bytes": len(probe_pcm),
                                "audio_chunks_sent": metrics.audio_chunks_sent,
                                "time": time.time()
                            }))

                        except Exception as e:
                            metrics.errors.append(f"probe_send_error: {str(e)}")
                            logger.error(json.dumps({
                                "event": "probe_send_error",
                                "call_id": call_id,
                                "connection_id": connection_id,
                                "error": str(e),
                                "time": time.time()
                            }))
                    except asyncio.TimeoutError:
                        metrics.errors.append('greeting_timeout')

                    # Start barge-in: speak a longer utterance while server may be speaking
                    async def send_barge_in_audio():
                        nonlocal barge_in_started_time
                        barge_phrase = f"This is a longer user utterance meant to barge in and interrupt the server greeting for connection {connection_id}"
                        barge_pcm = synthesize_pcm_for_phrase(barge_phrase, duration_s=3.0, rate=16000)
                        barge_in_started_time = time.perf_counter()

                        # send in 300ms chunks
                        chunk_len = int(0.3 * 16000) * 2
                        for i in range(0, len(barge_pcm), chunk_len):
                            chunk = barge_pcm[i:i+chunk_len]
                            audio_b64 = base64.b64encode(chunk).decode('utf-8')
                            audio_message = {
                                "kind": "AudioData",
                                "audioData": {
                                    "data": audio_b64,
                                    "silent": False,
                                    "timestamp": time.time()
                                }
                            }
                            await websocket.send(json.dumps(audio_message))
                            metrics.audio_chunks_sent += 1
                            await asyncio.sleep(0.25)

                    barge_task = asyncio.create_task(send_barge_in_audio())

                    # After barge-in begins, continue sending short silent chunks until connection end
                    end_time = time.perf_counter() + total_duration - 2.0
                    while time.perf_counter() < end_time:
                        audio_data = await generate_audio_data()
                        audio_b64 = base64.b64encode(audio_data).decode('utf-8')
                        audio_message = {
                            "kind": "AudioData",
                            "audioData": {
                                "data": audio_b64,
                                "silent": True,
                                "timestamp": time.time()
                            }
                        }
                        await websocket.send(json.dumps(audio_message))
                        metrics.audio_chunks_sent += 1
                        await asyncio.sleep(audio_interval)

                    try:
                        await asyncio.wait_for(barge_task, timeout=5.0)
                    except Exception:
                        pass

                except Exception as e:
                    metrics.errors.append(f"audio_send_error: {str(e)}")
            
            # Track whether the server sent any audio back (e.g., greeting TTS or audio responses)
            server_sent_audio = False
            # Greeting event (set when server starts speaking or sends greeting text)
            greeting_received_event = asyncio.Event()
            # Timestamp (monotonic) of last server audio observed
            last_server_audio_time = 0.0
            # Timestamp when client started barge-in speech
            barge_in_started_time = 0.0
            # Flag set if barge-in caused server to stop
            barge_in_detected_flag = False

            # Start message receiver task
            async def receive_messages():
                nonlocal first_message_received
                nonlocal server_sent_audio
                try:
                    timeout = test_config.get('message_timeout', 5.0)
                    end_time = time.perf_counter() + test_config.get('connection_duration', 30)

                    while time.perf_counter() < end_time:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)

                            if not first_message_received:
                                first_message_received = True
                                metrics.first_message_ms = (time.perf_counter() - session_start) * 1000

                            metrics.messages_received += 1

                            # Try to parse structured messages from server (some servers send JSON envelopes)
                            try:
                                payload = json.loads(message) if isinstance(message, str) else None
                            except Exception:
                                payload = None

                            # If server sends an audio envelope, mark that we received server audio
                            if isinstance(payload, dict):
                                kind = payload.get('kind') or payload.get('type')
                                # Common envelope keys include 'AudioData' or 'audio' vocabulary
                                if kind and (str(kind).lower() == 'audiodata' or 'audio' in str(kind).lower()):
                                    server_sent_audio = True
                                    last_server_audio_time = time.perf_counter()
                                    if not greeting_received_event.is_set():
                                        greeting_received_event.set()
                                # Some servers send audio under nested fields
                                if 'audio' in payload or 'audioData' in payload:
                                    server_sent_audio = True
                                    last_server_audio_time = time.perf_counter()
                                    if not greeting_received_event.is_set():
                                        greeting_received_event.set()

                            # Check for greeting message and record greeting latency
                            if isinstance(message, str) and any(
                                greeting in message.lower() for greeting in ['hi there', 'thank you', 'welcome', 'hello']
                            ):
                                metrics.greeting_latency_ms = (time.perf_counter() - session_start) * 1000
                                if not greeting_received_event.is_set():
                                    greeting_received_event.set()

                        except asyncio.TimeoutError:
                            # No message received in this interval, continue waiting
                            continue
                        except websockets.ConnectionClosed:
                            # Remote closed the connection
                            break
                except Exception as e:
                    metrics.errors.append(f"receive_error: {str(e)}")
            # Run both tasks concurrently
            sender_task = asyncio.create_task(send_audio())
            receiver_task = asyncio.create_task(receive_messages())
            
            # Wait for completion or timeout
            try:
                await asyncio.gather(sender_task, receiver_task)
            except Exception as e:
                metrics.errors.append(f"task_error: {str(e)}")
            finally:
                # Clean up tasks
                sender_task.cancel()
                receiver_task.cancel()
                
                # Wait briefly for cleanup
                await asyncio.sleep(0.1)
                # After tasks complete, if we neither heard a greeting nor any server audio, mark as failure
                if metrics.greeting_latency_ms is None and not server_sent_audio:
                    metrics.errors.append("no_greeting_or_audio_response")

                # Determine whether barge-in was detected: if we started barge-in and server stopped shortly after
                if barge_in_started_time:
                    if last_server_audio_time == 0.0 or last_server_audio_time < (barge_in_started_time + 0.5):
                        barge_in_detected_flag = True
                metrics.barge_in_detected = bool(barge_in_detected_flag)
    
    except Exception as e:
        metrics.errors.append(f"connection_error: {str(e)}")
    
    finally:
        metrics.disconnect_ms = time.perf_counter() * 1000
        
        # Measure peak memory usage
        try:
            current, peak = tracemalloc.get_traced_memory()
            metrics.memory_peak_mb = peak / (1024 * 1024)
        except:
            metrics.memory_peak_mb = 0.0
    
    return metrics


async def run_concurrent_connections(
    concurrency: int,
    ws_url: str,
    test_config: Dict[str, Any],
    profiler: BaselineProfiler
) -> List[ConnectionMetrics]:
    """Run multiple concurrent connections."""
    
    print(f"Starting {concurrency} concurrent connections...")
    
    # Create connection tasks
    tasks = [
        run_single_connection(
            connection_id=str(i),
            ws_url=ws_url,
            test_config=test_config,
            profiler=profiler
        )
        for i in range(concurrency)
    ]
    
    # Execute all connections concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out exceptions and log them
    connection_metrics = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Connection {i} failed: {result}")
        else:
            connection_metrics.append(result)
    
    print(f"Completed {len(connection_metrics)}/{concurrency} connections successfully")
    return connection_metrics


def calculate_summary_stats(connections: List[ConnectionMetrics]) -> Dict[str, Any]:
    """Calculate summary statistics from connection metrics."""
    
    if not connections:
        return {"error": "No successful connections"}
    
    # Extract metrics
    connect_latencies = [c.connect_latency_ms for c in connections if c.connect_latency_ms > 0]
    greeting_latencies = [c.greeting_latency_ms for c in connections if c.greeting_latency_ms is not None]
    first_message_latencies = [c.first_message_ms for c in connections if c.first_message_ms is not None]
    messages_per_connection = [c.messages_received for c in connections]
    audio_chunks_per_connection = [c.audio_chunks_sent for c in connections]
    memory_usage = [c.memory_peak_mb for c in connections if c.memory_peak_mb > 0]
    error_counts = [len(c.errors) for c in connections]
    
    def calculate_stats(values: List[float], name: str) -> Dict[str, Any]:
        if not values:
            return {f"{name}_count": 0}
        
        return {
            f"{name}_count": len(values),
            f"{name}_min": min(values),
            f"{name}_max": max(values),
            f"{name}_mean": statistics.mean(values),
            f"{name}_median": statistics.median(values),
            f"{name}_p95": statistics.quantiles(values, n=20)[18] if len(values) >= 20 else max(values),
            f"{name}_p99": statistics.quantiles(values, n=100)[98] if len(values) >= 100 else max(values),
            f"{name}_stdev": statistics.stdev(values) if len(values) > 1 else 0.0
        }
    
    summary = {
        "total_connections": len(connections),
        "successful_connections": len([c for c in connections if not c.errors]),
        "failed_connections": len([c for c in connections if c.errors]),
        "total_errors": sum(error_counts),
        **calculate_stats(connect_latencies, "connection_latency_ms"),
        **calculate_stats(greeting_latencies, "greeting_latency_ms"),
        **calculate_stats(first_message_latencies, "first_message_latency_ms"),
        **calculate_stats(messages_per_connection, "messages_per_connection"),
        **calculate_stats(audio_chunks_per_connection, "audio_chunks_per_connection"),
        **calculate_stats(memory_usage, "memory_usage_mb"),
    }
    
    return summary


async def run_baseline_test(args) -> BaselineResults:
    """Run complete baseline performance test."""
    
    test_config = {
        "concurrency": args.concurrency,
        "connection_duration": args.duration,
        "send_audio": args.send_audio,
        "audio_interval_ms": args.audio_interval,
        "message_timeout": args.message_timeout,
        "ws_url": args.ws_url,
        "iterations": getattr(args, 'iterations', 1)
    }
    
    print(f"Starting baseline performance test with config: {test_config}")
    
    # Initialize profiler
    profiler = BaselineProfiler()
    await profiler.start_monitoring()
    
    start_time = datetime.utcnow()
    test_start = time.perf_counter()
    
    try:
        # Run the test
        all_connections = []
        
        for iteration in range(test_config['iterations']):
            print(f"\nRunning iteration {iteration + 1}/{test_config['iterations']}")
            
            connections = await run_concurrent_connections(
                concurrency=args.concurrency,
                ws_url=args.ws_url,
                test_config=test_config,
                profiler=profiler
            )
            
            all_connections.extend(connections)
            
            # Brief pause between iterations
            if iteration < test_config['iterations'] - 1:
                await asyncio.sleep(2.0)
    
    finally:
        profiler.stop_monitoring()
    
    end_time = datetime.utcnow()
    test_duration = time.perf_counter() - test_start
    
    # Calculate summary statistics
    summary_stats = calculate_summary_stats(all_connections)
    
    # Create results
    results = BaselineResults(
        test_config=test_config,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
        duration_seconds=test_duration,
        connections=all_connections,
        system_metrics=profiler.system_metrics,
        summary_stats=summary_stats
    )
    
    return results


def save_baseline_results(results: BaselineResults, output_dir: str):
    """Save baseline results to files."""
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save complete results as JSON
    results_file = output_path / f"baseline_results_{timestamp}.json"
    with open(results_file, 'w') as f:
        json.dump(results.to_dict(), f, indent=2, default=str)
    
    # Save summary for quick reference
    summary_file = output_path / f"baseline_summary_{timestamp}.json"
    with open(summary_file, 'w') as f:
        json.dump({
            "test_config": results.test_config,
            "test_duration_seconds": results.duration_seconds,
            "summary_stats": results.summary_stats,
            "system_peak_cpu": max((s.cpu_percent for s in results.system_metrics), default=0),
            "system_peak_memory_mb": max((s.memory_used_mb for s in results.system_metrics), default=0),
        }, f, indent=2)
    
    # Create CSV for easy analysis
    csv_file = output_path / f"baseline_connections_{timestamp}.csv"
    with open(csv_file, 'w') as f:
        f.write("call_id,connect_latency_ms,greeting_latency_ms,first_message_ms,messages_received,audio_chunks_sent,errors,memory_peak_mb\n")
        for conn in results.connections:
            f.write(f"{conn.call_id},{conn.connect_latency_ms},{conn.greeting_latency_ms or ''},"
                   f"{conn.first_message_ms or ''},{conn.messages_received},{conn.audio_chunks_sent},"
                   f"{len(conn.errors)},{conn.memory_peak_mb}\n")
    
    print(f"\nBaseline results saved to:")
    print(f"  Full results: {results_file}")
    print(f"  Summary: {summary_file}")
    print(f"  CSV data: {csv_file}")


def print_baseline_summary(results: BaselineResults):
    """Print baseline test summary to console."""
    
    stats = results.summary_stats
    
    print(f"\n{'='*60}")
    print(f"BASELINE PERFORMANCE TEST RESULTS")
    print(f"{'='*60}")
    print(f"Test Duration: {results.duration_seconds:.1f}s")
    print(f"Concurrency: {results.test_config['concurrency']}")
    print(f"Total Connections: {stats['total_connections']}")
    print(f"Successful: {stats['successful_connections']}")
    print(f"Failed: {stats['failed_connections']}")
    print(f"Total Errors: {stats['total_errors']}")
    
    if stats.get('connection_latency_ms_count', 0) > 0:
        print(f"\nConnection Latency (ms):")
        print(f"  Mean: {stats['connection_latency_ms_mean']:.1f}")
        print(f"  Median: {stats['connection_latency_ms_median']:.1f}")
        print(f"  P95: {stats['connection_latency_ms_p95']:.1f}")
        print(f"  P99: {stats['connection_latency_ms_p99']:.1f}")
        print(f"  Max: {stats['connection_latency_ms_max']:.1f}")
    
    if stats.get('greeting_latency_ms_count', 0) > 0:
        print(f"\nGreeting Latency (ms):")
        print(f"  Mean: {stats['greeting_latency_ms_mean']:.1f}")
        print(f"  Median: {stats['greeting_latency_ms_median']:.1f}")
        print(f"  P95: {stats['greeting_latency_ms_p95']:.1f}")
    
    if results.system_metrics:
        cpu_peak = max(s.cpu_percent for s in results.system_metrics)
        mem_peak = max(s.memory_used_mb for s in results.system_metrics)
        print(f"\nSystem Resource Usage:")
        print(f"  Peak CPU: {cpu_peak:.1f}%")
        print(f"  Peak Memory: {mem_peak:.1f} MB")
    
    print(f"{'='*60}")


def parse_args():
    """Parse command line arguments."""
    
    parser = argparse.ArgumentParser(description="Baseline performance test for media orchestration")
    parser.add_argument("--ws-url", default="ws://localhost:8010/api/v1/media/stream", 
                       help="WebSocket URL to test")
    parser.add_argument("--concurrency", "-c", type=int, default=10,
                       help="Number of concurrent connections")
    parser.add_argument("--duration", "-d", type=int, default=30,
                       help="Connection duration in seconds")
    parser.add_argument("--iterations", type=int, default=1,
                       help="Number of test iterations")
    parser.add_argument("--send-audio", action="store_true", default=True,
                       help="Send audio data during test")
    parser.add_argument("--audio-interval", type=int, default=100,
                       help="Audio chunk interval in ms")
    parser.add_argument("--message-timeout", type=float, default=5.0,
                       help="Message receive timeout in seconds")
    parser.add_argument("--output-dir", default="./baseline_results",
                       help="Output directory for results")
    parser.add_argument("--profile", action="store_true",
                       help="Enable detailed profiling")
    
    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()
    
    try:
        # Run baseline test
        results = await run_baseline_test(args)
        
        # Print summary
        print_baseline_summary(results)
        
        # Save results
        save_baseline_results(results, args.output_dir)
        
        # Exit with error code if significant failures
        failure_rate = results.summary_stats['failed_connections'] / results.summary_stats['total_connections']
        if failure_rate > 0.1:  # More than 10% failures
            print(f"\nWARNING: High failure rate ({failure_rate:.1%})")
            sys.exit(1)
        
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nTest failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())