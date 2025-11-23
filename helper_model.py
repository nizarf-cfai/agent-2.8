import os
import google.generativeai as genai
import requests
import config
import httpx
import json
from dotenv import load_dotenv
load_dotenv()




BASE_URL = os.getenv("CANVAS_URL", "https://board-v24problem.vercel.app")

print("#### helper_model.py CANVAS_URL : ",BASE_URL)


genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL = "gemini-2.0-flash-001"



with open("system_prompts/clinical_agent.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

with open("system_prompts/context_agent.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT_CONTEXT_GEN = f.read()

with open("system_prompts/question_gen.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT_Q_GEN = f.read()

async def load_ehr():
    print("Start load_ehr")
    url = BASE_URL + "/api/board-items"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        data = response.json()

        return data
    # response = requests.get(url)
    # print("load ehr",response.status_code)
    # data = response.json()

    # return data

async def generate_response(todo_obj):
    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT,
    )
    print(f"Running helper model")
    ehr_data = await load_ehr()
    prompt = f"""Please execute this todo : 
        {todo_obj}


        This is patient encounter data : {ehr_data}"""

    resp = model.generate_content(prompt)
    with open(f"{config.output_dir}/generate_response.md", "w", encoding="utf-8") as f:
        f.write(resp.text)

    print("Agent Result :", resp.text[:200])
    return {
        "answer": resp.text.replace("```markdown", " ").replace("```", "")
        }

async def generate_context(question):
    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT_CONTEXT_GEN,
    )
    print(f"Running Context Generation model")
    ehr_data = await load_ehr()
    prompt = f"""Please generate context for this : 
        Question : {question}


        This is raw data : {ehr_data}"""

    resp = model.generate_content(prompt)
    with open(f"{config.output_dir}/generate_context.md", "w", encoding="utf-8") as f:
        f.write(resp.text)
    return resp.text.replace("```markdown", " ").replace("```", "")
        

async def generate_question(question):
    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT_Q_GEN,
    )
    print(f"Running Context Generation model")
    ehr_data = await load_ehr()
    prompt = f"""Please generate proper question : 
        Question : {question}


        This is raw data : {ehr_data}"""

    resp = model.generate_content(prompt)
    with open(f"{config.output_dir}/generate_question.md", "w", encoding="utf-8") as f:
        f.write(resp.text)
    return resp.text.replace("```markdown", " ").replace("```", "")