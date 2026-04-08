#!/usr/bin/env python3
"""Quick launcher: python run_dashboard.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
