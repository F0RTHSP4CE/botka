"""Dishka integration for python-telegram-bot."""

from functools import wraps
from typing import Any, Callable, TypeVar, ParamSpec, get_type_hints

from dishka import AsyncContainer
from telegram import Update
from telegram.ext import ContextTypes, Application

P = ParamSpec("P")
T = TypeVar("T")


# Key for storing container in bot_data
CONTAINER_KEY = "dishka_container"


def setup_dishka(container: AsyncContainer, app: Application) -> None:
    """Set up Dishka container for python-telegram-bot application.

    Args:
        container: The Dishka async container
        app: The python-telegram-bot Application instance
    """
    app.bot_data[CONTAINER_KEY] = container


def get_container(context: ContextTypes.DEFAULT_TYPE) -> AsyncContainer:
    """Get the Dishka container from context.

    Args:
        context: The telegram context

    Returns:
        The AsyncContainer instance

    Raises:
        RuntimeError: If container is not set up
    """
    container = context.bot_data.get(CONTAINER_KEY)
    if container is None:
        raise RuntimeError(
            "Dishka container not found. Call setup_dishka() before using inject()."
        )
    return container


def inject(func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to inject dependencies into handler functions.

    Dependencies are resolved from type hints. The handler must have
    'update' and 'context' as first two parameters (standard python-telegram-bot).
    Additional parameters will be injected from the container.

    Example:
        @inject
        async def cmd_status(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            resident_service: ResidentService,  # Injected
            config: Config,  # Injected
        ) -> None:
            ...
    """

    @wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> T:
        container = get_container(context)

        # Get type hints for injection
        hints = get_type_hints(func)

        # Skip 'update', 'context', and 'return'
        skip_params = {"update", "context", "return"}

        # Enter request scope and inject dependencies
        async with container() as request_container:
            for param_name, param_type in hints.items():
                if param_name in skip_params:
                    continue
                if param_name not in kwargs:
                    kwargs[param_name] = await request_container.get(param_type)

            return await func(update, context, *args, **kwargs)

    return wrapper


class FromDishka:
    """Type hint marker for Dishka injection (for documentation/IDE support).

    Usage:
        async def handler(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            service: FromDishka[MyService],
        ): ...

    Note: The actual injection is done by @inject decorator, not this class.
    This is just for compatibility with other Dishka integrations.
    """

    def __class_getitem__(cls, item: type[T]) -> type[T]:
        """Support FromDishka[Type] syntax, returns the inner type."""
        return item
