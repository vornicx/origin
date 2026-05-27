"""
External APIs Skill para Origin.
Acceso a APIs publicas gratuitas: clima, noticias, traduccion, crypto, IP, etc.
Inspirado en public-apis, construido desde cero sin dependencias de API keys.
"""

import time
import logging
import re
from typing import Dict, Any
from datetime import datetime
from urllib.parse import quote_plus

import httpx

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.external_apis")

# ── Timeout por defecto para llamadas externas ─────────────────────
DEFAULT_TIMEOUT = 15


class ExternalAPIsSkill(BaseSkill):
    """
    Skill: Gateway a APIs publicas gratuitas.

    Acciones:
        weather      - Clima actual y pronostico (Open-Meteo, sin API key)
        news         - Titulares recientes (Wikinews RSS via API)
        translate    - Traduccion de texto (MyMemory API, gratuita)
        crypto       - Precio de criptomonedas (CoinGecko, sin API key)
        ip_info      - Geolocalizacion por IP (ip-api.com, gratuita)
        exchange     - Tasas de cambio de divisas (ExchangeRate API)
        random_fact  - Dato curioso aleatorio (uselessfacts API)
        define       - Definicion de palabra en ingles (Free Dictionary API)

    Todas las APIs son gratuitas y no requieren API key.
    """

    VALID_ACTIONS = {
        "weather",
        "news",
        "translate",
        "crypto",
        "ip_info",
        "exchange",
        "random_fact",
        "define",
    }

    def __init__(self):
        super().__init__(
            name="external_apis", description="Accede a APIs publicas: clima, noticias, traduccion, crypto, divisas"
        )

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if not action:
            return False, f"Se requiere 'action'. Validas: {self.VALID_ACTIONS}"
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        # Validaciones especificas por accion
        if action == "weather":
            if not inputs.get("city") and not (inputs.get("lat") and inputs.get("lon")):
                return False, "Se requiere 'city' o 'lat'+'lon'"

        elif action == "translate":
            if not inputs.get("text"):
                return False, "Se requiere 'text' para traducir"
            if len(inputs["text"]) > 5000:
                return False, "Texto demasiado largo (max 5000 chars)"

        elif action == "crypto":
            if not inputs.get("coin"):
                return False, "Se requiere 'coin' (ej: bitcoin, ethereum)"

        elif action == "exchange":
            if not inputs.get("from_currency"):
                return False, "Se requiere 'from_currency' (ej: USD, EUR)"

        elif action == "define":
            if not inputs.get("word"):
                return False, "Se requiere 'word'"

        return True, ""

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        action = inputs["action"]

        try:
            dispatch = {
                "weather": self._weather,
                "news": self._news,
                "translate": self._translate,
                "crypto": self._crypto,
                "ip_info": self._ip_info,
                "exchange": self._exchange,
                "random_fact": self._random_fact,
                "define": self._define,
            }

            result = await dispatch[action](inputs)

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()

            has_error = isinstance(result, dict) and result.get("error") is not None
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }

        except httpx.TimeoutException:
            elapsed = round(time.time() - start, 3)
            return {
                "success": False,
                "result": None,
                "error": f"Timeout conectando al servicio '{action}'",
                "execution_time": elapsed,
            }
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"External API error ({action}): {e}")
            return {
                "success": False,
                "result": None,
                "error": f"Error en '{action}': {str(e)}",
                "execution_time": elapsed,
            }

    # ── Weather (Open-Meteo) ───────────────────────────────────────

    async def _weather(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clima actual y pronostico via Open-Meteo (100% gratuita, sin API key).
        Soporta busqueda por ciudad o coordenadas.
        """
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            # Si dan ciudad, resolver coordenadas via geocoding
            lat = inputs.get("lat")
            lon = inputs.get("lon")
            city_name = inputs.get("city", "")

            if not lat or not lon:
                geo_url = "https://geocoding-api.open-meteo.com/v1/search"
                geo_resp = await client.get(geo_url, params={"name": city_name, "count": 1, "language": "es"})
                geo_data = geo_resp.json()

                if not geo_data.get("results"):
                    return {"error": f"Ciudad no encontrada: '{city_name}'"}

                location = geo_data["results"][0]
                lat = location["latitude"]
                lon = location["longitude"]
                city_name = location.get("name", city_name)
                country = location.get("country", "")
            else:
                country = ""

            # Obtener clima actual + pronostico 3 dias
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "wind_speed_10m,weather_code,is_day",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code," "precipitation_sum,wind_speed_10m_max",
                "timezone": "auto",
                "forecast_days": 3,
            }

            resp = await client.get(weather_url, params=params)
            data = resp.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            # Decodificar weather code
            wc = current.get("weather_code", 0)

            # Pronostico diario
            forecast = []
            dates = daily.get("time", [])
            for i, date in enumerate(dates):
                forecast.append(
                    {
                        "date": date,
                        "temp_max": daily.get("temperature_2m_max", [None])[i],
                        "temp_min": daily.get("temperature_2m_min", [None])[i],
                        "condition": self._weather_code_to_text(daily.get("weather_code", [0])[i]),
                        "precipitation_mm": daily.get("precipitation_sum", [0])[i],
                        "wind_max_kmh": daily.get("wind_speed_10m_max", [0])[i],
                    }
                )

            return {
                "city": city_name,
                "country": country,
                "coordinates": {"lat": lat, "lon": lon},
                "current": {
                    "temperature_c": current.get("temperature_2m"),
                    "feels_like_c": current.get("apparent_temperature"),
                    "humidity_percent": current.get("relative_humidity_2m"),
                    "wind_kmh": current.get("wind_speed_10m"),
                    "condition": self._weather_code_to_text(wc),
                    "is_day": current.get("is_day", 1) == 1,
                },
                "forecast": forecast,
                "timezone": data.get("timezone", ""),
            }

    @staticmethod
    def _weather_code_to_text(code: int) -> str:
        """Convierte WMO weather code a texto legible."""
        codes = {
            0: "Despejado",
            1: "Mayormente despejado",
            2: "Parcialmente nublado",
            3: "Nublado",
            45: "Niebla",
            48: "Niebla con escarcha",
            51: "Llovizna ligera",
            53: "Llovizna moderada",
            55: "Llovizna intensa",
            61: "Lluvia ligera",
            63: "Lluvia moderada",
            65: "Lluvia intensa",
            71: "Nieve ligera",
            73: "Nieve moderada",
            75: "Nieve intensa",
            77: "Granizo fino",
            80: "Chubascos ligeros",
            81: "Chubascos moderados",
            82: "Chubascos intensos",
            85: "Chubascos de nieve ligeros",
            86: "Chubascos de nieve intensos",
            95: "Tormenta",
            96: "Tormenta con granizo ligero",
            99: "Tormenta con granizo intenso",
        }
        return codes.get(code, f"Codigo {code}")

    # ── News (Wikinews RSS) ────────────────────────────────────────

    async def _news(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Titulares recientes via Wikinews Atom feed.
        Idiomas: en, es, de, fr, etc.
        """
        lang = inputs.get("language", "es")
        max_items = min(inputs.get("max", 10), 20)

        url = f"https://{lang}.wikinews.org/w/api.php"
        params = {
            "action": "query",
            "list": "recentchanges",
            "rcnamespace": "0",
            "rclimit": max_items,
            "rctype": "new",
            "rcprop": "title|timestamp|user",
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        changes = data.get("query", {}).get("recentchanges", [])
        articles = []
        for item in changes:
            title = item.get("title", "")
            articles.append(
                {
                    "title": title,
                    "timestamp": item.get("timestamp", ""),
                    "url": f"https://{lang}.wikinews.org/wiki/{quote_plus(title.replace(' ', '_'))}",
                }
            )

        return {
            "language": lang,
            "articles": articles,
            "total": len(articles),
            "source": "wikinews",
        }

    # ── Translate (MyMemory) ───────────────────────────────────────

    async def _translate(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traduccion via MyMemory API (gratuita, 5000 chars/dia sin API key).
        """
        text = inputs["text"]
        source = inputs.get("from", "auto")
        target = inputs.get("to", "en")

        # Si source es auto, intentar detectar
        if source == "auto":
            source = "es" if re.search(r"[a-zA-Z]", text) and not re.search(r"[ñáéíóú]", text) else "es"
            # Heuristica simple: si parece espanol va a ingles, si no, a espanol
            if target == "en" and source == "es":
                pass  # default ok
            elif re.search(r"[ñáéíóú]", text):
                source = "es"
            else:
                source = "en"

        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text[:5000],
            "langpair": f"{source}|{target}",
        }

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        response_data = data.get("responseData", {})
        translated = response_data.get("translatedText", "")
        match_quality = response_data.get("match")

        # Alternativas de la memoria de traduccion
        alternatives = []
        for m in data.get("matches", [])[:3]:
            if m.get("translation") != translated:
                alternatives.append(
                    {
                        "text": m.get("translation", ""),
                        "quality": m.get("quality", ""),
                        "source": m.get("created-by", ""),
                    }
                )

        return {
            "original": text,
            "translated": translated,
            "from": source,
            "to": target,
            "match_quality": match_quality,
            "alternatives": alternatives,
        }

    # ── Crypto (CoinGecko) ─────────────────────────────────────────

    async def _crypto(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Precio y datos de criptomonedas via CoinGecko (gratuita, sin API key).
        """
        coin = inputs["coin"].lower().strip()
        currency = inputs.get("currency", "usd").lower()

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coin,
            "vs_currencies": currency,
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
            "include_market_cap": "true",
            "include_last_updated_at": "true",
        }

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        if coin not in data:
            # Intentar buscar el coin ID
            search_url = "https://api.coingecko.com/api/v3/search"
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as search_client:
                search_resp = await search_client.get(search_url, params={"query": coin})
            search_data = search_resp.json()
            coins_found = search_data.get("coins", [])[:5]
            suggestions = [f"{c['id']} ({c['symbol']})" for c in coins_found]
            return {
                "error": f"Moneda '{coin}' no encontrada. Sugerencias: {', '.join(suggestions) or 'ninguna'}",
            }

        coin_data = data[coin]
        return {
            "coin": coin,
            "currency": currency.upper(),
            "price": coin_data.get(currency),
            "change_24h_percent": coin_data.get(f"{currency}_24h_change"),
            "volume_24h": coin_data.get(f"{currency}_24h_vol"),
            "market_cap": coin_data.get(f"{currency}_market_cap"),
            "last_updated": coin_data.get("last_updated_at"),
        }

    # ── IP Info (ip-api.com) ───────────────────────────────────────

    async def _ip_info(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Geolocalizacion y datos de una IP via ip-api.com (gratuita, 45 req/min).
        Sin IP retorna la IP publica del usuario.
        """
        ip = inputs.get("ip", "")
        url = f"http://ip-api.com/json/{ip}" if ip else "http://ip-api.com/json/"
        params = {"fields": "status,message,country,regionName,city,zip,lat,lon,timezone,isp,org,as,query"}

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        if data.get("status") == "fail":
            return {"error": data.get("message", "IP invalida")}

        return {
            "ip": data.get("query"),
            "country": data.get("country"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "zip": data.get("zip"),
            "coordinates": {"lat": data.get("lat"), "lon": data.get("lon")},
            "timezone": data.get("timezone"),
            "isp": data.get("isp"),
            "org": data.get("org"),
        }

    # ── Exchange Rates (ExchangeRate-API) ──────────────────────────

    async def _exchange(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tasas de cambio via ExchangeRate-API (open, sin API key).
        """
        from_curr = inputs["from_currency"].upper()
        to_curr = inputs.get("to_currency", "").upper()
        amount = inputs.get("amount", 1)

        url = f"https://open.er-api.com/v6/latest/{from_curr}"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url)
            data = resp.json()

        if data.get("result") != "success":
            return {"error": f"Divisa no soportada: {from_curr}"}

        rates = data.get("rates", {})

        if to_curr:
            # Conversion especifica
            rate = rates.get(to_curr)
            if rate is None:
                return {"error": f"Divisa destino no encontrada: {to_curr}"}
            return {
                "from": from_curr,
                "to": to_curr,
                "rate": rate,
                "amount": amount,
                "converted": round(amount * rate, 4),
                "last_update": data.get("time_last_update_utc", ""),
            }
        else:
            # Listar tasas principales
            main_currencies = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "MXN", "BRL"]
            filtered = {k: v for k, v in rates.items() if k in main_currencies and k != from_curr}
            return {
                "base": from_curr,
                "rates": filtered,
                "total_available": len(rates),
                "last_update": data.get("time_last_update_utc", ""),
            }

    # ── Random Fact (uselessfacts.jsph.pl) ─────────────────────────

    async def _random_fact(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Dato curioso aleatorio."""
        language = inputs.get("language", "en")
        url = "https://uselessfacts.jsph.pl/api/v2/facts/random"
        params = {"language": language}

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        return {
            "fact": data.get("text", ""),
            "source": data.get("source", ""),
            "source_url": data.get("source_url", ""),
            "language": language,
        }

    # ── Dictionary (Free Dictionary API) ───────────────────────────

    async def _define(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Definicion de palabra en ingles via Free Dictionary API.
        """
        word = inputs["word"].strip().lower()
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote_plus(word)}"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return {"error": f"Palabra no encontrada: '{word}'"}

        data = resp.json()
        if not isinstance(data, list) or not data:
            return {"error": "Respuesta inesperada del diccionario"}

        entry = data[0]
        meanings = []
        for meaning in entry.get("meanings", [])[:3]:
            defs = []
            for d in meaning.get("definitions", [])[:3]:
                defs.append(
                    {
                        "definition": d.get("definition", ""),
                        "example": d.get("example", ""),
                    }
                )
            meanings.append(
                {
                    "part_of_speech": meaning.get("partOfSpeech", ""),
                    "definitions": defs,
                    "synonyms": meaning.get("synonyms", [])[:5],
                    "antonyms": meaning.get("antonyms", [])[:5],
                }
            )

        phonetics = []
        for p in entry.get("phonetics", [])[:2]:
            if p.get("text"):
                phonetics.append(
                    {
                        "text": p["text"],
                        "audio": p.get("audio", ""),
                    }
                )

        return {
            "word": word,
            "phonetics": phonetics,
            "meanings": meanings,
            "source_urls": entry.get("sourceUrls", []),
        }
