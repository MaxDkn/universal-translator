import asyncio
from fastapi import FastAPI, Request, Form, Query, BackgroundTasks
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from script import GladiaAudioManager, log_capture_string, get_device_info
from config import config
import logging

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 50

agent_to_phone = None
is_running = False
background_task = None

agent_device = "CM477"
phone_device = "KT AUDIO"
agent_language = "en"
phone_language = 'fr'
silence_timeout = 30
prebuffer_seconds = 5.5

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

templates = Jinja2Templates(directory="templates")

async def run_gladia_manager():
    """Fonction qui fait tourner le GladiaAudioManager en arrière-plan"""
    global agent_to_phone, is_running
    
    try:
        agent_device_info = get_device_info(config.get("devices", "agent"))
        phone_device_info = get_device_info(config.get("devices", "phone"))
        
        agent_to_phone = GladiaAudioManager(
            gladia_key=gladia_key,
            input_device=agent_device_info,
            output_device=phone_device_info,
            input_language=config.get("languages", "agent"),
            output_language=phone_language,
            silence_timeout=silence_timeout,
            prebuffer_seconds=prebuffer_seconds,
        ).start_audio_capture().start_vad_monitoring()
        
        logger.info(f"TTS enabled - Output device: {phone_device_info.name} | Input device: {agent_device_info.name}")
        logger.info('Starting transcription...\n')
        
        # Boucle infinie tant que is_running est True
        while is_running:
            await asyncio.sleep(0.1)
            
    except Exception as e:
        logger.error(f"Erreur dans run_gladia_manager: {e}")
    finally:
        if agent_to_phone:
            agent_to_phone.cleanup()
            logger.info("Gladia manager stopped and cleaned up")

def start_gladia():
    """Démarre le GladiaAudioManager"""
    global background_task, is_running
    
    if not is_running:
        is_running = True
        background_task = asyncio.create_task(run_gladia_manager())
        logger.info("GladiaAudioManager started")
    
def stop_gladia():
    """Arrête le GladiaAudioManager"""
    global background_task, is_running, agent_to_phone
    
    if is_running:
        is_running = False
        if agent_to_phone:
            agent_to_phone.cleanup()
            agent_to_phone = None
        if background_task:
            background_task.cancel()
            background_task = None
        logger.info("GladiaAudioManager stopped")

@app.get('/LaunchGladia')
async def launch_gladia():
    """Endpoint pour lancer manuellement (gardé pour compatibilité)"""
    start_gladia()
    return {"status": "Gladia launched"}

@app.get('/logs')
async def logs():
    global log_capture_string
    return log_capture_string.getvalue()

@app.get("/")
async def welcome(request: Request):
    return templates.TemplateResponse("welcome.html", {"request": request})

def get_last_n_logs(n_lines: int = MAX_LOG_LINES) -> str:
    """Récupère les n dernières lignes de logs"""
    global log_capture_string
    full_log = log_capture_string.getvalue()
    
    if not full_log.strip():
        return ""
    
    lines = full_log.strip().split('\n')
    
    last_lines = lines[-n_lines:] if len(lines) > n_lines else lines
    return '\n'.join(last_lines)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    global log_capture_string
    
    initial_logs = get_last_n_logs()
    if initial_logs:
        await websocket.send_text(initial_logs)
    
    last_log_hash = hash(initial_logs)
    
    try:
        while True:
            current_logs = get_last_n_logs()
            current_hash = hash(current_logs)
            
            if current_hash != last_log_hash:
                await websocket.send_text(current_logs)
                last_log_hash = current_hash
                
            await asyncio.sleep(0.1)
            
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()

@app.get("/settings")
async def settings_get(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "agent_device": config.get('device', 'agent'),
        "phone_device": config.get('device', 'phone'),
        "current_language": config.get('languages', 'agent'),
        "devices": ["CM477", "KT AUDIO"],
        "languages": ["fr", "en", "de", "es"]
    })

@app.get("/debug_logs")
async def debug_logs():
    """Route de debug pour vérifier l'état des logs"""
    global log_capture_string
    
    debug_info = {
        "log_capture_string_exists": log_capture_string is not None,
        "log_capture_string_type": type(log_capture_string).__name__,
        "log_content_length": len(log_capture_string.getvalue()) if log_capture_string else 0,
        "log_content_preview": log_capture_string.getvalue()[:200] + "..." if log_capture_string and len(log_capture_string.getvalue()) > 200 else log_capture_string.getvalue() if log_capture_string else "No content",
        "logger_handlers": [str(handler) for handler in logger.handlers] if 'logger' in globals() else "No logger found"
    }
    
    return debug_info

@app.post("/settings")
async def settings_post(
    request: Request,
    language: str = Form(...),
    agent_device: str = Form(...),
    output_device: str = Form(...)
):
    from config import config

    config.set(language, "languages", "agent")
    config.set(agent_device, "devices", "agent")
    config.set(output_device, "devices", "phone")
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/launch")
async def launch(request: Request):
    """Page de lancement - démarre automatiquement Gladia"""
    global is_running
    
    # Démarre automatiquement Gladia quand on accède à la page
    if not is_running:
        start_gladia()
    
    return templates.TemplateResponse("launch.html", {
        "request": request,
        "state": {
            "agent_device": config.get("devices", "agent"),
            "phone_device": config.get("devices", "phone"),
            "language": config.get("languages", "agent"),
            "is_running": is_running
        }
    })

@app.get("/toggle")
async def toggle():
    """Endpoint pour arrêter Gladia et rediriger"""
    stop_gladia()
    return RedirectResponse(url="/", status_code=303)

@app.get("/stop")
async def stop():
    """Endpoint pour arrêter Gladia"""
    stop_gladia()
    return {"status": "Gladia stopped"}

@app.get("/status")
async def status():
    """Endpoint pour vérifier le statut"""
    return {
        "is_running": is_running,
        "has_manager": agent_to_phone is not None
    }

@app.get('/put_log_test')
async def create_log():
    logger.info('Test')
    logger.debug('This is a debbug liiine')
    return "done"

# Gestion de l'arrêt propre de l'application
@app.on_event("shutdown")
async def shutdown_event():
    """Arrête proprement Gladia lors de l'arrêt de l'application"""
    stop_gladia()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)