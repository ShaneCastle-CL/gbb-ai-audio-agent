/**
 * ChatSection Component
 * 
 * Main chat interface with message display and scrolling
 */
import React, { useRef, useEffect } from 'react';
import { styles } from '../../styles/appStyles';
import ChatBubble from './ChatBubble';

const ChatSection = ({ messages }) => {
  const messageContainerRef = useRef(null);
  const chatRef = useRef(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (messageContainerRef.current) {
      messageContainerRef.current.scrollTo({
        top: messageContainerRef.current.scrollHeight,
        behavior: 'smooth'
      });
    } else if (chatRef.current) {
      chatRef.current.scrollTo({
        top: chatRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
  }, [messages]);

  return (
    <div style={styles.chatSection} ref={chatRef}>
      <div style={styles.chatSectionIndicator}></div>
      
      {messages.length === 0 ? (
        <div style={styles.chatSectionHeader}>
          <div style={styles.chatSectionTitle}>AI Voice Assistant</div>
          <div style={styles.chatSectionSubtitle}>
            Start a conversation by clicking the microphone or "Call Me" button
          </div>
        </div>
      ) : null}
      
      <div style={styles.messageContainer} ref={messageContainerRef}>
        {messages.map((message, index) => (
          <ChatBubble key={index} message={message} />
        ))}
      </div>
    </div>
  );
};

export default ChatSection;
