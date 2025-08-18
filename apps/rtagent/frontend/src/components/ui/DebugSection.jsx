/**
 * DebugSection Component
 * 
 * Development debug information display for backend statistics and connection status
 */
import React from 'react';
import { styles } from '../../styles/appStyles';

const DebugSection = ({ 
  debugInfo, 
  backendStats, 
  lastError, 
  connectionState,
  showBackendStats 
}) => {
  if (!showBackendStats) return null;

  return (
    <div style={styles.debugSection}>
      <h3 style={styles.debugTitle}>Backend Debug Information</h3>
      
      {/* Connection State */}
      <div style={styles.debugItem}>
        <strong>Connection State:</strong> 
        <span style={{
          color: connectionState === 'connected' ? '#4CAF50' : 
                connectionState === 'connecting' ? '#FF9800' : '#f44336',
          marginLeft: '8px'
        }}>
          {connectionState}
        </span>
      </div>

      {/* Last Error */}
      {lastError && (
        <div style={styles.debugItem}>
          <strong>Last Error:</strong> 
          <span style={{ color: '#f44336', marginLeft: '8px' }}>
            {lastError}
          </span>
        </div>
      )}

      {/* Backend Statistics */}
      {backendStats && (
        <div style={styles.debugItem}>
          <strong>Backend Stats:</strong>
          <pre style={styles.debugPre}>
            {JSON.stringify(backendStats, null, 2)}
          </pre>
        </div>
      )}

      {/* Debug Information */}
      {debugInfo && Object.keys(debugInfo).length > 0 && (
        <div style={styles.debugItem}>
          <strong>Debug Info:</strong>
          <pre style={styles.debugPre}>
            {JSON.stringify(debugInfo, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
};

export default DebugSection;
