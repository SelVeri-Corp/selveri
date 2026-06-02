"""Shim entry point: python server/server.py starts the SelVeri LSP server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from selveri_lsp.server import main

if __name__ == "__main__":
    main()
