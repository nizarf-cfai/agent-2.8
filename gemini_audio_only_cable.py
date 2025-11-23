

import asyncio
import os
import sys
import traceback
import pyaudio
import json
import datetime
import canvas_ops
from google import genai
import time
import socket
import threading
import warnings
import random
import time
import signal
import chat_model
import side_agent
from google.genai import types
import helper_model
import faiss_rag

from chroma_db.chroma_script import rag_from_json, get_easl_answer_async
from dotenv import load_dotenv
load_dotenv()

# Suppress Gemini warnings about non-text parts
warnings.filterwarnings("ignore", message=".*non-text parts.*")
warnings.filterwarnings("ignore", message=".*inline_data.*")
warnings.filterwarnings("ignore", message=".*concatenated text result.*")
warnings.filterwarnings("ignore", category=UserWarning)

# Set global socket timeout for extended tool execution
socket.setdefaulttimeout(300)  # 5 minutes timeout
# Audio configuration
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

# Gemini configuration
# MODEL = "gemini-live-2.5-flash-preview-native-audio-09-2025"
MODEL = "models/gemini-2.0-flash-live-001"

CONFIG = {"response_modalities": ["AUDIO"]}

# System prompt for Gemini
with open("system_prompt.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


with open("system_function.json", "r", encoding="utf-8") as f:
    FUNCTION_DECLARATIONS = json.load(f)


CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": SYSTEM_PROMPT,
    "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
    "speech_config":{
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Charon"}},
        "language_code": "en-GB"
    }
}
# Initialize PyAudio
pya = pyaudio.PyAudio()

class AudioOnlyGeminiCable:
    def __init__(self):
        self.audio_in_queue = None
        self.out_queue = None
        self.session = None
        self.audio_stream = None
        self.output_stream = None
        self.function_call_count = 0
        self.last_function_call_time = None
        
        # Initialize Gemini client
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            print("❌ GOOGLE_API_KEY environment variable not set!")
            sys.exit(1)
        
        # Configure client with extended timeout for tool execution
        self.client = genai.Client(
            # vertexai=True,
            # project="medforce-pilot-backend",
            # location="us-central1",
            api_key=api_key,
        )
        print(f"🔧 Gemini client initialized: {hasattr(self.client, 'aio')}")

    def create_func_response(self,fc, result:str):
        function_response = types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response={
                    "result" : result
                }
            )
        return function_response

    async def easl_todo_executor(self, question, todo_obj):
        easl_q = await helper_model.generate_question(question)

        ### EASL TRIGGER ACTION HERE

        for i in range(2):
            await canvas_ops.update_todo(
                {
                    "id" : todo_obj.get('id'),
                    "task_id" : "task-101",
                    "index":f"{i}",
                    "status" : "finished"
                }
            )
            # await asyncio.sleep(random.randint(0.5, 1))

        await canvas_ops.update_todo(
            {
                "id" : todo_obj.get('id'),
                "task_id" : "task-101",
                "index":"",
                "status" : "finished"
            }
        )
        # await asyncio.sleep(random.randint(0.5, 1))

        await canvas_ops.update_todo(
            {
                "id" : todo_obj.get('id'),
                "task_id" : "task-102",
                "index":"",
                "status" : "executing"
            }
        )
        # await asyncio.sleep(random.randint(0.5, 1))
        easl_status = await canvas_ops.initiate_easl_iframe(easl_q)
        for i in range(2):
            await canvas_ops.update_todo(
                {
                    "id" : todo_obj.get('id'),
                    "task_id" : "task-102",
                    "index":f"{i}",
                    "status" : "finished"
                }
            )
            # await asyncio.sleep(random.randint(0.5, 1))
        await canvas_ops.update_todo(
            {
                "id" : todo_obj.get('id'),
                "task_id" : "task-102",
                "index":"",
                "status" : "finished"
            }
        )

        iframe_id = "iframe-item-easl-interface"
        await canvas_ops.focus_item(iframe_id)
        print(f"  ✅ EASL Answer completed")

    async def easl_todo(self,fc, question):
        func_res = []
        func_step = []


        easl_todo_payload = {
            "title": "EASL Guideline Query Workflow",
            "description": "Handling query to EASL Guideline Agent in background",
            "todos": [
                {
                "id": "task-101",
                "text": "Creating question query and generating context",
                "status": "executing",
                "agent": "Data Analyst Agent",
                "subTodos": [
                        {
                        "text": f"Base question : {question}",
                        "status": "executing"
                        },
                        {
                        "text": f"Detailed Question is generated by ContextGen Agent.",
                        "status": "executing"
                        }
                    ]
                },
                {
                "id": "task-102",
                "text": "Send query to EASL Guideline Agent",
                "status": "pending",
                "agent": "Data Analyst Agent",
                "subTodos": [
                        {
                        "text": f"Query is processing",
                        "status": "pending"
                        },
                        {
                        "text": "Result is created in canvas",
                        "status": "pending"
                        }
                    ]
                }
            ]
            }
        
        todo_obj = await canvas_ops.create_todo(easl_todo_payload)
        
        for t in easl_todo_payload.get('todos', []):
            if 'http:' not in t.get('text'):
                # status_str += f"TOOL STATUS : Now, the {t.get('agent')} is starting the task: {t.get('text')}.\n"
                func_res.append(f"TOOL STATUS : Now, the {t.get('agent')} is starting the task: {t.get('text')}.")

            for st in t.get('subTodos',[])[:1]:
                func_res.append(f"TOOL STATUS : The {t.get('agent')} is executing the task: {st.get('text')}.")

        asyncio.create_task(self.easl_todo_executor(question,todo_obj))
        
        func_res.append(f"TOOL STATUS :All tasks are complete. The rest task are forwarded to EASL Guideline Agent.")

        func_step.append(
                types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response={
                    "result" : "EASL Task created by Task Manager Agent.",
                    "tool_status":func_res
                }
            )
            )

        return func_step

    async def navigate_answer(self,fc,query):
        func_res = []
        # func_res.append(
        #     self.create_func_response(fc, f"Sending query to RAG Agent and Data Analyst Agent.Query : '{query}'")
        # )

        relevant_object = faiss_rag.rag_object(query=query)


        context = json.dumps(relevant_object, separators=(",", ":"))
        # context = await rag_from_json(query, top_k=3)
        # func_res.append(
        #     self.create_func_response(fc, f"Clinical agent forming answer on query '{query}'")
        # )
        first_object = relevant_object[0].get("id")
        print("Focus first :", first_object)
        await canvas_ops.focus_item(first_object)

        
        object_id = await side_agent.resolve_object_id(query, context)

        print("OBJECT ID :",object_id)


        answer = await chat_model.get_answer(query=query, context=context)

        func_res.append(
            self.create_func_response(fc, f"{answer}")
        )
        return func_res

    async def create_todo_canvas(self, todo_obj):
        todo_id = todo_obj.get("id")

        for t in todo_obj.get("todoData", {}).get('todos', []):
            t_id = t.get('id')
            await canvas_ops.update_todo(
                {"id": todo_id, "task_id": t_id, "index": "", "status": "executing"}
            )


            # await asyncio.sleep(random.randint(0.5, 1))

            for i, st in enumerate(t.get('subTodos', [])):
                await canvas_ops.update_todo(
                    {"id": todo_id, "task_id": t_id, "index": f"{i}", "status": "finished"}
                )
                # await asyncio.sleep(random.randint(0.5, 1))

            await canvas_ops.update_todo(
                {"id": todo_id, "task_id": t_id, "index": "", "status": "finished"}
            )


            # await asyncio.sleep(random.randint(0.5, 1))



        todo_data = todo_obj.get("todoData", {})

        data = await side_agent.generate_response(todo_data)

        agent_res = {
            'content': data.get('answer', ''),
            'zone': "raw-ehr-data-zone"
        }
        if todo_data.get('title'):
            agent_res['title'] = todo_data.get('title', '').lower().replace("to do", "Result").capitalize()
        
        await canvas_ops.create_result(agent_res)

        print("  ✅ Analysis completed")

    async def get_schedule(self, fc):
        with open("output/schedule.json", "r", encoding="utf-8") as f:
            schedule_payload = json.load(f)

        response_sched = await canvas_ops.create_schedule(schedule_payload)
        print("Schedule id :", response_sched.get('id'))
        await canvas_ops.focus_item(response_sched.get('id'))
        return [
            self.create_func_response(fc, "Schedule is created in Canvas.")
        ]

    async def get_notification(self, fc):
        payload = {
            "message" : "Notofication sent to GP and Rheumatologist"
        }

        await canvas_ops.create_notification(payload)
        return [
            self.create_func_response(fc, "Notification is sent.")
        ]

    async def _handle_todo_exec(self,fc,  query):
        """Handle todo execution in the background and provide narration."""
        func_res = []
        function_list = []
        try:

            todo_obj = await side_agent.generate_todo(query=query)
            asyncio.create_task(self.create_todo_canvas(todo_obj))

            for t in todo_obj.get("todoData", {}).get('todos', []):
                if 'http:' not in t.get('text'):
                    func_res.append(f"TOOL STATUS : Now, the {t.get('agent')} is starting the task: {t.get('text')}.")

                for st in t.get('subTodos',[])[:1]:
                    func_res.append(f"TOOL STATUS : The {t.get('agent')} is executing the task: {st.get('text')}.")


            func_res.append(f"All tasks are complete. I will now consolidate the results.")

            function_list.append(
                types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response={
                    "result" : "Task created by Task Manager Agent.",
                    "tool_status":func_res
                }
            )
            )
            
            


        except Exception as e:
            print(f"❌ Error in _handle_todo_exec: {e}")
            await self.session.send(input=f"I encountered an error while processing the task: {e}")
        return function_list
        

    async def todo_exec(self, fc, query):
        """Starts the todo execution task in the background and returns an initial response."""
        func_res = await self._handle_todo_exec(fc, query)
        
        # Return an immediate response to unblock the model
        return func_res

    async def handle_tool_call(self, tool_call):
        """Handle tool calls from Gemini according to official documentation"""
        try:
            
            # Track function calls
            self.function_call_count += 1
            self.last_function_call_time = datetime.datetime.now()
            
            print(f"🔧 Function Call #{self.function_call_count}")
            
            # Process each function call in the tool call
            function_responses = []
            for fc in tool_call.function_calls:
                function_name = fc.name
                arguments = fc.args
                
                print(f"  📋 {function_name}: {json.dumps(arguments, indent=2)[:100]}...")
                #############
                ### NEW MODE
                #############

                query = arguments.get('query')
                lower_q = query.lower()

                tool_res = side_agent.parse_tool(query)

                if 'easl' in lower_q or 'guideline' in lower_q:
                    print("EASL TOOL")

                    func_tool = await self.easl_todo(fc, query)
                    function_responses += func_tool
                    
                elif tool_res.get('tool') == "get_easl_answer":
                    print("EASL TOOL")

                    func_tool = await self.easl_todo(fc, query)
                    function_responses += func_tool
                

                elif tool_res.get('tool') == "generate_task":
                    print("TASK TOOL")
                    
                    func_tool = await self.todo_exec(fc, query)
                    function_responses += func_tool

                    # No function_responses are returned from todo_exec now

                elif tool_res.get('tool') == "create_schedule":
                    print("SCHEDULE TOOL")
                    func_tool = await self.get_schedule(fc)
                    function_responses += func_tool

                elif tool_res.get('tool') == "send_notification":
                    print("NOTIFICATION TOOL")
                    func_tool = await self.get_notification(fc)
                    function_responses += func_tool

                else:
                    print("GENERAL")

                    func_tool = await self.navigate_answer(fc, query)
                    function_responses += func_tool

                #############
                #############

            
            # Send tool response back to Gemini
            print("ALL FUNC RES :", function_responses)
            await self.session.send_tool_response(function_responses=function_responses)
            print("  ✅ Response sent")
            
        except Exception as e:
            print(f"❌ Function call error: {e}")
            


    def find_input_device(self, substr: str) -> int:
        """Find input device by substring"""
        s = substr.lower()
        for i in range(pya.get_device_count()):
            info = pya.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0 and s in info['name'].lower():
                return i
        return None

    def find_output_device(self, substr: str) -> int:
        """Find output device by substring"""
        s = substr.lower()
        for i in range(pya.get_device_count()):
            info = pya.get_device_info_by_index(i)
            if info['maxOutputChannels'] > 0 and s in info['name'].lower():
                return i
        return None

    def safe_read_status(self):
        for _ in range(5):  # Try up to 5 times
            try:
                with open("agent_status.json", "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                # File is being written -> wait a tiny bit and retry
                time.sleep(0.05)
            except FileNotFoundError:
                return {"mute": False}
        return {"mute": False}

    async def listen_audio(self):
        """Listen to CABLE Output (Google Meet audio) and send to Gemini"""
        print("🎤 Starting audio capture...")
        
        # Find CABLE Output device
        input_device_index = self.find_input_device("CABLE Output")
        if input_device_index is None:
            print("❌ CABLE Output device not found!")
            return
        
        input_info = pya.get_device_info_by_index(input_device_index)
        print(f"🎤 Using: {input_info['name']}")
        
        # Open audio stream
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=CHUNK_SIZE,
        )
        
        print("🎤 Audio ready!")
        
        # Read audio chunks and send to Gemini
        while True:
            try:
                data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
                await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
            except Exception as e:
                print(f"❌ Error reading audio: {e}")
                break

    async def receive_audio(self):
        """Receive audio responses from Gemini"""
        print("🔊 Starting response processing...")
        
        while True:
            agent_status = self.safe_read_status()
            # with open("agent_status.json", "r", encoding="utf-8") as f:
            #     agent_status = json.load(f)
            if not agent_status.get('mute'):
                try:
                    turn = self.session.receive()
                    async for response in turn:
                        # Handle audio data
                        if data := response.data:
                            self.audio_in_queue.put_nowait(data)

                        
                        # Method 1: Check tool_call attribute
                        if hasattr(response, 'tool_call'):
                            if response.tool_call:
                                print("🔧 TOOL CALL DETECTED!")
                                await self.handle_tool_call(response.tool_call)
    
                    # Clear audio queue on turn completion to prevent overlap
                    while not self.audio_in_queue.empty():
                        self.audio_in_queue.get_nowait()
                        
                except Exception as e:
                    print(f"❌ Error receiving audio: {e}")
                    break

    async def play_audio(self):
        """Play audio responses to CABLE Input (Google Meet will hear this)"""
        print("🔊 Setting up audio output...")
        
        # Find CABLE Input device
        output_device_index = self.find_output_device("Voicemeeter Input")
        if output_device_index is None:
            print("❌ Output device not found!")
            return
        
        output_info = pya.get_device_info_by_index(output_device_index)
        print(f"🔊 Using: {output_info['name']}")
        
        # Open output stream
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            output_device_index=output_device_index,
        )
        
        print("🔊 Audio output ready!")
        
        # Play audio from queue with proper buffering
        while True:
            try:
                bytestream = await self.audio_in_queue.get()
                await asyncio.to_thread(stream.write, bytestream)
                # Add small delay to ensure proper audio streaming
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"❌ Error playing audio: {e}")
                break

    async def send_audio_to_gemini(self):
        """Send audio data to Gemini"""
        while True:
            try:
                audio_data = await self.out_queue.get()
                await self.session.send(input=audio_data)
            except Exception as e:
                print(f"❌ Error sending audio: {e}")
                break

    async def run(self):
        """Main function to run the audio-only Gemini session with CABLE devices"""
        print("🎵 Gemini Live API - Audio Only with CABLE Devices")
        print("=" * 60)
        print("🤖 LIVE MODE: Gemini AI is ENABLED")
        print("🎤 Capturing audio from Google Meet (CABLE Output)")
        print("🔊 Playing Gemini responses to Google Meet (CABLE Input)")
        print("=" * 60)
        print("📝 Instructions:")
        print("1. Start this script first")
        print("2. Then start visit_meet_with_audio.py in another terminal")
        print("3. Configure Google Meet audio settings:")
        print("   - Microphone: CABLE Output (VB-Audio Virtual Cable)")
        print("   - Speaker: CABLE Input (VB-Audio Virtual Cable)")
        print("4. Speak in the meeting - Gemini will respond with audio to the meeting")
        print("5. Press Ctrl+C to stop")
        # print("=" * 60)
        agent_status = {
            "mute" : False
        }
        with open("agent_status.json", "w", encoding="utf-8") as f:
            json.dump(agent_status, f,indent=4)
        try:

            async with (
                self.client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session




                # Create queues
                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=10)
                
                print("🔗 Connected to Gemini Live API with system prompt")
                
                # Start all tasks
                tg.create_task(self.send_audio_to_gemini())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())
                
                # Keep running until interrupted
                try:
                    await asyncio.Event().wait()
                except KeyboardInterrupt:
                    print("\n🛑 Shutting down...")
                    raise asyncio.CancelledError("User requested exit")
                
        except asyncio.CancelledError:
            print("✅ Session ended")
        except Exception as e:
            print(f"❌ Error: {e}")
            traceback.print_exc()
        finally:
            # Clean up audio stream
            if self.audio_stream:
                self.audio_stream.close()
            print("🧹 Cleanup completed")

def main():
    """Main entry point"""
    # Suppress all warnings from the application
    warnings.filterwarnings("ignore")
    
    print("🎵 Gemini Live API - Audio Only with CABLE Devices")
    print("=" * 50)
    
    # Check for API key
    if not os.getenv('GOOGLE_API_KEY'):
        print("❌ GOOGLE_API_KEY environment variable not set!")
        print("Please set your Google API key:")
        print("set GOOGLE_API_KEY=your_api_key_here")
        return
    
    def sigterm_handler(_signo, _stack_frame):
        # Raise KeyboardInterrupt to trigger the existing graceful shutdown logic
        print("\n🛑 SIGTERM received, shutting down gracefully...")
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, sigterm_handler)
    except (ValueError, AttributeError) as e:
        # signal handling might not be available in all environments (e.g., non-main threads on Windows)
        print(f"⚠️ Could not set SIGTERM handler: {e}. Graceful shutdown via terminate signal may not work.")


    gemini = AudioOnlyGeminiCable()
    asyncio.run(gemini.run())

# if __name__ == "__main__":
#     main()
main()