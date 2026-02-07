from __future__ import annotations

from dataclasses import dataclass
import logging
import random
from urllib.parse import urlparse

import httpx
from aiogram.types.input_file import BufferedInputFile
from aiogram.utils.media_group import MediaGroupBuilder

from botka.periodic.jobs.base import PeriodicContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _WeatherSnapshot:
    location: str
    temperature_c: float
    windspeed_kph: float
    condition: str
    emoji: str


async def send_good_morning(context: PeriodicContext) -> None:
    if context.settings.good_morning_chat_id is None:
        return
    weather = await _fetch_weather(context)
    lines = ["Good morning."]
    if weather is not None:
        lines.append(
            "Weather in {}: {} {} | {:.1f}C, wind {:.1f} km/h.".format(
                weather.location,
                weather.emoji,
                weather.condition,
                weather.temperature_c,
                weather.windspeed_kph,
            )
        )
    text = "\n".join(lines)
    photos = await _fetch_photos(context)
    random.shuffle(photos)
    if len(photos) >= 2:
        media_group = MediaGroupBuilder(caption=text)
        for photo in photos:
            media_group.add_photo(media=photo)
        await context.bot.send_media_group(
            chat_id=context.settings.good_morning_chat_id,
            message_thread_id=context.settings.good_morning_topic_id,
            media=media_group.build(),
        )
        return
    if photos:
        await context.bot.send_photo(
            chat_id=context.settings.good_morning_chat_id,
            message_thread_id=context.settings.good_morning_topic_id,
            photo=photos[0],
            caption=text,
        )
        return
    await context.bot.send_message(
        chat_id=context.settings.good_morning_chat_id,
        message_thread_id=context.settings.good_morning_topic_id,
        text=text,
        disable_web_page_preview=True,
    )


async def _fetch_weather(context: PeriodicContext) -> _WeatherSnapshot | None:
    city = (context.settings.good_morning_city or "").strip()
    if not city:
        return None
    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=True, max_redirects=5
    ) as client:
        geo_data = await _fetch_geocoding(client, city)
        if geo_data is None:
            return None
        weather = await _fetch_current_weather(
            client,
            geo_data.latitude,
            geo_data.longitude,
        )
    if weather is None:
        return None
    return _WeatherSnapshot(
        location=geo_data.location,
        temperature_c=weather["temperature"],
        windspeed_kph=weather["windspeed"],
        condition=weather["condition"],
        emoji=weather["emoji"],
    )


@dataclass(frozen=True)
class _GeoResult:
    location: str
    latitude: float
    longitude: float


async def _fetch_geocoding(client: httpx.AsyncClient, city: str) -> _GeoResult | None:
    response = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=10.0,
    )
    if response.is_error:
        return None
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return None
    item = results[0]
    name = item.get("name") or city
    country = item.get("country")
    label = f"{name}, {country}" if country else name
    return _GeoResult(
        location=label,
        latitude=float(item["latitude"]),
        longitude=float(item["longitude"]),
    )


async def _fetch_current_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> dict[str, float | str] | None:
    response = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current_weather": True,
        },
        timeout=10.0,
    )
    if response.is_error:
        return None
    payload = response.json()
    current = payload.get("current_weather")
    if not current:
        return None
    code = current.get("weathercode")
    emoji, condition = _weather_condition(code)
    return {
        "temperature": float(current.get("temperature")),
        "windspeed": float(current.get("windspeed")),
        "emoji": emoji,
        "condition": condition,
    }


def _weather_condition(code: int | None) -> tuple[str, str]:
    if code is None:
        return "❓", "unknown"
    mapping: dict[int, tuple[str, str]] = {
        0: ("☀️", "clear sky"),
        1: ("🌤️", "mainly clear"),
        2: ("⛅", "partly cloudy"),
        3: ("☁️", "overcast"),
        45: ("🌫️", "fog"),
        48: ("🌫️", "rime fog"),
        51: ("🌦️", "light drizzle"),
        53: ("🌦️", "moderate drizzle"),
        55: ("🌧️", "dense drizzle"),
        56: ("🌧️", "light freezing drizzle"),
        57: ("🌧️", "dense freezing drizzle"),
        61: ("🌧️", "slight rain"),
        63: ("🌧️", "moderate rain"),
        65: ("🌧️", "heavy rain"),
        66: ("🌧️", "light freezing rain"),
        67: ("🌧️", "heavy freezing rain"),
        71: ("🌨️", "slight snow"),
        73: ("🌨️", "moderate snow"),
        75: ("❄️", "heavy snow"),
        77: ("❄️", "snow grains"),
        80: ("🌦️", "slight rain showers"),
        81: ("🌦️", "moderate rain showers"),
        82: ("🌧️", "violent rain showers"),
        85: ("🌨️", "slight snow showers"),
        86: ("🌨️", "heavy snow showers"),
        95: ("⛈️", "thunderstorm"),
        96: ("⛈️", "thunderstorm with hail"),
        99: ("⛈️", "thunderstorm with heavy hail"),
    }
    return mapping.get(int(code), ("❓", "unknown"))


async def _fetch_photos(context: PeriodicContext) -> list[BufferedInputFile]:
    urls = context.settings.good_morning_photo_urls
    if not urls:
        return []
    photos: list[BufferedInputFile] = []
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        max_redirects=5,
        headers={"Accept": "image/*", "User-Agent": "botka"},
    ) as client:
        for url in urls:
            photo = await _fetch_photo(client, url)
            if photo is not None:
                photos.append(photo)
    return photos


async def _fetch_photo(client: httpx.AsyncClient, url: str) -> BufferedInputFile | None:
    for attempt in range(1, 6):
        try:
            response = await client.get(url, timeout=10.0)
        except httpx.HTTPError:
            return None
        if response.is_error:
            return None
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return None
        content = await response.aread()
        if content:
            filename = _filename_from_url(url, content_type)
            return BufferedInputFile(content, filename=filename)
        logger.warning(
            "Empty image response for %s (attempt %s, content-length=%s)",
            url,
            attempt,
            response.headers.get("content-length"),
        )
    return None


def _filename_from_url(url: str, content_type: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if name:
        return name
    extension = content_type.split("/", 1)[-1] or "jpg"
    return f"photo.{extension}"
