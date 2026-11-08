"""Internal preview launcher; normal Windows startup uses start.ps1."""
import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "3000")))
    args, _ = parser.parse_known_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)
