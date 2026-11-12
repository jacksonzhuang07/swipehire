"""Vercel Python entrypoint — serves the SwipeHire Flask app."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VERCEL", "1")
from app import create_app  # noqa: E402
app = create_app()
