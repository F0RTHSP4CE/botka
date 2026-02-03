from .answers import router as answers_router
from .messages import router as messages_router
from .commands import router as commands_router

__all__ = ["answers_router", "commands_router", "messages_router"]
