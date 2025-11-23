import subprocess
import os, sys
import subprocess, os, datetime, psutil


TARGET_SCRIPTS = ["visit_meet_with_audio.py", "gemini_audio_only_cable.py"]


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

kill_existing_processes("visit_meet_with_audio.py")
kill_existing_processes("gemini_audio_only_cable.py")