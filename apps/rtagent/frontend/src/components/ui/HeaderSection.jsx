/**
 * HeaderSection Component
 * 
 * Application header with title, logo, and navigation elements
 */
import React from 'react';
import { styles } from '../../styles/appStyles';

const HeaderSection = ({ onToggleBackendStats, showBackendStats }) => {
  return (
    <header style={styles.appHeader}>
      <div style={styles.headerContent}>
        <div style={styles.logoSection}>
          <div style={styles.logo}>🎯</div>
          <h1 style={styles.title}>Real-Time Voice Agent</h1>
        </div>
        
        <div style={styles.headerControls}>
          <button
            onClick={onToggleBackendStats}
            style={styles.devButton(showBackendStats)}
            title={showBackendStats ? "Hide Debug Info" : "Show Debug Info"}
          >
            {showBackendStats ? "🔍 Hide Debug" : "🔧 Debug"}
          </button>
        </div>
      </div>
    </header>
  );
};

export default HeaderSection;
