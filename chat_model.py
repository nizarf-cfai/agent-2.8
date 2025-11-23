import os
import google.generativeai as genai
import requests
import config
from google.genai.types import GenerateContentConfig
import json
from chroma_db.chroma_script import rag_from_json
import asyncio
import time
import side_agent
import canvas_ops
from dotenv import load_dotenv
load_dotenv()




genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL = "gemini-2.5-flash-lite"


with open("system_prompts/chat_model_system.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

model = genai.GenerativeModel(
    MODEL,
    system_instruction=SYSTEM_PROMPT,
)


async def get_answer(query :str, conversation_text: str='', context: str=''):
    if not context:
        context = await rag_from_json(query, top_k=3)
    prompt = f"""
    Answer below user query using available data.
    User query : {query}

    Chat History : 
    {conversation_text}

    Context : 
    {context}
    """

    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT
    )

    response = model.generate_content(prompt)

    return response.text.strip()

async def chat_agent(chat_history: list[dict]) -> str:
    """
    Chat Agent:
    Takes a list of messages (chat history) and returns a natural language response.
    History format:
    [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        ...
    ]
    """

    # Convert chat history into model-friendly input
    conversation = []
    if len(chat_history) > 1:
        for msg in chat_history[:-1]:
            conversation.append(f"{msg['role'].upper()}: {msg['content']}")

    conversation_text = "\n".join(conversation)
    
    query = chat_history[-1].get('content')
    context = await rag_from_json(query, top_k=3)

    # Tools check
    print("Tools check") 
    tool_res = side_agent.parse_tool(query)
    print("Tools use :", tool_res)

    lower_q = query.lower()
    if 'easl' in lower_q or 'guideline' in lower_q:
        await side_agent.trigger_easl(query)
        return "Question forwarded to EASL Interface. You will recieved the answer soon."
    
    
    if tool_res.get('tool') == "navigate_canvas":
        object_id = await side_agent.resolve_object_id(query, context)
        print("OBJECT ID :",object_id)
        
    elif tool_res.get('tool') == "get_easl_answer":
        await side_agent.trigger_easl(query)
        return "Question forwarded to EASL Interface. You will recieved the answer soon."
    

    elif tool_res.get('tool') == "generate_task":
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(side_agent.generate_task_workflow(query))
        else:
            loop.create_task(side_agent.generate_task_workflow(query))

        return "Task generated. Agent will execute in background."
    elif tool_res.get('tool') == "create_schedule":
        print("SCHEDULE TOOL")
        res = await get_schedule()
        conversation_text += str(res)
    elif tool_res.get('tool') == "send_notification":
        print("NOTIFICATION TOOL")
        res = await get_notification()
        conversation_text += str(res)

    else:
        object_id = await side_agent.resolve_object_id(query, context)
        print("OBJECT ID :",object_id)


    prompt = f"""
    Answer below user query using available data.
    User query : {query}

    Chat History : 
    {conversation_text}

    Context : 
    {context}
    """

    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT
    )

    response = model.generate_content(prompt)

    return response.text.strip()

async def get_schedule():
    with open("output/schedule.json", "r", encoding="utf-8") as f:
        schedule_payload = json.load(f)

    response_sched = await canvas_ops.create_schedule(schedule_payload)
    print("Schedule id :", response_sched.get('id'))
    await canvas_ops.focus_item(response_sched.get('id'))
    return schedule_payload

async def get_notification():
    payload = {
        "message" : "Notification sent to GP and Rheumatologist"
    }

    await canvas_ops.create_notification(payload)
    return "Notification sent"


history = [
        {"role": "user", "content": "Tell me about Sarah Miller summary."},
        # {"role": "user", "content": "Show me medication timeline"},
        # {"role": "user", "content": "Create task to pull Sarah Miller Radiology data."},
        # {"role": "user", "content": "What is the DILI diagnosis according EASL guideline for Sarah Miller?"},
    ]
# start_time = time.time()

# result = asyncio.run(chat_agent(history))

# end_time = time.time()
# execution_time = end_time - start_time


# print("Result :")
# print(result)
# print(f"Execution time: {execution_time:.4f} seconds")

# # ✅ Keep process alive so background tasks can run
# try:
#     asyncio.get_event_loop().run_forever()
# except KeyboardInterrupt:
#     pass
