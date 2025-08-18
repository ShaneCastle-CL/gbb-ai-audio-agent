/**
 * Main Application Entry Point
 * 
 * Clean entry point that imports and renders the complete RealTimeVoiceApp component
 * with exact same functionality as original but with clean modular architecture
 */
import React from 'react';
import './App.css'; // Import background styles
import RealTimeVoiceApp from './components/RealTimeVoiceApp';

const App = () => {
  return <RealTimeVoiceApp />;
};

export default App;
