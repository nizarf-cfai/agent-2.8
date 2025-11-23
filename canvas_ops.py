import requests
import json
import time
import aiohttp
import helper_model
import os
import config
from dotenv import load_dotenv
load_dotenv()


BASE_URL = os.getenv("CANVAS_URL", "https://board-v24problem.vercel.app")
print("#### canvas_ops.py CANVAS_URL : ",BASE_URL)

async def initiate_easl_iframe(question):
    url = BASE_URL + "/api/send-to-easl"
    payload = {
        "query": question,
        "metadata": {
            "source": "voice"
        }
    }

    headers = {
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)

    return response.json()

async def get_agent_question(question):
    context_str = await helper_model.generate_question(question)


    return context_str

async def get_agent_context(question):
    context_str = await helper_model.generate_context(question)


    return context_str

async def get_agent_answer(todo):
    data = await helper_model.generate_response(todo)

    result = {}
    result['content'] = data.get('answer', '')
    if todo.get('title'):
        result['title'] = todo.get('title', '').lower().replace("to do", "Result").capitalize()

    return result



async def focus_item(item_id):

    url = BASE_URL + "/api/focus"
    payload = {
        "objectId": item_id,
        "focusOptions": {
            "zoom": 0.8,
            "highlight": True
        }
    }
    print("Focus URL:",url)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/focus_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            data = await response.json()
            with open(f"{config.output_dir}/focus_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data

async def create_todo(payload_body):

    url = BASE_URL + "/api/enhanced-todo"

    payload = payload_body

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/todo_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            data = await response.json()
            with open(f"{config.output_dir}/todo_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data

async def update_todo(payload):
    url = BASE_URL + "/api/update-todo-status"

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/upadate_todo_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
            data = await response.json()
            # print("Update todo :", data)
            with open(f"{config.output_dir}/upadate_todo_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data

async def create_lab(payload):
   
    url = BASE_URL + "/api/lab-results"
    

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/lab_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            data = await response.json()

            with open(f"{config.output_dir}/lab_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data

async def create_result(agent_result):
    url = BASE_URL + "/api/agents"
    
    payload = agent_result

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/agentres_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            data = await response.json()

            with open(f"{config.output_dir}/agentres_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data
        
def create_diagnosis(payload):
    print("Start create object")
    url = BASE_URL + "/api/dili-diagnostic"
    payload['zone'] = "dili-analysis-zone"
    with open(f"{config.output_dir}/diagnosis_create_payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)
    response = requests.post(url, json=payload)
    print(response.status_code)
    with open(f"{config.output_dir}/diagnosis_create_response.json", "w", encoding="utf-8") as f:
        json.dump(response.json(), f, ensure_ascii=False, indent=4)    
    # async with aiohttp.ClientSession() as session:
    #     async with session.post(url, json=payload) as response:
    #         with open(f"{config.output_dir}/diagnosis_create_payload.json", "w", encoding="utf-8") as f:
    #             json.dump(payload, f, ensure_ascii=False, indent=4)

    #         data = await response.json()
    #         print("Object created")
    #         with open(f"{config.output_dir}/diagnosis_create_response.json", "w", encoding="utf-8") as f:
    #             json.dump(data, f, ensure_ascii=False, indent=4)
    #         return data
        
async def create_report(payload):
    url = BASE_URL + "/api/patient-report"
    payload['zone'] = "dili-analysis-zone"

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/report_create_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            data = await response.json()

            with open(f"{config.output_dir}/report_create_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data
        
async def create_schedule(payload):
    url = BASE_URL + "/api/schedule"

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/schedule_create_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            data = await response.json()

            with open(f"{config.output_dir}/schedule_create_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data
        
async def create_notification(payload):
    url = BASE_URL + "/api/notification"

    # response = requests.post(url, json=payload)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            with open(f"{config.output_dir}/notification_create_payload.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            data = await response.json()

            with open(f"{config.output_dir}/notification_create_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return data