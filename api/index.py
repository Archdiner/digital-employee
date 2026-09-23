"""Vercel entry point. The same FastAPI app; MODE=web so it only enqueues work for the Azure worker."""
from app.main import app  # noqa: F401
