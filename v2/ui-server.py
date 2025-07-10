import asyncio
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

templates = Jinja2Templates(directory="templates")


def get_state(session):
    if "state" not in session:
        session["state"] = {
            "language": None,
            "agent_device": None,
            "output_device": None,
            "running": False,
        }
    return session["state"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.sleep(15)
    print('TOTO')
    yield

@app.get("/")
async def welcome(request: Request):
    return templates.TemplateResponse("welcome.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Message reçu: {data}")
            
    except WebSocketDisconnect:
        pass

@app.get("/get_logs")
async def get_logs(lines: int = Query(default=20, description="Number of lines to return")):
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
    return RedirectResponse(url="/", status_code=303)

@app.get("/launch")
async def launch(request: Request):
    state = get_state(request.session)
    return templates.TemplateResponse("launch.html", {
        "request": request,
        "state": state
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
