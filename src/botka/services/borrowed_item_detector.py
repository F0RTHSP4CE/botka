from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from botka.config import Settings

logger = logging.getLogger(__name__)


class BorrowedItemDetector:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openai_api_key)

    async def detect_item_names(
        self,
        text: str | None,
        images: list[tuple[bytes, str]] | None,
    ) -> list[str]:
        clean_text = (text or "").strip()
        if not self.is_configured:
            logger.info("Borrowed detector not configured; using fallback parser.")
            return self._fallback_parse_items(clean_text)
        try:
            payload = self._build_payload(clean_text, images)
            headers = {
                "Authorization": f"Bearer {self._settings.openai_api_key}",
                "Content-Type": "application/json",
            }
            timeout = httpx.Timeout(self._settings.openai_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=payload,
                )
            if not response.is_success:
                logger.warning(
                    "Borrowed detector HTTP error: %s",
                    response.status_code,
                )
                return self._fallback_parse_items(clean_text)
            data: dict[str, Any] = response.json()
            text_out = self._extract_text_output(data)
            if not text_out:
                logger.warning("Borrowed detector empty output text.")
                return self._fallback_parse_items(clean_text)
            parsed = self._parse_json(text_out)
            if parsed is not None:
                items = self._coerce_items(parsed)
                normalized = self._normalize_items(items) if items else []
                logger.info(
                    "Borrowed detector parsed %d item(s).",
                    len(normalized),
                )
                return normalized
            logger.info(
                "Borrowed detector failed to parse JSON; using fallback. Output: %s",
                text_out,
            )
            return self._fallback_parse_items(clean_text)
        except Exception:
            logger.exception("Borrowed detector failed; using fallback parser.")
            return self._fallback_parse_items(clean_text)

    def _build_payload(
        self,
        text: str,
        images: list[tuple[bytes, str]] | None,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "You are analyzing a Telegram message from a 'borrowed items' chat topic. "
                    "People use this topic both to report borrowing physical items AND for casual chat/discussion. "
                    "Your task: determine if the message is actually about someone borrowing/taking a physical item. "
                    "Return JSON only with key 'items' as an array of strings. "
                    "If the message is casual conversation, a question, a greeting, a reply to someone, "
                    "a status update, or anything NOT about borrowing a specific physical item, "
                    'return {"items": []}. '
                    "Only extract item names when the person is clearly stating they are taking/borrowing something. "
                    "Keep each item short (1-5 words), no durations or descriptions. "
                    "If text is empty, infer the item(s) from the image(s) only if the image clearly shows "
                    "an item being taken or borrowed. "
                    f"Message text: {text or '<<no text>>'}"
                ),
            }
        ]
        if images:
            for image_bytes, image_mime in images:
                mime = image_mime or "image/jpeg"
                encoded = base64.b64encode(image_bytes).decode("ascii")
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{encoded}",
                    }
                )
        return {
            "model": self._settings.openai_model,
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "max_output_tokens": 80,
        }

    def _extract_text_output(self, data: dict[str, Any]) -> str | None:
        for item in data.get("output", []) or []:
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    return part.get("text")
        return None

    def _parse_json(self, text_out: str) -> dict[str, Any] | None:
        try:
            return json.loads(text_out)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text_out, re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

    def _coerce_items(self, parsed: dict[str, Any]) -> list[str]:
        items: list[str] = []
        raw_items = parsed.get("items")
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        items.append(cleaned)
        raw_item = parsed.get("item")
        if isinstance(raw_item, str):
            cleaned = raw_item.strip()
            if cleaned:
                items.append(cleaned)
        return items

    def _normalize_items(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in items:
            trimmed = item.strip(" .!\n\t")
            if not trimmed:
                continue
            key = trimmed.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(trimmed)
        return normalized

    _BORROW_PATTERN = re.compile(
        r"^(took|take|taking|borrowed|borrowing|borrow|grabbed|grabbing|"
        r"picked up|picking up|взял[аи]?|забрал[аи]?|беру|забираю)"
        r"\s+",
        re.IGNORECASE,
    )

    def _fallback_parse_items(self, text: str) -> list[str]:
        if not text:
            return []
        match = self._BORROW_PATTERN.match(text)
        if not match:
            return []
        cleaned = text[match.end() :]
        cleaned = re.sub(r"\s+(for|until|на|до)\s+.+$", "", cleaned, flags=re.I)
        cleaned = cleaned.strip(" .!\n\t")
        if not cleaned:
            return []
        parts = re.split(r"\s*(?:,|&|\band\b|\bи\b)\s*", cleaned, flags=re.I)
        if len(parts) <= 1:
            return [cleaned]
        items = [part.strip(" .!\n\t") for part in parts if part.strip(" .!\n\t")]
        return self._normalize_items(items)
