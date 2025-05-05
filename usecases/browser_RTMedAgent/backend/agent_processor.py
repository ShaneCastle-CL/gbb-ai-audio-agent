# filepath: /Users/jinle/Repos/_AIProjects/gbb-ai-audio-agent/usecases/browser_RTMedAgent/backend/agent_processor.py
import json
import asyncio
from typing import Optional, Any
from fastapi import WebSocket
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from conversation_manager import ConversationManager
from utils.prompt_manager import PromptManager
from utils.tool_manager import ToolManager
from utils.connection_manager import ConnectionManager
from utils.ml_logging import get_logger
from acs import AcsCaller # Assuming acs.py is in the same directory or accessible path
# Import the browser TTS function - adjust path if needed
from server import send_tts_audio # This might create a circular dependency if server imports this. Consider moving send_tts_audio.

logger = get_logger()

class AgentProcessor:
    def __init__(
        self,
        openai_client: AsyncOpenAI,
        prompt_manager: PromptManager,
        tool_manager: ToolManager,
        connection_manager: ConnectionManager,
        acs_caller: Optional[AcsCaller] = None,
        # tts_client: Optional[Any] = None # Add if browser TTS client is needed directly
    ):
        self.openai_client = openai_client
        self.prompt_manager = prompt_manager
        self.tool_manager = tool_manager
        self.manager = connection_manager # Use the central connection manager
        self.acs_caller = acs_caller
        # self.tts_client = tts_client # Store if needed

    async def process_gpt_response(
        self,
        cm: ConversationManager,
        user_prompt: str,
        websocket: WebSocket, # WebSocket for the specific client connection (browser or ACS internal)
        is_acs: bool = False,
        call_id: Optional[str] = None,
    ):
        """Processes user input with OpenAI, handles streaming, tool calls, and TTS."""
        logger.info(f"Processing GPT request. User prompt: '{user_prompt[:50]}...' Is ACS: {is_acs}, Call ID: {call_id}")
        cm.add_message("user", user_prompt)

        # Broadcast user message to frontend(s) via ConnectionManager
        await self.manager.broadcast(json.dumps({"type": "user", "message": user_prompt}))

        full_response = ""
        tool_calls = []

        try:
            stream = await self.openai_client.chat.completions.create(
                model=cm.model,
                messages=cm.get_messages(),
                stream=True,
                tools=self.tool_manager.get_tools_specs() if self.tool_manager.has_tools() else None,
                tool_choice="auto" if self.tool_manager.has_tools() else None,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta:
                    if delta.content:
                        content = delta.content
                        full_response += content
                        # Stream intermediate text to frontend(s)
                        await self.manager.broadcast(json.dumps({"type": "assistant_intermediate", "message": content}))
                        # logger.debug(f"Streamed intermediate: {content}")

                    if delta.tool_calls:
                        # Accumulate tool call chunks
                        for tool_call_chunk in delta.tool_calls:
                            index = tool_call_chunk.index
                            if len(tool_calls) <= index:
                                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            
                            tc = tool_calls[index]
                            if tool_call_chunk.id:
                                tc["id"] = tool_call_chunk.id
                            if tool_call_chunk.function:
                                if tool_call_chunk.function.name:
                                    tc["function"]["name"] += tool_call_chunk.function.name
                                if tool_call_chunk.function.arguments:
                                    tc["function"]["arguments"] += tool_call_chunk.function.arguments
            
            logger.info(f"GPT response finished. Full text: '{full_response[:50]}...', Tool calls: {len(tool_calls)}")

            # Add assistant's full response OR tool calls to conversation history
            if tool_calls:
                 # Convert accumulated dicts back to ToolCall objects for storage if needed by CM
                 # For now, storing the dict representation which matches API structure
                cm.add_message("assistant", content=None, tool_calls=tool_calls) 
                # Process tool calls
                await self.handle_tool_call(cm, tool_calls, websocket, is_acs, call_id)
            elif full_response:
                cm.add_message("assistant", full_response)
                # --- Send TTS ---
                if is_acs and call_id and self.acs_caller:
                    logger.info(f"Sending TTS response via ACS for call {call_id}: {full_response}")
                    await self.acs_caller.play_response(call_id, full_response)
                    # Also broadcast final message to frontend
                    await self.manager.broadcast(json.dumps({"type": "assistant", "message": full_response}))
                elif not is_acs:
                    logger.info(f"Sending TTS response via browser WebSocket: {full_response}")
                    # Assuming send_tts_audio is available and works with the browser websocket
                    await send_tts_audio(full_response, websocket) 
                    # Also broadcast final message via events ws
                    await self.manager.broadcast(json.dumps({"type": "assistant", "message": full_response}))
                else:
                     logger.warning("Cannot send TTS: ACS context specified but AcsCaller not available or call_id missing.")
            else:
                logger.warning("GPT response finished with no text content or tool calls.")


        except Exception as e:
            logger.error(f"Error during OpenAI stream processing: {e}", exc_info=True)
            await self.manager.broadcast(json.dumps({"type": "error", "message": f"Error processing request: {e}"}))
            # Optionally send error TTS?
            # error_message = "I encountered an error processing that request."
            # if is_acs and call_id and self.acs_caller:
            #     await self.acs_caller.play_response(call_id, error_message)
            # elif not is_acs:
            #     await send_tts_audio(error_message, websocket)


    async def handle_tool_call(
        self,
        cm: ConversationManager,
        tool_calls: list, # List of tool call dicts from the streaming response
        websocket: WebSocket,
        is_acs: bool = False,
        call_id: Optional[str] = None,
    ):
        """Handles the execution of tool calls identified by the LLM."""
        logger.info(f"Handling {len(tool_calls)} tool call(s).")
        
        # Note: OpenAI API might return multiple tool calls in one response.
        # This example processes them sequentially. Consider parallel execution if tools are independent.
        for tool_call_dict in tool_calls:
            tool_call_id = tool_call_dict['id']
            tool_name = tool_call_dict['function']['name']
            tool_args_str = tool_call_dict['function']['arguments']
            
            logger.info(f"Executing tool: {tool_name} with args: {tool_args_str}")
            await self.manager.broadcast(json.dumps({"type": "status", "message": f"Executing tool: {tool_name}..."}))

            try:
                tool_args = json.loads(tool_args_str)
                tool_response_content = await self.tool_manager.execute_tool(tool_name, **tool_args)
                logger.info(f"Tool {tool_name} executed successfully.")

                # Prepare the message for the next API call
                tool_response_message = {
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "name": tool_name,
                    "content": tool_response_content, # Must be a string
                }
                
                # Process the tool response to get the final user-facing message
                await self.process_tool_followup(cm, tool_response_message, websocket, is_acs, call_id)

            except json.JSONDecodeError:
                logger.error(f"Failed to decode arguments for tool {tool_name}: {tool_args_str}")
                error_message = f"Error: Invalid arguments provided for tool {tool_name}."
                await self.manager.broadcast(json.dumps({"type": "error", "message": error_message}))
                # Send error back to LLM? Or just inform user?
                # For now, just informing user via broadcast.

            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                error_message = f"Error executing tool {tool_name}: {e}"
                await self.manager.broadcast(json.dumps({"type": "error", "message": error_message}))
                 # Send error back to LLM? Or just inform user?
                # For now, just informing user via broadcast.


    async def process_tool_followup(
        self,
        cm: ConversationManager,
        tool_response_message: dict, # The message with role='tool'
        websocket: WebSocket,
        is_acs: bool = False,
        call_id: Optional[str] = None,
    ):
        """Processes the response from a tool execution with OpenAI."""
        logger.info(f"Processing tool followup for tool: {tool_response_message['name']}")
        cm.add_message(role="tool", content=tool_response_message['content'], tool_call_id=tool_response_message['tool_call_id'], name=tool_response_message['name'])

        # Broadcast tool result (optional, might expose internal details)
        # await self.manager.broadcast(json.dumps({"type": "tool_result", "name": tool_response_message['name'], "content": tool_response_message['content']}))

        final_response = ""
        try:
            stream = await self.openai_client.chat.completions.create(
                model=cm.model,
                messages=cm.get_messages(),
                stream=True,
                # Tools are usually not needed in the follow-up, but depends on workflow
                # tools=self.tool_manager.get_tools_specs() if self.tool_manager.has_tools() else None,
                # tool_choice="auto" if self.tool_manager.has_tools() else None,
            )

            async for chunk in stream:
                 delta = chunk.choices[0].delta if chunk.choices else None
                 if delta and delta.content:
                    content = delta.content
                    final_response += content
                    # Stream intermediate text to frontend(s)
                    await self.manager.broadcast(json.dumps({"type": "assistant_intermediate", "message": content}))

            logger.info(f"Tool followup response finished. Full text: '{final_response[:50]}...'" )

            if final_response:
                cm.add_message("assistant", final_response)
                # --- Send TTS ---
                if is_acs and call_id and self.acs_caller:
                    logger.info(f"Sending tool followup TTS via ACS for call {call_id}: {final_response}")
                    await self.acs_caller.play_response(call_id, final_response)
                    await self.manager.broadcast(json.dumps({"type": "assistant", "message": final_response}))
                elif not is_acs:
                    logger.info(f"Sending tool followup TTS via browser WebSocket: {final_response}")
                    await send_tts_audio(final_response, websocket)
                    await self.manager.broadcast(json.dumps({"type": "assistant", "message": final_response}))
                else:
                    logger.warning("Cannot send TTS: ACS context specified but AcsCaller not available or call_id missing.")
            else:
                 logger.warning("Tool followup response finished with no text content.")


        except Exception as e:
            logger.error(f"Error during OpenAI tool followup stream: {e}", exc_info=True)
            await self.manager.broadcast(json.dumps({"type": "error", "message": f"Error processing tool result: {e}"}))
            # Optionally send error TTS
            # error_message = "I encountered an error processing the tool result."
            # if is_acs and call_id and self.acs_caller:
            #     await self.acs_caller.play_response(call_id, error_message)
            # elif not is_acs:
            #     await send_tts_audio(error_message, websocket)

