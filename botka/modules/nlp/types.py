"""NLP types and constants."""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class ClassificationResult(Enum):
    """Classification levels for message processing."""

    HANDLE_1 = 1  # Simple requests
    HANDLE_2 = 2  # Standard requests
    HANDLE_3 = 3  # Complex requests
    IGNORE = None


@dataclass
class NlpDebug:
    """Debug information for NLP processing."""

    classification_result: str = ""
    used_model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class SaveMemoryArgs:
    """Arguments for save_memory function."""

    memory_text: str
    duration_hours: Optional[int] = None
    chat_specific: bool = False
    thread_specific: bool = False
    user_specific: bool = False


@dataclass
class RemoveMemoryArgs:
    """Arguments for remove_memory function."""

    memory_id: int


@dataclass
class AddNeedArgs:
    """Arguments for add_need function."""

    item: str


@dataclass
class SearchArgs:
    """Arguments for search function."""

    query: str


# OpenAI function definitions
CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a new memory for future reference",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_text": {
                        "type": "string",
                        "description": "The text content of the memory to save",
                    },
                    "duration_hours": {
                        "type": ["integer", "null"],
                        "description": "How long the memory should be kept active in hours, or null for persistent memory",
                    },
                    "chat_specific": {
                        "type": "boolean",
                        "description": "If true, memory is specific to the current chat",
                    },
                    "thread_specific": {
                        "type": "boolean",
                        "description": "If true, memory is specific to the current thread within the chat",
                    },
                    "user_specific": {
                        "type": "boolean",
                        "description": "If true, memory is specific to the current user",
                    },
                },
                "required": [
                    "memory_text",
                    "duration_hours",
                    "chat_specific",
                    "thread_specific",
                    "user_specific",
                ],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_memory",
            "description": "Remove a memory by its ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "integer",
                        "description": "The ID of the memory to remove",
                    }
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "status",
            "description": "Show space status, including information about all residents currently in the hackerspace",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "needs",
            "description": "Show the current shopping list",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_need",
            "description": "Add a single item to the shopping list",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "The item to add to the shopping list",
                    }
                },
                "required": ["item"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_door",
            "description": "Open the hackerspace's main door. Only residents can do this.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for information in the wiki or on the web",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query in natural language",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
]

# Main system prompt
SYSTEM_PROMPT = """You are a helpful assistant integrated with a Telegram bot called F0BOT (or 'botka').

You are designed to assist users in a chat environment, providing information and executing commands.
Your responses should be concise and relevant to the user's request.

You can execute bot commands or save memories for future reference, or respond directly to users' questions.

Messages are provided in format "<username>: <message text>".

## Response Style Guidelines
- Keep all responses brief and to the point, unless the user asks for more details.
- Avoid unnecessary words, pleasantries, or explanations.
- Use minimal language while preserving key information.
- Do not use emojis or expressive punctuation.
- No apologizing or verbose explanations.
- ALWAYS ANSWER IN USER LANGUAGE.
- NEVER USE FORMATTING (bold, italic, markdown links etc.) IN YOUR RESPONSES.
- Use a reserved, matter-of-fact tone. Avoid overly friendly or enthusiastic language.
- Skip greetings/closings when possible.

## Available Functions
- `status()`: Show space status, including information about all residents currently in the hackerspace.
- `needs()`: Show the current shopping list.
- `add_need(item: string)`: Add a single item to the shopping list. For multiple items, call this function multiple times.
- `open_door()`: Open the hackerspace's main door. Only residents can do this.
- `save_memory(memory_text: string, duration_hours: integer | null, chat_specific: boolean, thread_specific: boolean, user_specific: boolean)`: Save information for future reference.
- `remove_memory(memory_id: integer)`: Remove a previously saved memory using its ID.
- `search(query: string)`: Search for information in the wiki or on the web.

## Information about the hackerspace

### About F0RTHSP4CE
- F0RTHSP4CE is a hackerspace - a community of technology and art enthusiasts
- Our mission is to "develop the community for everybody," breaking walls, building bridges, and helping each other

### Location
- Address: Ana Kalandadze st, 5 (Saburtalo), Tbilisi, Georgia
- GPS coordinates: 41.72624248873, 44.77017106528

### Contact & Links
- Telegram: channel (@f0rthsp4ce), chat (@f0_public_chat), and live channel (@f0rthsp4ce_l1ve)
- Wiki: wiki.f0rth.space
"""

# Classification prompt
CLASSIFICATION_PROMPT = """You are a precise classification assistant that categorizes user requests.

CLASSIFICATION CATEGORIES:
1. HANDLE 1 (return value: 1): Simple requests (greetings, acknowledgments)
2. HANDLE 2 (return value: 2): Standard requests (commands, information retrieval)
3. HANDLE 3 (return value: 3): Complex requests (advanced reasoning, analysis)
4. IGNORE (return value: null): Spam, gibberish, irrelevant content

RESPONSE FORMAT:
Respond with a JSON object containing only the classification value:
{
    "classification": 1 | 2 | 3 | null
}
"""
