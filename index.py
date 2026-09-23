"""Vercel entry point (zero-config FastAPI). Same app; MODE=web means it only enqueues work for the Azure worker."""
from app.main import app  # noqa: F401
