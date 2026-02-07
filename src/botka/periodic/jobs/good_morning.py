from __future__ import annotations

from dataclasses import dataclass
import asyncio
from datetime import datetime
import logging
import random
import secrets

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
    provider_count: int
    forecast_min_c: float | None
    forecast_max_c: float | None


async def send_good_morning(context: PeriodicContext) -> None:
    if context.settings.good_morning_chat_id is None:
        return
    weather = await _fetch_weather(context)
    city = (
        weather.location
        if weather is not None
        else (context.settings.good_morning_city or "your city")
    )
    now = datetime.now().strftime("%H:%M")
    lines = ["Good morning! It's {} in {}.".format(now, city)]
    if weather is not None:
        weather_line = "Weather: <strong>{} {:.1f}°C {}</strong>".format(
            weather.emoji,
            weather.temperature_c,
            weather.condition,
        )
        if weather.forecast_min_c is not None and weather.forecast_max_c is not None:
            weather_line += " min {:.1f}°C max {:.1f}°C".format(
                weather.forecast_min_c,
                weather.forecast_max_c,
            )
        lines.append(weather_line)
    else:
        lines.append("Weather: <strong>unavailable</strong>")
    text = "\n".join(lines)
    photos = await _fetch_photos(context)
    random.shuffle(photos)
    if len(photos) >= 2:
        media_group = MediaGroupBuilder()
        for idx, photo in enumerate(photos):
            if idx == 0:
                media_group.add_photo(media=photo, caption=text, parse_mode="HTML")
                continue
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
            parse_mode="HTML",
        )
        return
    await context.bot.send_message(
        chat_id=context.settings.good_morning_chat_id,
        message_thread_id=context.settings.good_morning_topic_id,
        text=text,
        parse_mode="HTML",
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
        weather = await _fetch_aggregated_weather(
            client,
            geo_data.latitude,
            geo_data.longitude,
        )
    if weather is None:
        return None
    return _WeatherSnapshot(
        location=geo_data.location,
        temperature_c=weather.temperature_c,
        windspeed_kph=weather.windspeed_kph,
        condition=weather.condition,
        emoji=weather.emoji,
        provider_count=weather.provider_count,
        forecast_min_c=weather.forecast_min_c,
        forecast_max_c=weather.forecast_max_c,
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


@dataclass(frozen=True)
class _ProviderWeather:
    temperature_c: float
    windspeed_kph: float
    condition: str
    emoji: str
    forecast_min_c: float | None
    forecast_max_c: float | None
    source: str


@dataclass(frozen=True)
class _AggregatedWeather:
    temperature_c: float
    windspeed_kph: float
    condition: str
    emoji: str
    forecast_min_c: float | None
    forecast_max_c: float | None
    provider_count: int


async def _fetch_aggregated_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> _AggregatedWeather | None:
    tasks = [
        _fetch_open_meteo_weather(client, latitude, longitude),
        _fetch_met_no_weather(client, latitude, longitude),
        _fetch_wttr_weather(client, latitude, longitude),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    providers: list[_ProviderWeather] = []
    for item in results:
        if isinstance(item, Exception) or item is None:
            continue
        providers.append(item)
    if not providers:
        return None
    temperature_c = sum(p.temperature_c for p in providers) / len(providers)
    windspeed_kph = sum(p.windspeed_kph for p in providers) / len(providers)
    condition, emoji = _pick_dominant_condition(providers)
    forecast_min, forecast_max = _average_forecast_bounds(providers)
    return _AggregatedWeather(
        temperature_c=temperature_c,
        windspeed_kph=windspeed_kph,
        condition=condition,
        emoji=emoji,
        forecast_min_c=forecast_min,
        forecast_max_c=forecast_max,
        provider_count=len(providers),
    )


def _pick_dominant_condition(providers: list[_ProviderWeather]) -> tuple[str, str]:
    counts: dict[str, int] = {}
    emojis: dict[str, str] = {}
    for provider in providers:
        counts[provider.condition] = counts.get(provider.condition, 0) + 1
        emojis.setdefault(provider.condition, provider.emoji)
    condition = max(counts.items(), key=lambda item: item[1])[0]
    return condition, emojis.get(condition, "❓")


def _average_forecast_bounds(
    providers: list[_ProviderWeather],
) -> tuple[float | None, float | None]:
    min_values = [p.forecast_min_c for p in providers if p.forecast_min_c is not None]
    max_values = [p.forecast_max_c for p in providers if p.forecast_max_c is not None]
    if not min_values or not max_values:
        return None, None
    return (
        sum(min_values) / len(min_values),
        sum(max_values) / len(max_values),
    )


async def _fetch_open_meteo_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> _ProviderWeather | None:
    response = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current_weather": True,
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
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
    daily = payload.get("daily") or {}
    max_values = daily.get("temperature_2m_max") or []
    min_values = daily.get("temperature_2m_min") or []
    forecast_max = float(max_values[0]) if max_values else None
    forecast_min = float(min_values[0]) if min_values else None
    return _ProviderWeather(
        temperature_c=float(current.get("temperature")),
        windspeed_kph=float(current.get("windspeed")),
        condition=condition,
        emoji=emoji,
        forecast_min_c=forecast_min,
        forecast_max_c=forecast_max,
        source="open-meteo",
    )


async def _fetch_met_no_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> _ProviderWeather | None:
    response = await client.get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": latitude, "lon": longitude},
        headers={"User-Agent": "botka"},
        timeout=10.0,
    )
    if response.is_error:
        return None
    payload = response.json()
    timeseries = payload.get("properties", {}).get("timeseries") or []
    if not timeseries:
        return None
    first = timeseries[0]
    instant = first.get("data", {}).get("instant", {}).get("details", {})
    temperature = instant.get("air_temperature")
    wind_mps = instant.get("wind_speed")
    if temperature is None or wind_mps is None:
        return None
    summary = first.get("data", {}).get("next_1_hours", {}).get("summary", {})
    symbol_code = summary.get("symbol_code")
    emoji, condition = _met_no_condition(symbol_code)
    forecast_min, forecast_max = _met_no_daily_min_max(timeseries)
    return _ProviderWeather(
        temperature_c=float(temperature),
        windspeed_kph=float(wind_mps) * 3.6,
        condition=condition,
        emoji=emoji,
        forecast_min_c=forecast_min,
        forecast_max_c=forecast_max,
        source="met.no",
    )


def _met_no_daily_min_max(
    timeseries: list[dict[str, object]],
) -> tuple[float | None, float | None]:
    temps: list[float] = []
    for item in timeseries[:24]:
        details = item.get("data", {}).get("instant", {}).get("details", {})
        value = details.get("air_temperature")
        if value is not None:
            temps.append(float(value))
    if not temps:
        return None, None
    return min(temps), max(temps)


def _met_no_condition(symbol_code: str | None) -> tuple[str, str]:
    if not symbol_code:
        return "❓", "unknown"
    symbol = symbol_code.split("_")[0]
    mapping: dict[str, tuple[str, str]] = {
        "clearsky": ("☀️", "clear sky"),
        "fair": ("🌤️", "fair"),
        "partlycloudy": ("⛅", "partly cloudy"),
        "cloudy": ("☁️", "cloudy"),
        "fog": ("🌫️", "fog"),
        "lightrain": ("🌦️", "light rain"),
        "rain": ("🌧️", "rain"),
        "heavyrain": ("🌧️", "heavy rain"),
        "lightsnow": ("🌨️", "light snow"),
        "snow": ("❄️", "snow"),
        "heavysnow": ("❄️", "heavy snow"),
        "sleet": ("🌧️", "sleet"),
        "thunderstorm": ("⛈️", "thunderstorm"),
    }
    return mapping.get(symbol, ("❓", symbol))


async def _fetch_wttr_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> _ProviderWeather | None:
    response = await client.get(
        f"https://wttr.in/{latitude},{longitude}",
        params={"format": "j1"},
        timeout=10.0,
    )
    if response.is_error:
        return None
    payload = response.json()
    current = (payload.get("current_condition") or [{}])[0]
    weather_today = (payload.get("weather") or [{}])[0]
    temperature = current.get("temp_C")
    wind_kph = current.get("windspeedKmph")
    if temperature is None or wind_kph is None:
        return None
    description = "unknown"
    desc_list = current.get("weatherDesc") or []
    if desc_list:
        description = desc_list[0].get("value") or description
    emoji, condition = _wttr_condition(description)
    forecast_min = weather_today.get("mintempC")
    forecast_max = weather_today.get("maxtempC")
    return _ProviderWeather(
        temperature_c=float(temperature),
        windspeed_kph=float(wind_kph),
        condition=condition,
        emoji=emoji,
        forecast_min_c=float(forecast_min) if forecast_min is not None else None,
        forecast_max_c=float(forecast_max) if forecast_max is not None else None,
        source="wttr.in",
    )


def _wttr_condition(description: str) -> tuple[str, str]:
    lowered = description.lower()
    if "thunder" in lowered:
        return "⛈️", "thunderstorm"
    if "snow" in lowered:
        return "❄️", "snow"
    if "rain" in lowered or "drizzle" in lowered:
        return "🌧️", "rain"
    if "fog" in lowered or "mist" in lowered:
        return "🌫️", "fog"
    if "cloud" in lowered:
        return "☁️", "cloudy"
    if "sun" in lowered or "clear" in lowered:
        return "☀️", "clear"
    return "❓", description


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
            extension = content_type.split("/", 1)[-1] or "jpg"
            token = secrets.token_hex(8)
            filename = f"photo-{token}.{extension}"
            return BufferedInputFile(content, filename=filename)
        logger.warning(
            "Empty image response for %s (attempt %s, content-length=%s)",
            url,
            attempt,
            response.headers.get("content-length"),
        )
    return None
