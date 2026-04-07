"""
APEX Dashboard — FastAPI Web Server with WebSocket real-time updates.

Run:
    uvicorn dashboard.app:app --host 0.0.0.0 --port 8080 --reload

Or via main:
    python -m dashboard.app
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex import config
from apex.bitget_client import BitgetClient
from apex.data_manager import DataManager
from apex.risk_manager import RiskManager
from apex.signal_engine import SignalEngine, ApexSignal, NoTradeSignal
from apex.signal_formatter import signal_to_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self.client: Optional[BitgetClient] = None
        self.data_manager: Optional[DataManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.signal_engine: Optional[SignalEngine] = None

        self.last_scan_time: Optional[float] = None
        self.last_signals: List[dict] = []
        self.scan_in_progress: bool = False
        self.pair_status: Dict[str, dict] = {}   # symbol → latest status

        self.connected_clients: Set[WebSocket] = set()

    def init_apex(self):
        self.client = BitgetClient()
        self.data_manager = DataManager(self.client)
        self.risk_manager = RiskManager(account_balance=10_000.0)
        self.signal_engine = SignalEngine()

state = AppState()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("APEX Dashboard starting up...")
    state.init_apex()
    # Kick off background scan loop
    task = asyncio.create_task(background_scan_loop())
    yield
    task.cancel()
    logger.info("APEX Dashboard shutting down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="APEX Trading Dashboard", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def api_status():
    """Return current risk manager status."""
    status = state.risk_manager.get_status_report()
    status["scan_in_progress"] = state.scan_in_progress
    status["last_scan"] = (
        datetime.fromtimestamp(state.last_scan_time, tz=timezone.utc).isoformat()
        if state.last_scan_time else None
    )
    status["server_time"] = datetime.now(tz=timezone.utc).isoformat()
    return status


@app.get("/api/signals")
async def api_signals():
    """Return the latest scan signals."""
    return {
        "signals": state.last_signals,
        "scan_in_progress": state.scan_in_progress,
        "last_scan": (
            datetime.fromtimestamp(state.last_scan_time, tz=timezone.utc).isoformat()
            if state.last_scan_time else None
        ),
    }


@app.get("/api/pair/{symbol:path}")
async def api_pair(symbol: str):
    """Analyse a single pair on demand."""
    symbol = symbol.replace("-", "/").replace("_", "/")
    if ":" not in symbol:
        symbol = symbol + ":USDT"

    try:
        timeframes = state.data_manager.get_multi_timeframe(symbol, force_refresh=True)
        signal = state.signal_engine.analyse(symbol, timeframes)
        result = signal_to_dict(signal)
    except Exception as exc:
        result = {"valid": False, "pair": symbol, "reason": str(exc)}

    await broadcast({"type": "single_signal", "data": result})
    return result


@app.post("/api/scan")
async def api_scan():
    """Trigger an immediate market scan."""
    if state.scan_in_progress:
        return {"status": "already_running"}
    asyncio.create_task(run_scan())
    return {"status": "started"}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state.connected_clients.add(ws)
    logger.info("WebSocket client connected. Total: %d", len(state.connected_clients))

    try:
        # Send current state immediately on connect
        await ws.send_json({
            "type": "init",
            "data": {
                "status": state.risk_manager.get_status_report(),
                "signals": state.last_signals,
                "scan_in_progress": state.scan_in_progress,
                "last_scan": (
                    datetime.fromtimestamp(state.last_scan_time, tz=timezone.utc).isoformat()
                    if state.last_scan_time else None
                ),
            }
        })

        # Keep alive + handle incoming messages
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                data = json.loads(msg)
                await handle_ws_message(ws, data)
            except asyncio.TimeoutError:
                # Send heartbeat
                await ws.send_json({"type": "ping", "ts": time.time()})

    except WebSocketDisconnect:
        pass
    finally:
        state.connected_clients.discard(ws)
        logger.info("WebSocket client disconnected. Total: %d", len(state.connected_clients))


async def handle_ws_message(ws: WebSocket, data: dict):
    action = data.get("action")
    if action == "scan":
        if not state.scan_in_progress:
            asyncio.create_task(run_scan())
    elif action == "analyse_pair":
        symbol = data.get("symbol", "")
        if symbol:
            asyncio.create_task(run_single_analysis(symbol))
    elif action == "pong":
        pass


async def broadcast(message: dict):
    """Send a message to all connected WebSocket clients."""
    dead = set()
    for ws in state.connected_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    state.connected_clients -= dead


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

async def run_scan(pairs: Optional[List[str]] = None):
    """Run a full market scan asynchronously."""
    if state.scan_in_progress:
        return

    state.scan_in_progress = True
    await broadcast({"type": "scan_started", "ts": time.time()})
    logger.info("Market scan started.")

    try:
        if pairs is None:
            try:
                pairs = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: state.data_manager.get_top_pairs(n=20)
                )
            except Exception:
                pairs = config.TOP_PAIRS[:20]

        results = []
        for i, symbol in enumerate(pairs):
            # Notify progress
            await broadcast({
                "type": "scan_progress",
                "symbol": symbol,
                "current": i + 1,
                "total": len(pairs),
            })

            try:
                timeframes = await asyncio.get_event_loop().run_in_executor(
                    None, lambda s=symbol: state.data_manager.get_multi_timeframe(s)
                )
                signal = await asyncio.get_event_loop().run_in_executor(
                    None, lambda s=symbol, tf=timeframes: state.signal_engine.analyse(s, tf)
                )
                result = signal_to_dict(signal)
                results.append(result)

                # Broadcast individual pair result
                await broadcast({
                    "type": "pair_result",
                    "symbol": symbol,
                    "data": result,
                })

            except Exception as exc:
                logger.error("Error analysing %s: %s", symbol, exc)
                results.append({"valid": False, "pair": symbol, "reason": str(exc)})

            await asyncio.sleep(0.2)

        # Sort: valid signals first, by confidence
        valid = sorted(
            [r for r in results if r.get("valid")],
            key=lambda x: x.get("confidence", 0),
            reverse=True,
        )
        invalid = [r for r in results if not r.get("valid")]
        state.last_signals = valid + invalid
        state.last_scan_time = time.time()

        await broadcast({
            "type": "scan_complete",
            "signals": state.last_signals,
            "ts": state.last_scan_time,
            "valid_count": len(valid),
            "total": len(results),
        })
        logger.info("Scan complete. %d valid signals from %d pairs.", len(valid), len(results))

    except Exception as exc:
        logger.exception("Scan error: %s", exc)
        await broadcast({"type": "scan_error", "message": str(exc)})
    finally:
        state.scan_in_progress = False


async def run_single_analysis(symbol: str):
    """Analyse a single pair and broadcast result."""
    await broadcast({"type": "analysing", "symbol": symbol})
    try:
        timeframes = await asyncio.get_event_loop().run_in_executor(
            None, lambda: state.data_manager.get_multi_timeframe(symbol, force_refresh=True)
        )
        signal = await asyncio.get_event_loop().run_in_executor(
            None, lambda: state.signal_engine.analyse(symbol, timeframes)
        )
        result = signal_to_dict(signal)
    except Exception as exc:
        result = {"valid": False, "pair": symbol, "reason": str(exc)}

    await broadcast({"type": "single_signal", "symbol": symbol, "data": result})


# ---------------------------------------------------------------------------
# Background auto-scan loop
# ---------------------------------------------------------------------------

async def background_scan_loop():
    """Auto-scan every 5 minutes."""
    await asyncio.sleep(3)   # warm-up
    while True:
        try:
            await run_scan()
        except Exception as exc:
            logger.error("Background scan error: %s", exc)
        await asyncio.sleep(300)   # 5 min


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )
