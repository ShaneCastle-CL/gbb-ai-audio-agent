/**
 * ControlSection Component
 * 
 * Main control buttons for microphone, phone calls, and reset functionality
 */
import React, { useState } from 'react';
import { styles } from '../../styles/appStyles';

const ControlSection = ({ 
  recording, 
  onToggleRecording, 
  callActive, 
  onToggleCall, 
  onReset,
  showPhoneInput,
  onTogglePhoneInput,
  targetPhoneNumber,
  onPhoneNumberChange,
  onCall
}) => {
  // Tooltip states
  const [showResetTooltip, setShowResetTooltip] = useState(false);
  const [showMicTooltip, setShowMicTooltip] = useState(false);
  const [showPhoneTooltip, setShowPhoneTooltip] = useState(false);

  // Hover states for enhanced button effects
  const [resetHovered, setResetHovered] = useState(false);
  const [micHovered, setMicHovered] = useState(false);
  const [phoneHovered, setPhoneHovered] = useState(false);

  const handlePhoneCall = () => {
    if (targetPhoneNumber) {
      onCall(targetPhoneNumber);
      onTogglePhoneInput(); // Close the input
    }
  };

  return (
    <div style={styles.controlSection}>
      <div style={styles.controlContainer}>
        {/* Reset Button */}
        <div style={{ position: "relative" }}>
          <button
            style={styles.resetButton(false, resetHovered)}
            onClick={onReset}
            onMouseEnter={() => {
              setResetHovered(true);
              setShowResetTooltip(true);
            }}
            onMouseLeave={() => {
              setResetHovered(false);
              setShowResetTooltip(false);
            }}
          >
            🔄
          </button>
          <div style={{
            ...styles.buttonTooltip,
            ...(showResetTooltip ? styles.buttonTooltipVisible : {})
          }}>
            Reset Conversation
          </div>
        </div>

        {/* Microphone Button */}
        <div style={{ position: "relative" }}>
          <button
            style={styles.micButton(recording, micHovered)}
            onClick={onToggleRecording}
            onMouseEnter={() => {
              setMicHovered(true);
              setShowMicTooltip(true);
            }}
            onMouseLeave={() => {
              setMicHovered(false);
              setShowMicTooltip(false);
            }}
          >
            {recording ? "🎙️" : "🎤"}
          </button>
          <div style={{
            ...styles.buttonTooltip,
            ...(showMicTooltip ? styles.buttonTooltipVisible : {})
          }}>
            {recording ? "Stop Recording" : "Start Voice Chat"}
          </div>
        </div>

        {/* Phone/Call Me Button */}
        <div style={{ position: "relative" }}>
          <button
            style={styles.callMeButton(callActive)}
            onClick={onToggleCall}
            onMouseEnter={() => {
              setPhoneHovered(true);
              setShowPhoneTooltip(true);
            }}
            onMouseLeave={() => {
              setPhoneHovered(false);
              setShowPhoneTooltip(false);
            }}
          >
            {callActive ? "📞 End Call" : "📞 Call Me"}
          </button>
          <div style={{
            ...styles.buttonTooltip,
            ...(showPhoneTooltip ? styles.buttonTooltipVisible : {})
          }}>
            {callActive ? "End the current call" : "Start a phone call"}
          </div>
        </div>

        {/* Phone Input Button */}
        <div style={{ position: "relative" }}>
          <button
            style={styles.phoneButton(showPhoneInput, phoneHovered)}
            onClick={onTogglePhoneInput}
            onMouseEnter={() => {
              setPhoneHovered(true);
            }}
            onMouseLeave={() => {
              setPhoneHovered(false);
            }}
          >
            ☎️
          </button>
        </div>
      </div>

      {/* Phone Input Section */}
      {showPhoneInput && (
        <div style={styles.phoneInputSection}>
          <input
            type="tel"
            placeholder="Enter phone number (+1234567890)"
            value={targetPhoneNumber}
            onChange={(e) => onPhoneNumberChange(e.target.value)}
            style={styles.phoneInput}
            autoFocus
          />
          <button
            onClick={handlePhoneCall}
            style={styles.callMeButton(false)}
          >
            📞 Call This Number
          </button>
        </div>
      )}
    </div>
  );
};

export default ControlSection;
