from typing import Dict, Any, List
import json
import logging

from .utils import extract_json_array_from_text

logger = logging.getLogger("origin.mind.planner")


SKILL_DOCS = {
    "web_search": {
        "description": "Busca información en la web usando DuckDuckGo",
        "inputs": {"query": "string - término de búsqueda"},
        "example": {"skill": "web_search", "inputs": {"query": "Python async programming"}},
    },
    "datetime": {
        "description": "Obtiene fecha/hora, convierte zonas horarias, calcula diferencias",
        "actions": ["now", "convert", "diff", "add"],
        "inputs_now": {"action": "now", "timezone": "string (ej: UTC o 'local')"},
        "inputs_convert": {"action": "convert", "from_tz": "string", "to_tz": "string", "dt": "string ISO"},
        "inputs_diff": {"action": "diff", "dt1": "ISO", "dt2": "ISO"},
        "inputs_add": {"action": "add", "days": "int", "hours": "int", "minutes": "int"},
        "example": {"skill": "datetime", "inputs": {"action": "now", "timezone": "UTC"}},
    },
    "calculator": {
        "description": "Evalúa expresiones matemáticas de forma segura (sin eval)",
        "inputs": {"expression": "string - expresión matemática"},
        "example": {"skill": "calculator", "inputs": {"expression": "2 + 2 * 5"}},
    },
    "system_info": {
        "description": "Obtiene información del sistema: OS, CPU, RAM, disco, procesos",
        "inputs": {"action": "string - 'os', 'cpu', 'memory', 'disk', 'processes'"},
        "example": {"skill": "system_info", "inputs": {"action": "memory"}},
    },
    "web_scraper": {
        "description": "Extrae contenido de páginas web: texto, enlaces, tablas",
        "actions": ["extract", "text", "links", "tables", "select", "meta"],
        "inputs_extract": {"action": "extract", "url": "string"},
        "inputs_text": {"action": "text", "url": "string"},
        "inputs_links": {"action": "links", "url": "string"},
        "inputs_tables": {"action": "tables", "url": "string"},
        "inputs_select": {"action": "select", "url": "string", "selector": "string CSS selector"},
        "inputs_meta": {"action": "meta", "url": "string"},
        "example": {"skill": "web_scraper", "inputs": {"action": "text", "url": "https://example.com"}},
    },
    "shell": {
        "description": "Ejecuta comandos del sistema: run, open, kill, list, which, env",
        "actions": ["run", "open", "kill", "list", "which", "env"],
        "inputs_run": {"action": "run", "command": "string - comando a ejecutar"},
        "inputs_open": {"action": "open", "app": "string - nombre de app (chrome, notepad, youtube, calculator, etc)"},
        "inputs_kill": {"action": "kill", "target": "string - nombre del proceso"},
        "inputs_list": {"action": "list"},
        "inputs_which": {"action": "which", "command": "string - comando a buscar"},
        "inputs_env": {"action": "env"},
        "CRITICO": "Para 'open' usar parametro 'app' (no 'command', no 'target')",
        "example_run": {"skill": "shell", "inputs": {"action": "run", "command": "dir"}},
        "example_open": {"skill": "shell", "inputs": {"action": "open", "app": "chrome"}},
    },
    "external_apis": {
        "description": "Accede a APIs públicas: clima, noticias, traducción, criptomonedas, divisas",
        "actions": ["weather", "translate", "crypto", "exchange", "news", "ip_info", "define", "random_fact"],
        "inputs_weather": {"action": "weather", "city": "string OR latitude, longitude"},
        "inputs_translate": {"action": "translate", "text": "string", "from_lang": "código", "to_lang": "código"},
        "inputs_crypto": {"action": "crypto", "symbol": "string (BTC, ETH, etc)"},
        "inputs_exchange": {"action": "exchange", "from_curr": "USD", "to_curr": "EUR"},
        "inputs_news": {"action": "news", "query": "string"},
        "inputs_ip_info": {"action": "ip_info"},
        "inputs_define": {"action": "define", "word": "string"},
        "inputs_random_fact": {"action": "random_fact"},
        "example": {"skill": "external_apis", "inputs": {"action": "weather", "city": "Madrid"}},
    },
    "file_manager": {
        "description": "Lee, escribe, busca y gestiona archivos locales",
        "actions": [
            "read",
            "write",
            "append",
            "list",
            "search",
            "grep",
            "info",
            "move",
            "copy",
            "mkdir",
            "delete",
            "tree",
        ],
        "inputs_read": {"action": "read", "path": "string"},
        "inputs_write": {"action": "write", "path": "string", "content": "string"},
        "inputs_list": {"action": "list", "path": "string"},
        "inputs_search": {"action": "search", "directory": "string", "pattern": "string"},
        "inputs_grep": {"action": "grep", "pattern": "regex", "directory": "string", "file_pattern": "*.py"},
        "example": {"skill": "file_manager", "inputs": {"action": "read", "path": "/path/to/file.txt"}},
    },
    "memory_compaction": {
        "description": "Analiza, detecta duplicados y compacta memorias de Origin",
        "actions": ["stats", "duplicates", "cluster", "compact", "plan", "prune"],
        "inputs_stats": {"action": "stats"},
        "inputs_duplicates": {"action": "duplicates", "threshold": "0.92"},
        "inputs_cluster": {"action": "cluster", "threshold": "0.75"},
        "inputs_compact": {"action": "compact", "strategy": "progressive|key_facts|merge"},
        "example": {"skill": "memory_compaction", "inputs": {"action": "stats"}},
    },
    "code_doctor": {
        "description": "Diagnostica la salud de proyectos React: score, errores, performance, seguridad",
        "actions": ["scan", "score", "diagnostics", "diff", "staged", "compare"],
        "inputs_scan": {"action": "scan", "path": "string (directorio proyecto)"},
        "inputs_score": {"action": "score", "path": "string"},
        "example": {"skill": "code_doctor", "inputs": {"action": "scan", "path": "./frontend"}},
    },
    "vision": {
        "description": "Captura y analiza la pantalla: ver que hay abierto, leer texto, buscar elementos",
        "actions": ["screenshot", "analyze", "find", "read_text", "compare", "monitor"],
        "inputs_screenshot": {"action": "screenshot"},
        "inputs_analyze": {"action": "analyze", "question": "string - pregunta sobre lo que se ve (opcional)"},
        "inputs_find": {"action": "find", "description": "string - que buscar en pantalla"},
        "inputs_read_text": {"action": "read_text"},
        "inputs_monitor": {"action": "monitor", "region": "[x, y, width, height]"},
        "CRITICO": "Para VER la pantalla usar vision. Para ACTUAR sobre ella usar ui_automation.",
        "example_screenshot": {"skill": "vision", "inputs": {"action": "screenshot"}},
        "example_analyze": {
            "skill": "vision",
            "inputs": {"action": "analyze", "question": "Que aplicaciones estan abiertas?"},
        },
    },
    "ui_automation": {
        "description": "Controla mouse, teclado y ventanas del sistema operativo",
        "actions": [
            "click",
            "double_click",
            "right_click",
            "type",
            "hotkey",
            "press",
            "scroll",
            "move",
            "drag",
            "window",
            "cursor_info",
        ],
        "inputs_click": {"action": "click", "x": "int - coordenada X", "y": "int - coordenada Y"},
        "inputs_type": {"action": "type", "text": "string - texto a escribir"},
        "inputs_hotkey": {"action": "hotkey", "keys": "string - atajo (ej: ctrl+c, alt+tab, win+d)"},
        "inputs_press": {"action": "press", "key": "string - tecla (enter, tab, escape, etc)"},
        "inputs_scroll": {"action": "scroll", "direction": "up|down", "amount": "int (default 3)"},
        "inputs_move": {"action": "move", "x": "int", "y": "int"},
        "inputs_drag": {"action": "drag", "start_x": "int", "start_y": "int", "end_x": "int", "end_y": "int"},
        "inputs_window": {
            "action": "window",
            "sub_action": "list|focus|minimize|maximize|close",
            "title": "string - titulo ventana",
        },
        "inputs_cursor_info": {"action": "cursor_info"},
        "CRITICO": "Para clicks SIEMPRE usar coordenadas x,y. Combinar con vision para saber DONDE clickar.",
        "example_click": {"skill": "ui_automation", "inputs": {"action": "click", "x": 500, "y": 300}},
        "example_type": {"skill": "ui_automation", "inputs": {"action": "type", "text": "hello world"}},
        "example_hotkey": {"skill": "ui_automation", "inputs": {"action": "hotkey", "keys": "ctrl+c"}},
        "example_window": {"skill": "ui_automation", "inputs": {"action": "window", "sub_action": "list"}},
    },
    "notification": {
        "description": "Envía notificaciones toast de Windows, reproduce sonidos y alertas",
        "actions": ["toast", "sound", "alert", "history"],
        "inputs_toast": {
            "action": "toast",
            "title": "Título de la notificación",
            "message": "Texto del cuerpo",
            "icon": "info|success|warning|error",
        },
        "inputs_sound": {"action": "sound", "preset": "info|success|warning|error|alert|startup|beep"},
        "inputs_alert": {
            "action": "alert",
            "title": "Título urgente",
            "message": "Texto",
            "sound_preset": "preset sonido",
        },
        "inputs_history": {"action": "history"},
        "example_toast": {
            "skill": "notification",
            "inputs": {"action": "toast", "title": "Listo", "message": "Tarea completada", "icon": "success"},
        },
        "example_sound": {"skill": "notification", "inputs": {"action": "sound", "preset": "success"}},
        "example_alert": {
            "skill": "notification",
            "inputs": {"action": "alert", "title": "CPU Alta", "message": "Uso al 95%", "sound_preset": "warning"},
        },
    },
    "voice": {
        "description": "Escucha por micrófono (STT) y habla con voz natural (TTS Edge)",
        "actions": ["listen", "speak", "voices", "set_voice", "status"],
        "inputs_listen": {"action": "listen", "timeout": "segundos (default 8)", "language": "es-ES|en-US|fr-FR"},
        "inputs_speak": {
            "action": "speak",
            "text": "texto a decir en voz alta",
            "preset": "origin|british|female_es|origin_en",
        },
        "inputs_voices": {"action": "voices", "language": "filtro: es|en|fr"},
        "inputs_set_voice": {"action": "set_voice", "preset": "origin|british|female_es|origin_en|female_en"},
        "inputs_status": {"action": "status"},
        "CRITICO": "Para 'speak' SIEMPRE pasar 'text'. Para 'listen' basta con action='listen'.",
        "example_speak": {"skill": "voice", "inputs": {"action": "speak", "text": "Hola Vadim"}},
        "example_listen": {"skill": "voice", "inputs": {"action": "listen", "language": "es-ES"}},
    },
    "app_integrations": {
        "description": "Integra con Spotify, Email y webhooks externos",
        "actions": [
            "spotify_play",
            "spotify_pause",
            "spotify_next",
            "spotify_prev",
            "spotify_search",
            "spotify_now_playing",
            "spotify_volume",
            "email_send",
            "webhook_send",
            "status",
        ],
        "inputs_spotify_play": {"action": "spotify_play", "query": "nombre de canción/artista a reproducir"},
        "inputs_spotify_pause": {"action": "spotify_pause"},
        "inputs_spotify_next": {"action": "spotify_next"},
        "inputs_spotify_search": {"action": "spotify_search", "query": "término", "type": "track|artist|album"},
        "inputs_spotify_now_playing": {"action": "spotify_now_playing"},
        "inputs_spotify_volume": {"action": "spotify_volume", "volume": "0-100"},
        "inputs_email_send": {"action": "email_send", "to": "email destino", "subject": "asunto", "body": "contenido"},
        "inputs_webhook_send": {"action": "webhook_send", "url": "URL", "payload": "dict datos", "method": "POST|GET"},
        "inputs_status": {"action": "status"},
        "CRITICO": "Spotify necesita SPOTIPY_CLIENT_ID/SECRET en .env. Email necesita SMTP_USER/PASS. Webhooks siempre disponibles.",  # noqa: E501
        "example_play": {
            "skill": "app_integrations",
            "inputs": {"action": "spotify_play", "query": "Bohemian Rhapsody"},
        },
        "example_email": {
            "skill": "app_integrations",
            "inputs": {"action": "email_send", "to": "user@mail.com", "subject": "Hola", "body": "Desde Origin"},
        },
    },
    "monitor": {
        "description": "Monitorea CPU, RAM, disco y red en background con alertas automáticas",
        "actions": ["start", "stop", "status", "thresholds", "history", "snapshot"],
        "inputs_start": {
            "action": "start",
            "interval": "segundos entre checks (default 15)",
            "thresholds": "(opcional) dict",
        },
        "inputs_stop": {"action": "stop"},
        "inputs_status": {"action": "status"},
        "inputs_thresholds": {"action": "thresholds", "set": "(opcional) dict ej: {'cpu_percent': 80}"},
        "inputs_history": {"action": "history", "limit": "número alertas (default 20)"},
        "inputs_snapshot": {"action": "snapshot"},
        "CRITICO": "Para 'start' basta con action='start'. snapshot da métricas instantáneas sin iniciar monitoreo.",
        "example_start": {"skill": "monitor", "inputs": {"action": "start", "interval": 30}},
        "example_snapshot": {"skill": "monitor", "inputs": {"action": "snapshot"}},
        "example_status": {"skill": "monitor", "inputs": {"action": "status"}},
    },
    "wake_word": {
        "description": "Activa/desactiva el modo siempre-escuchando (Siri/Alexa style). Detecta 'Hey Origin' y procesa comandos de voz.",  # noqa: E501
        "actions": ["start", "stop", "status"],
        "inputs_start": {"action": "start", "language": "es-ES (opcional)"},
        "inputs_stop": {"action": "stop"},
        "inputs_status": {"action": "status"},
        "example_start": {"skill": "wake_word", "inputs": {"action": "start"}},
        "example_stop": {"skill": "wake_word", "inputs": {"action": "stop"}},
    },
    "camera": {
        "description": "Accede a la webcam para capturar y analizar con visión IA lo que el usuario muestra físicamente.",  # noqa: E501
        "actions": ["capture", "analyze", "analyze_frame", "list_cameras", "status"],
        "inputs_capture": {"action": "capture", "camera_index": "int (default 0)"},
        "inputs_analyze": {
            "action": "analyze",
            "question": "string (opcional) - qué analizar",
            "camera_index": "int (default 0)",
        },
        "inputs_analyze_frame": {
            "action": "analyze_frame",
            "frame_b64": "string base64 JPEG",
            "question": "string (opcional)",
        },
        "inputs_list_cameras": {"action": "list_cameras"},
        "inputs_status": {"action": "status"},
        "CRITICO": "Para ver lo que el usuario muestra usar 'analyze' (captura webcam) o 'analyze_frame' (frame del browser).",  # noqa: E501
        "example_analyze": {
            "skill": "camera",
            "inputs": {"action": "analyze", "question": "¿Qué objeto me estás mostrando?"},
        },
        "example_capture": {"skill": "camera", "inputs": {"action": "capture"}},
    },
    "system_tray": {
        "description": "Integración con Windows: icono en bandeja del sistema, hotkey global Ctrl+Alt+J, arranque con Windows.",  # noqa: E501
        "actions": ["start", "stop", "status", "notify", "set_startup", "open_ui"],
        "inputs_start": {"action": "start"},
        "inputs_stop": {"action": "stop"},
        "inputs_notify": {"action": "notify", "title": "string", "message": "string"},
        "inputs_set_startup": {"action": "set_startup", "enable": "bool"},
        "inputs_open_ui": {"action": "open_ui"},
        "inputs_status": {"action": "status"},
        "example_start": {"skill": "system_tray", "inputs": {"action": "start"}},
        "example_notify": {
            "skill": "system_tray",
            "inputs": {"action": "notify", "title": "Origin", "message": "Sistema listo"},
        },
        "example_startup": {"skill": "system_tray", "inputs": {"action": "set_startup", "enable": True}},
    },
    "os_control": {
        "description": "Control nativo de Windows: volumen, brillo, energía (lock/sleep/restart/shutdown), batería, WiFi, displays.",  # noqa: E501
        "actions": [
            "volume",
            "brightness",
            "lock",
            "sleep",
            "hibernate",
            "restart",
            "shutdown",
            "battery",
            "network",
            "wifi_list",
            "wifi_connect",
            "displays",
            "audio_devices",
            "power_plan",
            "idle_time",
        ],
        "inputs_volume": {
            "action": "volume",
            "op": "get|set|mute|unmute|toggle_mute|up|down",
            "level": "0-100 (si op=set)",
            "step": "int (default 10)",
        },
        "inputs_brightness": {
            "action": "brightness",
            "op": "get|set|up|down|list",
            "level": "0-100 (si op=set)",
            "display": "int (opcional)",
        },
        "inputs_lock": {"action": "lock"},
        "inputs_sleep": {"action": "sleep"},
        "inputs_restart": {"action": "restart", "confirm": "true (obligatorio)", "delay_sec": "int (default 5)"},
        "inputs_shutdown": {"action": "shutdown", "confirm": "true (obligatorio)", "delay_sec": "int (default 5)"},
        "inputs_battery": {"action": "battery"},
        "inputs_network": {"action": "network"},
        "inputs_wifi_list": {"action": "wifi_list"},
        "inputs_wifi_connect": {"action": "wifi_connect", "ssid": "string nombre red"},
        "inputs_displays": {"action": "displays"},
        "inputs_audio_devices": {"action": "audio_devices"},
        "inputs_power_plan": {"action": "power_plan", "op": "get|list|set", "guid": "GUID si op=set"},
        "inputs_idle_time": {"action": "idle_time"},
        "CRITICO": "restart/shutdown REQUIEREN confirm=true. Volumen/brillo levels son 0-100.",
        "example_volume_set": {"skill": "os_control", "inputs": {"action": "volume", "op": "set", "level": 50}},
        "example_volume_down": {"skill": "os_control", "inputs": {"action": "volume", "op": "down", "step": 20}},
        "example_brightness": {"skill": "os_control", "inputs": {"action": "brightness", "op": "set", "level": 70}},
        "example_lock": {"skill": "os_control", "inputs": {"action": "lock"}},
        "example_battery": {"skill": "os_control", "inputs": {"action": "battery"}},
    },
    "clipboard": {
        "description": "Lee/escribe el portapapeles de Windows. Útil para 'traduce esto', 'explica el código que copié', etc.",  # noqa: E501
        "actions": ["read", "write", "clear", "history", "watch_start", "watch_stop", "status"],
        "inputs_read": {"action": "read"},
        "inputs_write": {"action": "write", "text": "string a copiar"},
        "inputs_clear": {"action": "clear"},
        "inputs_history": {"action": "history", "limit": "int (default 10, max 50)"},
        "inputs_watch_start": {"action": "watch_start", "interval": "float seg (default 0.5)"},
        "inputs_watch_stop": {"action": "watch_stop"},
        "inputs_status": {"action": "status"},
        "CRITICO": "Para tareas tipo 'traduce esto' o 'explica esto', primero usar 'read' del clipboard.",
        "example_read": {"skill": "clipboard", "inputs": {"action": "read"}},
        "example_write": {"skill": "clipboard", "inputs": {"action": "write", "text": "Hola Vadim"}},
    },
    "active_window": {
        "description": "Conciencia de contexto: qué app/ventana tienes en foco ahora. Permite respuestas contextuales.",
        "actions": ["current", "list_visible", "track_start", "track_stop", "history", "time_by_app", "status"],
        "inputs_current": {"action": "current"},
        "inputs_list_visible": {"action": "list_visible", "limit": "int (default 30)"},
        "inputs_track_start": {"action": "track_start", "interval": "float seg (default 1.0)"},
        "inputs_track_stop": {"action": "track_stop"},
        "inputs_history": {"action": "history", "limit": "int (default 20)"},
        "inputs_time_by_app": {"action": "time_by_app"},
        "inputs_status": {"action": "status"},
        "CRITICO": "Para 'qué estoy haciendo' o respuestas contextuales, usar 'current'.",
        "example_current": {"skill": "active_window", "inputs": {"action": "current"}},
        "example_track": {"skill": "active_window", "inputs": {"action": "track_start", "interval": 2.0}},
    },
    "browser": {
        "description": "Automatización de navegador (Playwright/Chromium): navega, clickea, escribe, extrae texto, ejecuta JS.",  # noqa: E501
        "actions": ["navigate", "click", "type", "extract", "screenshot", "javascript", "close"],
        "inputs_navigate": {"action": "navigate", "url": "string", "timeout": "ms (default 30000)"},
        "inputs_click": {"action": "click", "text": "string (visible) OR selector: 'CSS selector'"},
        "inputs_type": {"action": "type", "selector": "CSS selector", "text": "string", "delay": "ms entre teclas"},
        "inputs_extract": {"action": "extract", "selector": "CSS (default body)"},
        "inputs_screenshot": {"action": "screenshot"},
        "inputs_javascript": {"action": "javascript", "code": "JS para ejecutar en la página"},
        "inputs_close": {"action": "close"},
        "CRITICO": "Para web interactiva (login, formularios, SPAs) preferir browser sobre web_scraper. Requiere Playwright instalado.",  # noqa: E501
        "example_navigate": {"skill": "browser", "inputs": {"action": "navigate", "url": "https://github.com"}},
        "example_click": {"skill": "browser", "inputs": {"action": "click", "text": "Sign in"}},
        "example_extract": {"skill": "browser", "inputs": {"action": "extract", "selector": "article"}},
    },
    "eventlog": {
        "description": "Lee y consulta el Windows Event Log (System, Application, Security).",
        "actions": ["query", "tail", "stats", "watch_start", "watch_stop"],
        "inputs_query": {
            "action": "query",
            "log": "System|Application|Security",
            "level": "Critical|Error|Warning|Information",
            "limit": "int (default 50)",
        },
        "inputs_tail": {"action": "tail", "log": "string", "n": "int (default 20)"},
        "inputs_stats": {"action": "stats", "log": "string"},
        "inputs_watch_start": {"action": "watch_start", "log": "string", "level": "string"},
        "inputs_watch_stop": {"action": "watch_stop"},
        "CRITICO": "Para diagnosticar crashes, errores de drivers, o eventos de seguridad — usar query con level=Error.",  # noqa: E501
        "example_query": {
            "skill": "eventlog",
            "inputs": {"action": "query", "log": "System", "level": "Error", "limit": 20},
        },
        "example_tail": {"skill": "eventlog", "inputs": {"action": "tail", "log": "Application", "n": 10}},
    },
}


class Planner:
    """Paso 2: Genera un plan de 1-5 pasos para ejecutar skills."""

    def __init__(self, llm_router, profile_mgr, available_skills: Dict[str, str]):
        self.llm_router = llm_router
        self.profile_mgr = profile_mgr
        self.available_skills = available_skills

    async def generate(self, intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        skills_reference = self._build_skills_reference()

        required_skills = intent.get("required_skills", [])
        if required_skills:
            suggested_skills_text = f"\n\nSuggested skills from intent parsing: {', '.join(required_skills)}\n(But you can use others if better suited)"  # noqa: E501
        else:
            suggested_skills_text = "\nNo specific skills identified. Choose skills that best fit the task."

        system_msg = f"""Eres {self.profile_mgr.origin_identity.get('nombre')}, asistente técnico.

Tu tarea es generar un plan EJECUTABLE de 1-5 pasos concretos.

DOCUMENTACIÓN COMPLETA DE SKILLS DISPONIBLES:
{skills_reference}

INSTRUCCIONES CRÍTICAS:
1. SOLO usa skills que están en la documentación arriba (o skill vacío para respuesta directa)
2. Para cada skill, usa EXACTAMENTE los parámetros mostrados en la documentación
3. Los valores de "inputs" deben ser ESPECÍFICOS para la tarea del usuario, NO placeholders
4. Si es una tarea simple, puedes omitir skill (usar "skill": "")
5. Responde SOLO en JSON array, sin markdown ni explicaciones

FORMATO REQUERIDO para cada paso:
{{
  "step": 1,
  "action": "Descripción clara de qué hace este paso",
  "skill": "nombre_skill_exacto_o_vacio",
  "inputs": {{"param_name": "valor_específico"}},
  "expected_output": "Qué resultado se espera"
}}
"""

        plan_prompt = f"""Intención del usuario: {intent.get('intent')}
Tipo de tarea: {intent.get('task_type')}
{suggested_skills_text}

Genera un plan de 1-5 pasos en JSON array puro (sin markdown, sin código extra).

RECUERDA: Los inputs deben usar los nombres EXACTOS de parámetros de la documentación.
Ejemplo:
[
  {{"step": 1, "action": "get current time", "skill": "datetime", "inputs": {{"action": "now", "timezone": "Europe/Madrid"}}, "expected_output": "current date and time"}}  # noqa: E501
]
"""

        result = await self.llm_router.call_llm(
            prompt=plan_prompt, system_message=system_msg, temperature=0.5, task_type="planning"
        )

        if not result.get("success"):
            return [
                {
                    "step": 1,
                    "action": intent.get("intent"),
                    "skill": "",
                    "inputs": {},
                    "expected_output": "Respuesta directa",
                }
            ]

        plan_data = extract_json_array_from_text(result.get("content", ""))
        if plan_data:
            validated_plan = []
            for step in plan_data[:5]:
                if isinstance(step, dict):
                    skill_name = step.get("skill", "")
                    if skill_name == "" or skill_name in self.available_skills:
                        validated_plan.append(step)
                    else:
                        logger.warning(f"Unknown skill in plan: {skill_name}, treating as direct response")
                        step["skill"] = ""
                        validated_plan.append(step)
            return (
                validated_plan
                if validated_plan
                else [
                    {
                        "step": 1,
                        "action": intent.get("intent"),
                        "skill": "",
                        "inputs": {},
                        "expected_output": "Respuesta",
                    }
                ]
            )

        return [{"step": 1, "action": intent.get("intent"), "skill": "", "inputs": {}, "expected_output": "Respuesta"}]

    def _build_skills_reference(self) -> str:
        """Construye la referencia de skills SOLO para las disponibles.

        Antes inyectaba TODOS los SKILL_DOCS (~6KB tokens) en cada plan, incluso
        si la skill no estaba registrada. Ahora filtra por available_skills y
        además limita ejemplos al primero (los demás son ruido).
        """
        parts = []
        candidate_names = [n for n in sorted(SKILL_DOCS.keys()) if n in self.available_skills]

        # Si tenemos skills registradas sin doc, listarlas con su descripción cruda
        undocumented = [n for n in sorted(self.available_skills.keys()) if n not in SKILL_DOCS]

        for skill_name in candidate_names:
            doc = SKILL_DOCS[skill_name]
            parts.append(f"\n{skill_name.upper()}:")
            parts.append(f"  Descripción: {doc.get('description')}")
            if "actions" in doc:
                parts.append(f"  Acciones válidas: {', '.join(doc['actions'])}")
            if "CRITICO" in doc:
                parts.append(f"  CRÍTICO: {doc['CRITICO']}")

            for key, value in sorted(doc.items()):
                if key.startswith("inputs"):
                    action_name = key.replace("inputs_", "").replace("inputs", "").strip("_") or "default"
                    parts.append(f"  Parámetros '{action_name}': {json.dumps(value, ensure_ascii=False)}")

            # Solo el primer ejemplo — el resto inflaba el prompt sin aportar
            example_keys = sorted(k for k in doc.keys() if k.startswith("example"))
            if example_keys:
                example_type = example_keys[0].replace("example_", "").replace("example", "").strip("_") or "default"
                parts.append(f"  Ejemplo '{example_type}': {json.dumps(doc[example_keys[0]], ensure_ascii=False)}")

        if undocumented:
            parts.append("\nOTRAS SKILLS DISPONIBLES (sin doc detallada — inputs típicos: action, params):")
            for name in undocumented:
                parts.append(f"  - {name}: {self.available_skills.get(name, '')[:120]}")

        return "\n".join(parts)
