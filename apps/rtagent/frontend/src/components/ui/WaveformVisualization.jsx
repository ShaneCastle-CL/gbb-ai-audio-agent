/**
 * WaveformVisualization Component
 * 
 * Real-time audio waveform display with amplitude tracking
 */
import React, { useEffect, useRef } from 'react';
import { styles } from '../../styles/appStyles';

const WaveformVisualization = ({ amplitude, isRecording }) => {
  const canvasRef = useRef(null);
  const waveformDataRef = useRef([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    // Update waveform data
    waveformDataRef.current.push(amplitude);
    if (waveformDataRef.current.length > 100) {
      waveformDataRef.current.shift();
    }

    // Draw waveform
    ctx.strokeStyle = isRecording ? '#10b981' : '#64748b';
    ctx.lineWidth = 2;
    ctx.beginPath();

    waveformDataRef.current.forEach((amp, index) => {
      const x = (index / waveformDataRef.current.length) * width;
      const y = height / 2 + (amp * height / 2) * 0.8;
      
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();
  }, [amplitude, isRecording]);

  return (
    <div style={styles.waveformSection}>
      <div style={styles.waveformSectionTitle}>
        {isRecording ? "🎙️ Listening..." : "🌊 Voice Waveform"}
      </div>
      <div style={styles.waveformContainer}>
        <canvas
          ref={canvasRef}
          width={400}
          height={60}
          style={styles.waveformSvg}
        />
      </div>
      <div style={styles.sectionDivider}></div>
    </div>
  );
};

export default WaveformVisualization;
