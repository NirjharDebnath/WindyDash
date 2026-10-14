"""
WindyDash Daemon Entrypoint
Aliases sysmon.py for backward compatibility.
"""
from sysmon import app, HOST, PORT
import uvicorn

if __name__ == "__main__":
    print(f"WindyDash Daemon running on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")