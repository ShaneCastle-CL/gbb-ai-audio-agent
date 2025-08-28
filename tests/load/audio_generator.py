#!/usr/bin/env python3
"""
Audio Generation Helper for Load Testing
========================================

Uses the production text-to-speech module to generate proper audio files
for conversational flows that will be recognized by the orchestrator.
"""

import os
import sys
import hashlib
from pathlib import Path
from typing import Dict, Optional

# Add the src directory to Python path to import text_to_speech
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
os.environ["DISABLE_CLOUD_TELEMETRY"] = "true"

from speech.text_to_speech import SpeechSynthesizer


class LoadTestAudioGenerator:
    """Generates and caches audio files for load testing using production TTS."""
    
    def __init__(self, cache_dir: str = "tests/load/audio_cache"):
        """Initialize the audio generator with caching directory."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize the speech synthesizer with environment credentials
        self.synthesizer = SpeechSynthesizer(
            region=os.getenv("AZURE_SPEECH_REGION"),
            key=os.getenv("AZURE_SPEECH_KEY"),
            language="en-US",
            voice="en-US-JennyMultilingualNeural",  # Use a clear conversational voice
            playback="never",  # Disable local playback for load testing
            enable_tracing=False  # Disable tracing for performance
        )
        
        print(f"🎤 Audio generator initialized")
        print(f"📂 Cache directory: {self.cache_dir}")
        print(f"🌍 Region: {os.getenv('AZURE_SPEECH_REGION')}")
        print(f"🔑 Using API Key: {'Yes' if os.getenv('AZURE_SPEECH_KEY') else 'No (DefaultAzureCredential)'}")
    
    def _get_cache_filename(self, text: str, voice: str = None) -> str:
        """Generate a cache filename based on text and voice."""
        voice = voice or self.synthesizer.voice
        # Create hash of text and voice for unique filename
        content_hash = hashlib.md5(f"{text}|{voice}".encode()).hexdigest()
        return f"audio_{content_hash}.pcm"
    
    def generate_audio(self, text: str, voice: str = None, force_regenerate: bool = False) -> bytes:
        """
        Generate audio for the given text using Azure TTS.
        
        Args:
            text: Text to synthesize
            voice: Optional voice name (defaults to configured voice)
            force_regenerate: If True, regenerate even if cached
            
        Returns:
            PCM audio data bytes suitable for streaming
        """
        voice = voice or self.synthesizer.voice
        cache_file = self.cache_dir / self._get_cache_filename(text, voice)
        
        # Return cached audio if available and not forcing regeneration
        if cache_file.exists() and not force_regenerate:
            print(f"📄 Using cached audio for: '{text[:50]}...'")
            return cache_file.read_bytes()
        
        print(f"🎵 Generating audio for: '{text[:50]}...'")
        
        try:
            # Generate audio using production TTS with optimized settings for speech recognition
            audio_bytes = self.synthesizer.synthesize_to_pcm(
                text=text,
                voice=voice,
                sample_rate=16000,  # Standard rate for speech recognition
                style="chat",       # Conversational style
                rate="+0%"          # Natural rate
            )
            
            if not audio_bytes:
                raise ValueError("No audio data generated")
            
            # Cache the generated audio
            cache_file.write_bytes(audio_bytes)
            print(f"✅ Generated and cached {len(audio_bytes)} bytes of audio")
            
            return audio_bytes
            
        except Exception as e:
            print(f"❌ Failed to generate audio: {e}")
            # Return empty bytes to avoid breaking the simulation
            return b""
    
    def pregenerate_conversation_audio(self, conversation_texts: list, voice: str = None) -> Dict[str, bytes]:
        """
        Pre-generate audio for all texts in a conversation.
        
        Args:
            conversation_texts: List of text strings to generate audio for
            voice: Optional voice name
            
        Returns:
            Dictionary mapping text to audio bytes
        """
        print(f"🔄 Pre-generating audio for {len(conversation_texts)} utterances...")
        
        audio_cache = {}
        for i, text in enumerate(conversation_texts):
            print(f"📝 [{i+1}/{len(conversation_texts)}] Processing: '{text[:50]}...'")
            audio_bytes = self.generate_audio(text, voice)
            audio_cache[text] = audio_bytes
        
        print(f"✅ Pre-generation complete: {len(audio_cache)} audio files ready")
        return audio_cache
    
    def clear_cache(self):
        """Clear all cached audio files."""
        cache_files = list(self.cache_dir.glob("*.pcm"))
        for cache_file in cache_files:
            cache_file.unlink()
        print(f"🗑️ Cleared {len(cache_files)} cached audio files")
    
    def get_cache_info(self) -> Dict[str, any]:
        """Get information about the audio cache."""
        cache_files = list(self.cache_dir.glob("*.pcm"))
        total_size = sum(f.stat().st_size for f in cache_files)
        
        return {
            "cache_directory": str(self.cache_dir),
            "file_count": len(cache_files),
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024)
        }
    
    def validate_configuration(self) -> bool:
        """Validate that the TTS configuration is working."""
        try:
            print("🔍 Validating Azure TTS configuration...")
            return self.synthesizer.validate_configuration()
        except Exception as e:
            print(f"❌ Configuration validation failed: {e}")
            return False


def main():
    """Test the audio generator."""
    generator = LoadTestAudioGenerator()
    
    # Validate configuration
    if not generator.validate_configuration():
        print("❌ Configuration validation failed. Please check your Azure Speech credentials.")
        return
    
    # Test audio generation
    test_texts = [
        "Hello, my name is Alice Brown, my social is 1234, and my zip code is 60610",
        # "I'm looking to learn about Madrid. Please provide in 100 words",
        # "Actually, I need help with my car insurance.",
        # "What does my policy cover?",
        # "Thank you for the information."
    ]
    
    print(f"\n🧪 Testing audio generation with {len(test_texts)} samples...")
    
    # Pre-generate all audio
    audio_cache = generator.pregenerate_conversation_audio(test_texts)
    
    # Show results
    print(f"\n📊 Results:")
    for text, audio_bytes in audio_cache.items():
        duration = len(audio_bytes) / (16000 * 2)  # 16kHz, 16-bit
        print(f"  '{text[:40]}...' -> {len(audio_bytes)} bytes ({duration:.2f}s)")
    
    # Show cache info
    cache_info = generator.get_cache_info()
    print(f"\n📂 Cache Info:")
    print(f"  Files: {cache_info['file_count']}")
    print(f"  Size: {cache_info['total_size_mb']:.2f} MB")


if __name__ == "__main__":
    main()