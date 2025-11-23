import asyncio
import os
import sys
import traceback
import pyaudio
import json
import datetime
import canvas_ops
from google import genai
import socket
import threading
import warnings
import random
import time

from chroma_db.chroma_script import rag_from_json, get_easl_answer_async
from dotenv import load_dotenv
load_dotenv()

# --- Warnings / timeouts ------------------------------------------------------
warnings.filterwarnings("ignore", message=".*non-text parts.*")
warnings.filterwarnings("ignore", message=".*inline_data.*")
warnings.filterwarnings("ignore", message=".*concatenated text result.*")
warnings.filterwarnings("ignore", category=UserWarning)
socket.setdefaulttimeout(300)  # 5 minutes

# --- Audio config -------------------------------------------------------------
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

# --- Gemini config ------------------------------------------------------------
MODEL = "models/gemini-2.0-flash-live-001"

with open("system_prompt.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

with open("system_function.json", "r", encoding="utf-8") as f:
    FUNCTION_DECLARATIONS = json.load(f)

CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": SYSTEM_PROMPT,
    "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
    "speech_config": {
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Charon"}},
        "language_code": "en-GB",
    },
}

# --- PyAudio ------------------------------------------------------------------
pya = pyaudio.PyAudio()


class AudioOnlyGeminiCable:
    def __init__(self):
        self.audio_in_queue: asyncio.Queue | None = None
        self.out_queue: asyncio.Queue | None = None
        self.session = None
        self.audio_stream = None
        self.function_call_count = 0
        self.last_function_call_time: datetime.datetime | None = None

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            print("❌ GOOGLE_API_KEY environment variable not set!")
            sys.exit(1)

        self.client = genai.Client(
            http_options={"api_version": "v1beta", "timeout": 300},
            api_key=api_key,
        )
        print(f"🔧 Gemini client initialized: {hasattr(self.client, 'aio')}")

    # ---------- Utility -------------------------------------------------------
    def safe_read_status(self):
        """Read agent_status.json safely to avoid JSONDecodeError during writes."""
        for _ in range(5):
            try:
                with open("agent_status.json", "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                time.sleep(0.05)
            except FileNotFoundError:
                return {"mute": False}
        return {"mute": False}

    def find_input_device(self, substr: str) -> int | None:
        s = substr.lower()
        for i in range(pya.get_device_count()):
            info = pya.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and s in info.get("name", "").lower():
                return i
        return None

    def find_output_device(self, substr: str) -> int | None:
        s = substr.lower()
        for i in range(pya.get_device_count()):
            info = pya.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0 and s in info.get("name", "").lower():
                return i
        return None

    # ---------- Tool Handling -------------------------------------------------
    async def handle_tool_call(self, tool_call):
        """
        Handle tool calls exactly once per call, return compliant FunctionResponse,
        and (Option A) trigger UI/side-effects automatically in background tasks.
        """
        try:
            from google.genai import types

            self.function_call_count += 1
            self.last_function_call_time = datetime.datetime.now()
            print(f"🔧 Function Call #{self.function_call_count}")

            function_responses = []

            for fc in tool_call.function_calls:
                name = fc.name
                args = fc.args or {}
                print(f"  📋 {name}: {json.dumps(args, indent=2)[:200]}...")

                # Compute the value (no "result" wrapper yet).
                if name == "get_canvas_objects":
                    query = args.get("query", "")
                    canvas_result = await self.get_canvas_objects(query)
                    print("  📦 Canvas RAG result (trunc):", str(canvas_result)[:200])

                    value = {
                        "status": "Canvas objects retrieved",
                        "action": "Retrieved canvas items; model may navigate next",
                        "query": query,
                        "canvas_data": canvas_result,
                        "message": (
                            f"Retrieved relevant canvas objects for query '{query}'. "
                            "Model can now choose to navigate to a specific object."
                        ),
                    }

                    # (Option A) Any UI side-effects for queries? Keep **lightweight** here.
                    # We DO NOT re-run heavy RAG or navigate again to avoid duplication.
                    # Navigation is better as a separate tool call (navigate_canvas).

                    # Still allow background ops if needed later:
                    # asyncio.create_task(self.save_function_call({"query": query}))

                else:
                    # Other tools → synthesize a standard value object
                    value = self.get_function_value_payload(args)

                    # (Option A) Auto-UI actions immediately in background:
                    asyncio.create_task(self.save_function_call(args))

                # Wrap according to Live API: response={"result": <value>}
                function_responses.append(
                    types.FunctionResponse(id=fc.id, name=name, response={"result": value})
                )

            # Send **one** tool response covering all function calls in this turn
            await self.session.send_tool_response(function_responses=function_responses)
            print("  ✅ Tool response sent")

            # Let model continue reasoning naturally (no forced reset)
            await asyncio.sleep(0.2)

        except Exception as e:
            print(f"❌ Function call error: {e}")
            traceback.print_exc()
            try:
                from google.genai import types

                error_response = types.FunctionResponse(
                    id="error",
                    name="error",
                    response={"result": {"error": f"Function call failed: {str(e)}"}},
                )
                await self.session.send_tool_response(function_responses=[error_response])
                print("  🔄 Error recovery response sent")
            except Exception as ee:
                print(f"❌ Error recovery failed: {ee}")

    def get_function_value_payload(self, arguments: dict) -> dict:
        """
        Return a JSON-serializable value object for the FunctionResponse.result.
        This should match the logical output of each function.
        """
        if "objectId" in arguments:
            object_id = arguments.get("objectId", "")
            return {
                "status": "Canvas navigation completed",
                "action": "Moved viewport to target object",
                "objectId": object_id,
                "message": (
                    "Navigation performed to the requested object. "
                    "Viewport focused and highlighted."
                ),
            }

        elif "parameter" in arguments:
            return {
                "status": "Lab result generated",
                "action": "Created lab result for medical parameter",
                "parameter": arguments.get("parameter", "Unknown"),
                "value": arguments.get("value", "N/A"),
                "unit": arguments.get("unit", ""),
                "status_level": arguments.get("status", "Normal"),
            }

        elif "query" in arguments and len(arguments) == 1:
            query = arguments.get("query", "")
            return {
                "status": "Query acknowledged",
                "action": "Model will use retrieved information",
                "query": query,
            }

        elif "question" in arguments:
            # EASL flow (value object says accepted; side-effects are backgrounded)
            question = arguments.get("question", "")
            return {
                "status": "EASL workflow started",
                "action": "Query dispatched to EASL agent",
                "question": question,
            }

        # Generic planning task
        title = arguments.get("title", "New Task")
        content = arguments.get("content", "Task created")
        items = arguments.get("items", [])
        return {
            "status": "Task created successfully",
            "action": "Created structured task",
            "title": title,
            "content": content,
            "items": items,
        }

    async def get_canvas_objects(self, query: str):
        """Get canvas objects using RAG from JSON (single call per tool)."""
        try:
            print(f"🔍 Getting canvas objects for query: {query}")
            result = await rag_from_json(query, top_k=3)
            # result is expected to be a string or JSON string; keep as-is
            print(f"🔍 Canvas objects retrieved (trunc): {str(result)[:200]}")
            return result if result else "No relevant canvas objects found."
        except Exception as e:
            print(f"❌ Error getting canvas objects: {e}")
            return f"Error retrieving canvas objects: {str(e)}"

    # ---------- Side-effects (Option A auto-actions) --------------------------
    def start_background_agent_processing(self, action_data, todo_obj):
        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handle_agent_processing(action_data, todo_obj))
            except Exception as e:
                print(f"❌ Background agent error: {e}")
            finally:
                loop.close()

        threading.Thread(target=runner, daemon=True).start()
        print("  🔄 Background processing started")

    async def _handle_agent_processing(self, action_data, todo_obj):
        try:
            agent_res = await canvas_ops.get_agent_answer(action_data)

            # Update todo statuses with a bit of life
            todo_id = todo_obj.get("id")
            for t in todo_obj.get("todoData", {}).get("todos", []):
                t_id = t.get("id")
                await canvas_ops.update_todo({"id": todo_id, "task_id": t_id, "index": "", "status": "executing"})
                for i, _ in enumerate(t.get("subTodos", [])):
                    await canvas_ops.update_todo({"id": todo_id, "task_id": t_id, "index": f"{i}", "status": "finished"})
                    await asyncio.sleep(random.randint(1, 3))
                await canvas_ops.update_todo({"id": todo_id, "task_id": t_id, "index": "", "status": "finished"})
                await asyncio.sleep(random.randint(2, 3))

            agent_res["zone"] = "raw-ehr-data-zone"
            await canvas_ops.create_result(agent_res)
            print("  ✅ Analysis completed")

        except Exception as e:
            print(f"❌ Background processing error: {e}")
            try:
                await self.session.send(input=f"BACKGROUND PROCESSING ERROR: {str(e)}")
            except Exception as ee:
                print(f"⚠️ Could not notify model: {ee}")

    def start_background_easl_processing(self, query, todo_obj):
        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handle_easl_iframe(query, todo_obj))
            except Exception as e:
                print(f"❌ Background EASL error: {e}")
            finally:
                loop.close()

        threading.Thread(target=runner, daemon=True).start()
        print("  🔄 Background EASL started")

    async def _handle_easl_iframe(self, query, todo_obj):
        try:
            print("Start trigger EASL iframe")
            question = await canvas_ops.get_agent_question(query)

            # Task-101 progression
            for i in range(2):
                await canvas_ops.update_todo(
                    {"id": todo_obj.get("id"), "task_id": "task-101", "index": f"{i}", "status": "finished"}
                )
                await asyncio.sleep(random.randint(1, 3))
            await canvas_ops.update_todo(
                {"id": todo_obj.get("id"), "task_id": "task-101", "index": "", "status": "finished"}
            )
            await asyncio.sleep(random.randint(1, 3))

            # Task-102 progression
            await canvas_ops.update_todo({"id": todo_obj.get("id"), "task_id": "task-102", "index": "", "status": "executing"})
            await asyncio.sleep(random.randint(1, 3))
            easl_status = await canvas_ops.initiate_easl_iframe(question)
            for i in range(2):
                await canvas_ops.update_todo(
                    {"id": todo_obj.get("id"), "task_id": "task-102", "index": f"{i}", "status": "finished"}
                )
                await asyncio.sleep(random.randint(1, 3))
            await canvas_ops.update_todo(
                {"id": todo_obj.get("id"), "task_id": "task-102", "index": "", "status": "finished"}
            )
            print("iframe status:", easl_status)

            # Focus the iframe
            await canvas_ops.focus_item("iframe-item-easl-interface")
            print("  ✅ EASL workflow completed")

        except Exception:
            traceback.print_exc()
            try:
                await self.session.send(input="BACKGROUND EASL PROCESSING ERROR")
            except Exception as ee:
                print(f"⚠️ Could not notify model: {ee}")

    async def save_function_call(self, action_data):
        """
        Auto-perform UI actions as soon as a tool is invoked (Option A).
        Keep these idempotent/lightweight to avoid race conditions.
        """
        if not action_data:
            return

        try:
            if "objectId" in action_data:
                object_id = action_data["objectId"]
                focus_res = await canvas_ops.focus_item(object_id)
                print("  🎯 Navigation completed", focus_res)

            elif "parameter" in action_data:
                lab_res = await canvas_ops.create_lab(action_data)
                await asyncio.sleep(1)
                lab_id = lab_res.get("id")
                if lab_id:
                    await canvas_ops.focus_item(lab_id)
                print("  🧪 Lab result created")

            elif "question" in action_data:
                print("  🔍 Start EASL Processing:", action_data)
                query = action_data.get("question", "")
                task_payload = {
                    "title": "EASL Guideline Query Workflow",
                    "description": "Handling query to EASL Guideline Agent in background",
                    "todos": [
                        {
                            "id": "task-101",
                            "text": "Creating question query and generating context",
                            "status": "executing",
                            "agent": "Data Analyst Agent",
                            "subTodos": [
                                {"text": f"Base question : {query}", "status": "executing"},
                                {"text": "Detailed Question generated by ContextGen Agent", "status": "executing"},
                            ],
                        },
                        {
                            "id": "task-102",
                            "text": "Send query to EASL Guideline Agent",
                            "status": "pending",
                            "agent": "Data Analyst Agent",
                            "subTodos": [
                                {"text": "Query is processing", "status": "pending"},
                                {"text": "Result is created in canvas", "status": "pending"},
                            ],
                        },
                    ],
                }
                task_res = await canvas_ops.create_todo(task_payload)
                await asyncio.sleep(2)
                self.start_background_easl_processing(query, task_res)
                print("  🔍 EASL question processed")

            else:
                # Generic planning box
                action_data["area"] = "planning-zone"
                task_res = await canvas_ops.create_todo(action_data)
                await asyncio.sleep(2)
                if task_res and task_res.get("id"):
                    await canvas_ops.focus_item(task_res["id"])
                print("  📝 Task created")
                await asyncio.sleep(1)
                self.start_background_agent_processing(action_data, task_res)

        except Exception as e:
            print(f"❌ save_function_call() error: {e}")

    # ---------- Streams -------------------------------------------------------
    async def listen_audio(self):
        """Listen to CABLE Output (Google Meet audio) and enqueue to Gemini."""
        print("🎤 Starting audio capture...")
        input_device_index = self.find_input_device("CABLE Output")
        if input_device_index is None:
            print("❌ CABLE Output device not found!")
            return

        input_info = pya.get_device_info_by_index(input_device_index)
        print(f"🎤 Using: {input_info['name']}")

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

        while True:
            try:
                data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
                await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
            except Exception as e:
                print(f"❌ Error reading audio: {e}")
                break

    async def receive_audio(self):
        """
        Receive streamed model responses and trigger tools when needed,
        following the official Live API turn loop pattern.
        """
        print("🔊 Starting response processing...")

        # Use official pattern: async iterate over turns, then over responses
        try:
            async for turn in self.session.receive():
                agent_status = self.safe_read_status()
                if agent_status.get("mute"):
                    # Still drain events to keep session healthy
                    continue

                for response in turn:
                    # 1) Audio chunks
                    if getattr(response, "data", None):
                        try:
                            self.audio_in_queue.put_nowait(response.data)
                        except asyncio.QueueFull:
                            pass

                    # ✅ SPEECH RECOGNIZED (THIS SHOWS GEMINI HEARD YOU)
                    if hasattr(response, "text") and response.text:
                        print(f"🗣️ Gemini heard you → \"{response.text.strip()}\"")

                    # TOOL CALL
                    if getattr(response, "tool_call", None):
                        print("🔧 TOOL CALL DETECTED!")
                        await self.handle_tool_call(response.tool_call)

        except Exception as e:
            print(f"❌ Error receiving audio: {e}")

    async def play_audio(self):
        """Play audio responses to 'Voicemeeter Input' so Meet hears the model."""
        print("🔊 Setting up audio output...")
        output_device_index = self.find_output_device("Voicemeeter Input")
        if output_device_index is None:
            print("❌ Output device not found!")
            return

        output_info = pya.get_device_info_by_index(output_device_index)
        print(f"🔊 Using: {output_info['name']}")

        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            output_device_index=output_device_index,
        )
        print("🔊 Audio output ready!")

        while True:
            try:
                bytestream = await self.audio_in_queue.get()
                await asyncio.to_thread(stream.write, bytestream)
                await asyncio.sleep(0.01)  # smooth playback
            except Exception as e:
                print(f"❌ Error playing audio: {e}")
                break

    async def send_audio_to_gemini(self):
        """Send microphone (Meet output) audio to Gemini."""
        while True:
            try:
                audio_data = await self.out_queue.get()
                await self.session.send(input=audio_data)
            except Exception as e:
                print(f"❌ Error sending audio: {e}")
                break

    # ---------- Runner --------------------------------------------------------
    async def run(self):
        print("🎵 Gemini Live API - Audio Only with CABLE Devices")
        print("=" * 60)
        print("🤖 LIVE MODE: Gemini AI is ENABLED")
        print("🎤 Capturing audio from Google Meet (CABLE Output)")
        print("🔊 Playing Gemini responses to Google Meet (Voicemeeter Input)")
        print("=" * 60)
        print("📝 Instructions:")
        print("1. Start this script first")
        print("2. Then start visit_meet_with_audio.py in another terminal")
        print("3. In Google Meet audio settings:")
        print("   - Microphone: CABLE Output (VB-Audio Virtual Cable)")
        print("   - Speaker:    CABLE Input / Voicemeeter (as you configured)")
        print("4. Speak in the meeting - Gemini responds with audio")
        print("5. Ctrl+C to stop")

        # initialize agent status
        with open("agent_status.json", "w", encoding="utf-8") as f:
            json.dump({"mute": False}, f, indent=4)

        try:
            async with (
                self.client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                self.audio_in_queue = asyncio.Queue(maxsize=50)
                self.out_queue = asyncio.Queue(maxsize=10)

                print("🔗 Connected to Gemini Live API with system prompt")

                tg.create_task(self.send_audio_to_gemini())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

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
            if self.audio_stream:
                self.audio_stream.close()
            print("🧹 Cleanup completed")


def main():
    warnings.filterwarnings("ignore")

    print("🎵 Gemini Live API - Audio Only with CABLE Devices")
    print("=" * 50)

    if not os.getenv("GOOGLE_API_KEY"):
        print("❌ GOOGLE_API_KEY environment variable not set!")
        print("Please set your Google API key, e.g.:")
        print("set GOOGLE_API_KEY=your_api_key_here")
        return

    gemini = AudioOnlyGeminiCable()
    asyncio.run(gemini.run())


# Start immediately when imported/executed (your current behavior)
main()
