# main.py
from fastapi import FastAPI, BackgroundTasks
import subprocess
import os, sys
import subprocess, os, datetime, psutil
from fastapi.middleware.cors import CORSMiddleware
import json
import chat_model
import side_agent


TARGET_SCRIPTS = ["visit_meet_with_audio.py", "gemini_audio_only_cable.py"]
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # <— Allow every domain
    allow_credentials=True,
    allow_methods=["*"],        # <— Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],        # <— Allow all headers
)

@app.get("/health")
def health():
    return {"status": "ok"}

def kill_existing_processes(script_name: str):
    """Find and kill any running PowerShell or Python processes executing the target script."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = " ".join(proc.info['cmdline']).lower() if proc.info['cmdline'] else ""
            if any(script_name.lower() in cmdline for script_name in TARGET_SCRIPTS):
                print(f"Terminating PID {proc.pid}: {cmdline}")
                proc.terminate()
                proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

def run_powershell_script(script_name: str, meet_url: str = None):
    if meet_url:
        command = f'start powershell -NoExit -ExecutionPolicy Bypass -Command "python {script_name} --link {meet_url}"'
    else:
        command = f'start powershell -NoExit -ExecutionPolicy Bypass -Command "python {script_name}"'

    subprocess.Popen(command, shell=True)
    print(f"Opened PowerShell window for {script_name}")


@app.post("/join-meeting")
def join_meeting(payload: dict, background_tasks: BackgroundTasks):
    meet_url = payload.get("meetUrl")
    print("Received:", payload)

    background_tasks.add_task(kill_existing_processes, "visit_meet_with_audio.py")
    background_tasks.add_task(kill_existing_processes, "gemini_audio_only_cable.py")

    background_tasks.add_task(run_powershell_script, "visit_meet_with_audio.py", meet_url)
    background_tasks.add_task(run_powershell_script, "gemini_audio_only_cable.py")

    return {"status": "joining", "meeting_url": meet_url}

@app.post("/mute")
def mute(payload: dict):
    with open("agent_status.json", "r", encoding="utf-8") as f:
        agent_status = json.load(f)
    if agent_status.get('mute'):
        agent_status['mute'] = False
    else:
        agent_status['mute'] = True
    with open("agent_status.json", "w", encoding="utf-8") as f:
        json.dump(agent_status, f,indent=4)

    print("Agent Status:", agent_status['mute'])
    return agent_status


@app.post("/send-chat")
async def run_chat_agent(payload: list[dict]):
    answer = await chat_model.chat_agent(payload)

    print("Agent Answer:", answer)
    return answer


@app.post("/generate_diagnosis")
async def gen_diagnosis(payload: dict):
    await side_agent.create_dili_diagnosis()
    return {
        "status" : "done"
    }

@app.post("/generate_report")
async def gen_report(payload: dict):
    await side_agent.create_patient_report()
    return {
        "status" : "done"
    }

@app.post("/generate_legal")
async def gen_report(payload: dict):
    await side_agent.create_legal_doc()
    return {
        "status" : "done"
    }