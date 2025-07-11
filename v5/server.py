from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import subprocess
import threading
import asyncio
import json
import os
import toml
from datetime import datetime
from typing import Optional, List
import weakref

app = FastAPI()
templates = Jinja2Templates(directory="templates")

CONFIG_PATH = "config.toml"
_config_lock = threading.Lock()

class ConfigManager:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.reload()

    def reload(self):
        with _config_lock:
            try:
                self._data = toml.load(self.path)
            except FileNotFoundError:
                self._data = {
                    "silence_timeout": 30,
                    "prebuffer_seconds": 5.5,
                    "devices": {
                        "agent": "KT USB Audio",
                        "phone": "CM477"
                    },
                    "languages": {
                        "agent": "de",
                        "phone": "fr"
                    }
                }
                self.save()

    def get(self, *keys):
        value = self._data
        for key in keys:
            value = value[key]
        return value

    def set(self, value, *keys):
        with _config_lock:
            d = self._data
            for key in keys[:-1]:
                d = d.setdefault(key, {})
            d[keys[-1]] = value
            self.save()

    def save(self):
        with open(self.path, "w") as f:
            toml.dump(self._data, f)

    def get_all(self):
        return self._data.copy()

    def update_config(self, new_config):
        with _config_lock:
            self._data.update(new_config)
            self.save()

    @property
    def gladia_key(self):
        return os.environ.get("GLADIA_API_KEY", "")

    @property
    def agent_device(self):
        return self.get("devices", "agent")

    @property
    def phone_device(self):
        return self.get("devices", "phone")

    @property
    def agent_language(self):
        return self.get("languages", "agent")

    @property
    def phone_language(self):
        return self.get("languages", "phone")

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast_logs(self, logs: List[str]):
        """Broadcast les derniers logs à tous les clients connectés"""
        if not self.active_connections:
            return
            
        message = {
            "type": "logs_update",
            "data": logs
        }
        
        connections_copy = self.active_connections.copy()
        
        for connection in connections_copy:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending to websocket: {e}")
                self.disconnect(connection)
    
    async def broadcast_status(self, status: dict):
        """Broadcast le statut à tous les clients connectés"""
        if not self.active_connections:
            return
            
        message = {
            "type": "status_update", 
            "data": status
        }
        
        connections_copy = self.active_connections.copy()
        
        for connection in connections_copy:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error sending to websocket: {e}")
                self.disconnect(connection)

class ScriptManager:
    def __init__(self, websocket_manager: WebSocketManager, config_manager: ConfigManager):
        self.process: Optional[subprocess.Popen] = None
        self.logs = []
        self.is_running = False
        self.websocket_manager = websocket_manager
        self.config_manager = config_manager
        
    async def start_script(self):
        if self.is_running:
            return {"status": "error", "message": "Script is already running"}
        
        try:
            # Recharger la config pour avoir les dernières valeurs
            self.config_manager.reload()
            
            # Construire la commande avec la config actuelle
            cmd = [
                "sudo", ".venv/bin/python", "script.py",
                "--agent-device", self.config_manager.agent_device,
                "--agent-language", self.config_manager.agent_language,
                "--phone-device", self.config_manager.phone_device,
                "--phone-language", self.config_manager.phone_language,
                "--gladia-key", self.config_manager.gladia_key
            ]
            
            # Démarrer le processus avec capture des sorties
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            self.is_running = True
            
            # Démarrer le thread de lecture des logs
            self.log_thread = threading.Thread(target=self._read_logs_async, daemon=True)
            self.log_thread.start()
            
            # Broadcast du nouveau statut
            status = self.get_status()
            await self.websocket_manager.broadcast_status(status)
            
            return {"status": "success", "message": "Script started", "pid": self.process.pid}
            
        except Exception as e:
            return {"status": "error", "message": f"Failed to start script: {str(e)}"}
    
    async def stop_script(self):
        if not self.is_running:
            return {"status": "error", "message": "Script is not running"}
        
        try:
            if self.process:
                # Arrêt propre
                self.process.terminate()
                try:
                    # Attendre 5 secondes pour un arrêt propre
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Forcer l'arrêt si nécessaire
                    self.process.kill()
                    self.process.wait()
                
                # Attendre que le thread de lecture des logs se termine
                if hasattr(self, 'log_thread') and self.log_thread.is_alive():
                    self.log_thread.join(timeout=2)
                
                self.process = None
                
                # Ajouter un log d'arrêt
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                stop_log = f"[{timestamp}] === SCRIPT STOPPED ==="
                self.logs.append(stop_log)
                
                # Garder seulement les 1000 derniers logs
                if len(self.logs) > 1000:
                    self.logs.pop(0)
                
                # Broadcast des derniers logs incluant le message d'arrêt
                recent_logs = self.logs[-50:]
                await self.websocket_manager.broadcast_logs(recent_logs)
                
                # Maintenant marquer comme arrêté et broadcaster le statut
                self.is_running = False
                status = self.get_status()
                await self.websocket_manager.broadcast_status(status)
                
            return {"status": "success", "message": "Script stopped"}
            
        except Exception as e:
            return {"status": "error", "message": f"Failed to stop script: {str(e)}"}
    
    def _read_logs_async(self):
        """Thread function to read logs from the subprocess"""
        if not self.process:
            return
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            for line in iter(self.process.stdout.readline, ''):
                if line:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    log_entry = f"[{timestamp}] {line.strip()}"
                    self.logs.append(log_entry)
                    
                    # Garder seulement les 1000 derniers logs
                    if len(self.logs) > 1000:
                        self.logs.pop(0)
                    
                    # Broadcast des derniers 50 logs
                    recent_logs = self.logs[-50:]
                    loop.run_until_complete(
                        self.websocket_manager.broadcast_logs(recent_logs)
                    )
        except Exception as e:
            print(f"Error in log reading thread: {e}")
        finally:
            # Juste s'assurer que les derniers logs sont envoyés
            if self.logs:
                recent_logs = self.logs[-50:]
                loop.run_until_complete(
                    self.websocket_manager.broadcast_logs(recent_logs)
                )
            loop.close()
    
    def get_status(self):
        return {
            "running": self.is_running,
            "pid": self.process.pid if self.process else None,
            "log_count": len(self.logs)
        }
    
    def get_recent_logs(self, count: int = 50):
        """Retourne les n derniers logs"""
        return self.logs[-count:] if self.logs else []

# Instances globales
config_manager = ConfigManager()
websocket_manager = WebSocketManager()
script_manager = ScriptManager(websocket_manager, config_manager)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    config_data = config_manager.get_all()
    status = script_manager.get_status()
    recent_logs = script_manager.get_recent_logs()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "config": config_data,
        "status": status,
        "initial_logs": recent_logs
    })

@app.get("/config")
async def get_config():
    return config_manager.get_all()

@app.post("/config")
async def update_config(config_data: dict):
    try:
        config_manager.update_config(config_data)
        return {"status": "success", "message": "Configuration updated"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to update config: {str(e)}"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    
    status = script_manager.get_status()
    await websocket.send_text(json.dumps({
        "type": "status_update",
        "data": status
    }))
    
    recent_logs = script_manager.get_recent_logs()
    await websocket.send_text(json.dumps({
        "type": "logs_update", 
        "data": recent_logs
    }))
    
    config_data = config_manager.get_all()
    await websocket.send_text(json.dumps({
        "type": "config_update",
        "data": config_data
    }))
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "start_script":
                result = await script_manager.start_script()
                await websocket.send_text(json.dumps({
                    "type": "command_response",
                    "data": result
                }))
                
            elif message["type"] == "stop_script":
                result = await script_manager.stop_script()
                await websocket.send_text(json.dumps({
                    "type": "command_response",
                    "data": result
                }))
                
            elif message["type"] == "update_config":
                try:
                    config_manager.update_config(message["data"])
                    new_config = config_manager.get_all()
                    await websocket_manager.broadcast_config(new_config)
                    await websocket.send_text(json.dumps({
                        "type": "command_response",
                        "data": {"status": "success", "message": "Configuration updated"}
                    }))
                except Exception as e:
                    await websocket.send_text(json.dumps({
                        "type": "command_response",
                        "data": {"status": "error", "message": f"Failed to update config: {str(e)}"}
                    }))
                
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

async def broadcast_config(self, config: dict):
    """Broadcast la config à tous les clients connectés"""
    if not self.active_connections:
        return
        
    message = {
        "type": "config_update",
        "data": config
    }
    
    connections_copy = self.active_connections.copy()
    
    for connection in connections_copy:
        try:
            await connection.send_text(json.dumps(message))
        except Exception as e:
            print(f"Error sending to websocket: {e}")
            self.disconnect(connection)

WebSocketManager.broadcast_config = broadcast_config

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)