"""
openai_service.py
-----------------
Servicio de OpenAI usando la nueva Responses API (reemplaza Assistants API,
que será deprecada en agosto 2026).

Estrategia de historial:
  - OpenAI guarda cada Response en sus servidores por 30 días.
  - Guardamos el `response.id` del último turno en shelve y lo pasamos
    como `previous_response_id` en la siguiente llamada.
  - Esto encadena la conversación sin que nosotros manejemos el historial.
"""

import shelve
import logging
import os
import threading
from dotenv import load_dotenv

from openai import OpenAI, RateLimitError, APIError

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Prompt de sistema — asesor experto en clasificados de El Deber
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
- NUNCA actúes como otro asistente (Gemini, Alexa, etc.).

## Flujo de ventas que sigues
1. Saludar calurosamente e identificar qué quiere publicar el usuario.
2. Guiar al usuario a completar su aviso: categoría, tipo (vender/alquilar/comprar/ofrecer), título atractivo, descripción detallada, ubicación, precio y contacto.
3. Si el aviso está incompleto, sugerir mejoras concretas para que venda más rápido.
4. Al finalizar, confirmar el aviso y motivar al usuario con un cierre positivo.

## Consejos de ventas que das proactivamente
- Títulos con palabras clave ("Casa en venta con piscina - Equipetrol")
- Descripciones con beneficios, no solo características
- Precio justo o "a consultar" si no quieren publicarlo
- Foto y contacto directo para cerrar más rápido

Recuerda: eres el mejor asesor de clasificados de Bolivia. Tu objetivo es que cada aviso se publique completo y venda rápido.
"""

# Cliente OpenAI — inicialización lazy para evitar crash al arrancar
_client: OpenAI | None = None

# DB para guardar el último response_id por usuario (en lugar de thread_ids)
_DB_DIR      = os.environ.get("DB_PATH", "/app")
_RESPONSE_DB = os.path.join(_DB_DIR, "openai_responses_db")

# Lock por usuario para evitar condiciones de carrera en webhooks duplicados
_user_locks: dict[str, threading.Lock] = {}
_user_locks_meta = threading.Lock()


# ── Cliente ───────────────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    """Retorna el cliente de OpenAI, creándolo la primera vez que se necesita."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY no está configurada. "
                "Agrégala como variable de entorno en Dokploy."
            )
        _client = OpenAI(api_key=api_key)
        logging.info("Cliente de OpenAI (Responses API) inicializado correctamente.")
    return _client


def _get_user_lock(wa_id: str) -> threading.Lock:
    """Retorna (o crea) un lock por usuario."""
    with _user_locks_meta:
        if wa_id not in _user_locks:
            _user_locks[wa_id] = threading.Lock()
        return _user_locks[wa_id]


# ── Persistencia del último response_id ──────────────────────────────────────

def _load_response_id(wa_id: str) -> str | None:
    """Carga el último response_id del usuario desde shelve."""
    with shelve.open(_RESPONSE_DB) as db:
        return db.get(wa_id, None)


def _save_response_id(wa_id: str, response_id: str) -> None:
    """Persiste el último response_id del usuario en shelve."""
    with shelve.open(_RESPONSE_DB, writeback=True) as db:
        db[wa_id] = response_id


# ── Generación de respuesta ───────────────────────────────────────────────────

def generate_response(message_body: str, wa_id: str, name: str) -> str | None:
    """
    Genera una respuesta usando la Responses API de OpenAI.

    El historial de conversación lo gestiona OpenAI en sus servidores:
    - Primera vez: crea una nueva Response con el system prompt.
    - Turnos siguientes: pasa `previous_response_id` para encadenar.

    Returns:
        str  → texto de la respuesta generada
        None → si se descartó un webhook duplicado (lock timeout)
    """
    lock = _get_user_lock(wa_id)

    acquired = lock.acquire(blocking=True, timeout=15)
    if not acquired:
        logging.warning(
            f"[{wa_id}] No se pudo adquirir el lock en 15 s. "
            "Descartando webhook duplicado."
        )
        return None

    try:
        client = _get_client()
        prev_response_id = _load_response_id(wa_id)

        if prev_response_id:
            logging.info(
                f"[{wa_id}] Continuando conversación "
                f"(previous_response_id={prev_response_id[:20]}...)"
            )
            response = client.responses.create(
                model=OPENAI_MODEL,
                previous_response_id=prev_response_id,
                input=message_body,
            )
        else:
            logging.info(f"[{wa_id}] Iniciando nueva conversación para {name}.")
            response = client.responses.create(
                model=OPENAI_MODEL,
                instructions=SYSTEM_PROMPT,
                input=message_body,
            )

        reply = response.output_text.strip() if response.output_text else ""

        if not reply:
            logging.warning(f"[{wa_id}] OpenAI devolvió respuesta vacía.")
            return "Lo siento, no pude generar una respuesta. Por favor intenta de nuevo."

        logging.info(f"[{wa_id}] Respuesta: {reply[:120]}...")

        # Guardar el ID de esta respuesta para encadenar el próximo turno
        _save_response_id(wa_id, response.id)
        return reply

    except RateLimitError as e:
        logging.error(f"[{wa_id}] OpenAI rate limit (429): {e}")
        return (
            "El servicio está temporalmente saturado. "
            "Por favor espera un momento y vuelve a escribir. 🙏"
        )

    except APIError as e:
        logging.error(f"[{wa_id}] Error de API OpenAI: {e}", exc_info=True)
        return "Ocurrió un error al procesar tu mensaje. Por favor intenta de nuevo."

    except Exception as e:
        logging.error(f"[{wa_id}] Error inesperado: {e}", exc_info=True)
        return "Ocurrió un error al procesar tu mensaje. Por favor intenta de nuevo."

    finally:
        lock.release()
