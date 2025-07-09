from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import logging
import threading
import time

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

templates = Jinja2Templates(directory="templates")

logging.basicConfig(filename='app.log', level=logging.INFO, format='%(asctime)s - %(message)s')

def log_time():
    while True:
        logging.info("Bonjour")
        time.sleep(5)

threading.Thread(target=log_time, daemon=True).start()

def get_state(session):
    if "state" not in session:
        session["state"] = {
            "language": None,
            "agent_device": None,
            "output_device": None,
            "running": False,
        }
    return session["state"]

@app.get("/")
async def welcome(request: Request):
    return templates.TemplateResponse("welcome.html", {"request": request})

@app.get("/get_logs")
async def get_logs(lines: int = Query(default=20, description="Number of lines to return")):
    """Route to get logs with specified number of lines"""
    try:
        with open("app.log", "r") as f:
            all_lines = f.readlines()
            # Get the last 'lines' number of lines
            recent_lines = all_lines[-lines:] if len(all_lines) >= lines else all_lines
        
        logs_content = "".join(recent_lines)
        return PlainTextResponse(logs_content)
    except FileNotFoundError:
        return PlainTextResponse("No logs available.")

@app.get("/settings")
async def settings_get(request: Request):
    state = get_state(request.session)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "state": state,
        "devices": ["CM477", "KT AUDIO"],
        "languages": ["fr", "en", "de", "es"]
    })

@app.post("/settings")
async def settings_post(
    request: Request,
    language: str = Form(...),
    agent_device: str = Form(...),
    output_device: str = Form(...)
):
    state = get_state(request.session)
    state["language"] = language
    state["agent_device"] = agent_device
    state["output_device"] = output_device
    logging.info(f"Configuration updated: Language={language}, Agent={agent_device}, Output={output_device}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/launch")
async def launch(request: Request):
    state = get_state(request.session)
    return templates.TemplateResponse("launch.html", {
        "request": request,
        "state": state
    })

@app.post("/toggle")
async def toggle(request: Request):
    state = get_state(request.session)
    state["running"] = not state["running"]
    
    # Log state change
    if state["running"]:
        logging.info("Service started")
    else:
        logging.info("Service stopped")
    
    return RedirectResponse(url="/launch", status_code=303)

# Add startup log
logging.info("=== Application started ===")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)