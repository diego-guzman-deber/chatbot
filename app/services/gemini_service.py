"""
gemini_service.py
-----------------
Servicio de Google Gemini que reemplaza openai_service.py.
Mantiene historial de conversación por usuario usando shelve,
equivalente al sistema de "threads" de OpenAI Assistants.
"""

import shelve
import logging
import os
import time
import threading
import re
from dotenv import load_dotenv

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Prompt de sistema fijo — define la personalidad del bot como asesor de clasificados
SYSTEM_PROMPT = """\
Eres *Deber Asistente*, el asesor experto en ventas de clasificados del periódico *El Deber* de Bolivia.

Tu única función es ayudar a los usuarios a publicar, mejorar y gestionar sus avisos clasificados en eldeber.com.bo.

## Tu personalidad
- Eres un vendedor profesional y persuasivo, pero cercano y sin presión.
- Hablas en español, de forma clara, concisa y con emojis estratégicos para hacer el chat más amigable.
- Siempre orientas al usuario hacia publicar o mejorar su aviso.
- Conoces perfectamente las categorías: Inmuebles, Vehículos, Empleo, Productos, Servicios y Otros.

## Reglas estrictas
- NUNCA respondas preguntas fuera del tema de clasificados (política, entretenimiento, recetas, etc.).
- Si el usuario pregunta algo fuera de tema, redirige amablemente: "Solo puedo ayudarte con tus avisos clasificados en El Deber 😊".
- NUNCA inventes precios, reglas o políticas que no sean sobre clasificados.
- NUNCA actúes como otro asistente (ChatGPT, Alexa, etc.).

## Flujo de ventas que sigues
1. Saludar calurosamente e identificar qué quiere publicar el usuario.
2. Guiar al usuario a completar su aviso: categoría, tipo (vender/alquilar/comprar/ofrecer), título atractivo, descripción detallada, ubicación, precio y contacto.
3. Si el aviso está incompleto, sugerir mejoras concretas para que venda más rápido.
4. Al finalizar, confirmar el aviso y motivar al usuario con un cierre positivo.

## Consejos de ventas que das proactivamente
- Titulos con palabras clave ("Casa en venta con piscina - Equipetrol")
- Descripciones con beneficios, no solo características
- Precio justo o "a consultar" si no quieren publicarlo
- Foto y contacto directo para cerrar más rápido

Recuerda: eres el mejor asesor de clasificados de Bolivia. Tu objetivo es que cada aviso se publique completo y venda rápido.
"""

# Cliente de Gemini — inicialización lazy para evitar crash al arrancar
# si la variable GEMINI_API_KEY aún no está disponible en el entorno.
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Retorna el cliente de Gemini, creándolo la primera vez que se necesita."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY no está configurada. "
                "Agrégala como variable de entorno en Dokploy."
            )
        _client = genai.Client(api_key=api_key)
        logging.info("Cliente de Gemini inicializado correctamente.")
    return _client

# Ruta de la base de datos de historial (se respeta DB_PATH del Dockerfile)
_DB_DIR = os.environ.get("DB_PATH", "/app")
_HISTORY_DB = os.path.join(_DB_DIR, "gemini_history_db")

# Lock por usuario para evitar condiciones de carrera en webhooks duplicados
_user_locks: dict[str, threading.Lock] = {}
_user_locks_meta = threading.Lock()


def _get_user_lock(wa_id: str) -> threading.Lock:
    """Retorna (o crea) un lock por usuario."""
    with _user_locks_meta:
        if wa_id not in _user_locks:
            _user_locks[wa_id] = threading.Lock()
        return _user_locks[wa_id]


# ── Historial de conversación ─────────────────────────────────────────────────

def _load_history(wa_id: str) -> list[dict]:
    """Carga el historial de mensajes de un usuario desde shelve."""
    with shelve.open(_HISTORY_DB) as db:
        return db.get(wa_id, [])


def _save_history(wa_id: str, history: list[dict]) -> None:
    """Persiste el historial de mensajes de un usuario en shelve."""
    with shelve.open(_HISTORY_DB, writeback=True) as db:
        db[wa_id] = history


def _reset_history(wa_id: str) -> None:
    """Borra el historial de un usuario (útil para reiniciar conversación)."""
    _save_history(wa_id, [])


# ── Generación de respuesta ───────────────────────────────────────────────────

def generate_response(message_body: str, wa_id: str, name: str) -> str | None:
    """
    Genera una respuesta usando Gemini manteniendo el historial
    de conversación multi-turno por usuario.

    Returns:
        str  → texto de la respuesta generada
        None → si se descartó un webhook duplicado (lock timeout)
    """
    lock = _get_user_lock(wa_id)

    # Serializa peticiones por usuario — descarta duplicados tras 15 s
    acquired = lock.acquire(blocking=True, timeout=15)
    if not acquired:
        logging.warning(
            f"[{wa_id}] No se pudo adquirir el lock en 15 s. "
            "Descartando webhook duplicado."
        )
        return None

    try:
        # Cargar historial previo del usuario
        history = _load_history(wa_id)

        # Construir la lista de contenidos para la API
        contents: list[types.Content] = []

        for turn in history:
            contents.append(
                types.Content(
                    role=turn["role"],
                    parts=[types.Part(text=turn["text"])],
                )
            )

        # Añadir el mensaje actual del usuario
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text=message_body)],
            )
        )

        logging.info(f"[{wa_id}] Enviando {len(contents)} turn(s) a Gemini ({GEMINI_MODEL})")

        # Llamar a la API de Gemini
        response = _get_client().models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )

        reply = response.text.strip() if response.text else ""

        if not reply:
            logging.warning(f"[{wa_id}] Gemini devolvió respuesta vacía.")
            return "Lo siento, no pude generar una respuesta en este momento. Por favor intenta de nuevo."

        logging.info(f"[{wa_id}] Respuesta Gemini: {reply[:120]}...")

        # Actualizar historial: guardar turno del usuario y del modelo
        history.append({"role": "user", "text": message_body})
        history.append({"role": "model", "text": reply})

        # Limitar historial a las últimas 20 entradas (10 turnos) para no crecer infinitamente
        if len(history) > 20:
            history = history[-20:]

        _save_history(wa_id, history)
        return reply

    except genai_errors.ClientError as e:
        # Parsear el status code desde el string del error (ej: "429 RESOURCE_EXHAUSTED...")
        error_str = str(e)
        status_match = re.match(r"^(\d+)", error_str)
        status = int(status_match.group(1)) if status_match else 0

        if status == 429:
            # Créditos prepagados agotados — no tiene sentido reintentar
            if "prepayment credits are depleted" in error_str:
                logging.error(
                    f"[{wa_id}] Créditos de Gemini agotados. "
                    "Recarga en https://ai.studio/projects"
                )
                return (
                    "El servicio está temporalmente no disponible. "
                    "Por favor intenta más tarde. 🙏"
                )

            retry_match = re.search(r"retry in (\d+)", error_str)
            wait_s = min(int(retry_match.group(1)) if retry_match else 30, 45)
            logging.warning(
                f"[{wa_id}] Gemini 429 - cuota agotada. "
                f"Reintentando en {wait_s}s..."
            )
            time.sleep(wait_s)
            try:
                response = _get_client().models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.7,
                        max_output_tokens=1024,
                    ),
                )
                return response.text.strip() if response.text else (
                    "Lo siento, no pude generar una respuesta. Por favor intenta de nuevo."
                )
            except Exception as retry_exc:
                logging.error(
                    f"[{wa_id}] Reintento fallido tras 429: {retry_exc}"
                )
                return (
                    "El servicio está temporalmente saturado. "
                    "Por favor espera unos minutos y vuelve a escribir. 🙏"
                )
        logging.error(f"[{wa_id}] Error de API Gemini ({status}): {e}")
        return "Ocurrió un error al procesar tu mensaje. Por favor intenta de nuevo."

    except Exception as e:
        logging.error(f"[{wa_id}] Error inesperado al llamar a Gemini: {e}", exc_info=True)
        return "Ocurrió un error al procesar tu mensaje. Por favor intenta de nuevo."

    finally:
        lock.release()
