"""Message classification for NLP module."""

import logging
import json
from typing import Optional
from enum import Enum

import httpx

from .types import ClassificationResult, CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)


async def classify_request(
    text: str, history_context: str = ""
) -> ClassificationResult:
    """Classify a request to determine handling level."""
    from ...bot import state

    openai = state.config.services.openai
    if not openai:
        return ClassificationResult.HANDLE_2  # Default

    nlp_config = state.config.nlp
    if not nlp_config or not nlp_config.models:
        return ClassificationResult.HANDLE_2

    # Use first (cheapest) model for classification
    model = nlp_config.models[0]

    user_message = (
        f"History context:\n{history_context}\n\nMessage to classify:\n{text}"
    )

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{openai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": CLASSIFICATION_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 20,
                    "temperature": 0.0,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "Classification",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "classification": {"type": ["integer", "null"]},
                                },
                                "required": ["classification"],
                                "additionalProperties": False,
                            },
                        },
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)

            classification = result.get("classification")
            if classification == 1:
                return ClassificationResult.HANDLE_1
            elif classification == 2:
                return ClassificationResult.HANDLE_2
            elif classification == 3:
                return ClassificationResult.HANDLE_3
            else:
                return ClassificationResult.IGNORE

        except Exception as e:
            logger.error(f"Classification error: {e}")
            return ClassificationResult.HANDLE_2


async def classify_random_request(text: str, history_context: str = "") -> bool:
    """Classify if bot should respond to a random message."""
    from ...bot import state

    openai = state.config.services.openai
    if not openai:
        return False

    nlp_config = state.config.nlp
    if not nlp_config or not nlp_config.models:
        return False

    model = nlp_config.models[0]

    random_prompt = """You are a conversation intervention classifier that determines whether a bot should respond to a message in a group chat.

DECISION CATEGORIES:
1. RESPOND (return value: true): Topic where bot expertise would be valuable, information requests
2. DO NOT RESPOND (return value: false): Casual chat, personal exchanges, topics outside bot expertise

Default to NOT respond (false) unless clear value would be added.

Respond with JSON: {"should_respond": true | false}"""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{openai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": random_prompt},
                        {
                            "role": "user",
                            "content": f"Context:\n{history_context}\n\nMessage:\n{text}",
                        },
                    ],
                    "max_tokens": 20,
                    "temperature": 0.0,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "RandomClassification",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "should_respond": {"type": "boolean"},
                                },
                                "required": ["should_respond"],
                                "additionalProperties": False,
                            },
                        },
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            return result.get("should_respond", False)

        except Exception as e:
            logger.error(f"Random classification error: {e}")
            return False
