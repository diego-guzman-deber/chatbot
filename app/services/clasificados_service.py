import shelve
import logging
import os

# ──────────────────────────────────────────────
# Estado de conversación por usuario
# ──────────────────────────────────────────────
ESTADOS = [
    "INICIO",
    "MENU",
    "PASO_CATEGORIA",
    "PASO_TIPO",
    "PASO_TITULO",
    "PASO_DESCRIPCION",
    "PASO_UBICACION",
    "PASO_PRECIO",
    "PASO_CONTACTO",
    "PASO_RESUMEN",
]

_DB_DIR = os.environ.get("DB_PATH", "/app")
DB_CONVS = os.path.join(_DB_DIR, "conversations_db")


def _load_conv(wa_id: str) -> dict:
    with shelve.open(DB_CONVS) as db:
        return db.get(wa_id, {"estado": "INICIO", "datos": {}})


def _save_conv(wa_id: str, conv: dict):
    with shelve.open(DB_CONVS, writeback=True) as db:
        db[wa_id] = conv


def _reset_conv(wa_id: str):
    _save_conv(wa_id, {"estado": "INICIO", "datos": {}})


# ──────────────────────────────────────────────
# Helpers de detección de intención
# ──────────────────────────────────────────────
SALUDOS = {"hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hi", "hey", "ola"}
PUBLICAR = {"publicar", "crear", "agregar", "nuevo aviso", "anuncio", "poner aviso", "quiero publicar"}
CONSULTAR = {"consultar", "ver avisos", "buscar", "encontrar", "busco"}
REINICIAR = {"reiniciar", "empezar de nuevo", "cancelar", "salir", "reset"}

CATEGORIAS = {
    "1": "Inmueble",
    "2": "Vehículo",
    "3": "Empleo",
    "4": "Producto",
    "5": "Servicio",
    "6": "Otro",
    "inmueble": "Inmueble",
    "vehiculo": "Vehículo",
    "vehículo": "Vehículo",
    "empleo": "Empleo",
    "trabajo": "Empleo",
    "producto": "Producto",
    "servicio": "Servicio",
    "otro": "Otro",
}

TIPOS = {
    "1": "Vender",
    "2": "Alquilar",
    "3": "Comprar",
    "4": "Ofrecer",
    "vender": "Vender",
    "venta": "Vender",
    "alquilar": "Alquilar",
    "alquiler": "Alquilar",
    "renta": "Alquilar",
    "comprar": "Comprar",
    "compra": "Comprar",
    "ofrecer": "Ofrecer",
    "ofrezco": "Ofrecer",
}


def _match(texto: str, opciones: set) -> bool:
    t = texto.lower().strip()
    return any(op in t for op in opciones)


def _map_categoria(texto: str) -> str | None:
    t = texto.lower().strip()
    for k, v in CATEGORIAS.items():
        if k in t or v.lower() in t:
            return v
    return None


def _map_tipo(texto: str) -> str | None:
    t = texto.lower().strip()
    for k, v in TIPOS.items():
        if k in t or v.lower() in t:
            return v
    return None


# ──────────────────────────────────────────────
# Mensajes predefinidos
# ──────────────────────────────────────────────
MSG_BIENVENIDA = (
    "¡Hola! 👋 Soy el asistente virtual de *Clasificados de El Deber*.\n\n"
    "¿En qué puedo ayudarte hoy?\n\n"
    "📋 *1.* Publicar un aviso\n"
    "🔍 *2.* Consultar avisos\n\n"
    "Escribe lo que necesitas o elige una opción."
)

MSG_FUERA_TEMA = (
    "Puedo ayudarte únicamente con la creación y gestión de avisos clasificados de El Deber. 😊"
)

MSG_CATEGORIA = (
    "¡Perfecto! Te ayudaré a crear tu aviso. 📝\n\n"
    "*¿Qué deseas publicar?*\n\n"
    "1️⃣ Inmueble\n"
    "2️⃣ Vehículo\n"
    "3️⃣ Empleo\n"
    "4️⃣ Producto\n"
    "5️⃣ Servicio\n"
    "6️⃣ Otro\n\n"
    "Escribe el número o el nombre de la categoría."
)

MSG_TIPO = (
    "¿Deseas *vender, alquilar, comprar* u *ofrecer*?\n\n"
    "1️⃣ Vender\n"
    "2️⃣ Alquilar\n"
    "3️⃣ Comprar\n"
    "4️⃣ Ofrecer"
)

MSG_TITULO = (
    "¿Cuál será el *título* de tu aviso?\n\n"
    "_Ejemplo: Casa en venta excelente ubicación_"
)

MSG_DESCRIPCION = (
    "Ahora escribe una *descripción detallada* de tu aviso.\n\n"
    "Incluye detalles como:\n"
    "• Características\n"
    "• Estado (nuevo/usado)\n"
    "• Tamaño o dimensiones\n"
    "• Beneficios o extras\n"
    "• Cualquier información relevante"
)

MSG_UBICACION = "¿En qué *ciudad o zona* se encuentra? 📍"

MSG_PRECIO = (
    "¿Cuál es el *precio*? 💰\n\n"
    "_Si no deseas mostrarlo, escribe_ *Consultar*."
)

MSG_CONTACTO = (
    "¿Qué *número telefónico* o medio de contacto deseas publicar? 📞"
)


def _generar_resumen(datos: dict) -> str:
    return (
        "✅ *Resumen de tu aviso:*\n\n"
        f"📂 *Categoría:* {datos.get('categoria', '-')}\n"
        f"🔖 *Tipo:* {datos.get('tipo', '-')}\n\n"
        f"📌 *Título:*\n{datos.get('titulo', '-')}\n\n"
        f"📝 *Descripción:*\n{datos.get('descripcion', '-')}\n\n"
        f"📍 *Ubicación:* {datos.get('ubicacion', '-')}\n"
        f"💰 *Precio:* {datos.get('precio', '-')}\n"
        f"📞 *Contacto:* {datos.get('contacto', '-')}\n\n"
        "¿Deseas *modificar* algún dato o el aviso está *listo para publicar*? ✔️"
    )


MSG_PUBLICADO = (
    "🎉 ¡Tu aviso ha sido registrado exitosamente en *Clasificados de El Deber*!\n\n"
    "Será revisado y publicado en breve. ¡Gracias por confiar en nosotros!\n\n"
    "Si necesitas algo más, escribe *hola* para comenzar de nuevo."
)

MSG_CONSULTA = (
    "Para consultar avisos publicados, visita:\n"
    "🌐 *www.eldeber.com.bo/clasificados*\n\n"
    "También puedes indicarme qué tipo de aviso buscas y te orientaré. 😊"
)


# ──────────────────────────────────────────────
# Motor principal de respuestas
# ──────────────────────────────────────────────
def generate_response(message_body: str, wa_id: str, name: str) -> str:
    texto = message_body.strip()
    texto_lower = texto.lower()

    conv = _load_conv(wa_id)
    estado = conv["estado"]
    datos = conv["datos"]

    logging.info(f"[{wa_id}] Estado: {estado} | Mensaje: {texto!r}")

    # Comando global: reiniciar en cualquier momento
    if _match(texto_lower, REINICIAR):
        _reset_conv(wa_id)
        return MSG_BIENVENIDA

    # ── INICIO ──────────────────────────────────
    if estado == "INICIO":
        if _match(texto_lower, SALUDOS) or _match(texto_lower, PUBLICAR) or _match(texto_lower, CONSULTAR):
            if _match(texto_lower, PUBLICAR):
                conv["estado"] = "PASO_CATEGORIA"
                _save_conv(wa_id, conv)
                return MSG_CATEGORIA
            elif _match(texto_lower, CONSULTAR):
                return MSG_CONSULTA
            else:
                conv["estado"] = "MENU"
                _save_conv(wa_id, conv)
                return MSG_BIENVENIDA
        else:
            conv["estado"] = "MENU"
            _save_conv(wa_id, conv)
            return MSG_BIENVENIDA

    # ── MENU ─────────────────────────────────────
    if estado == "MENU":
        if _match(texto_lower, PUBLICAR) or texto_lower in {"1", "publicar"}:
            conv["estado"] = "PASO_CATEGORIA"
            _save_conv(wa_id, conv)
            return MSG_CATEGORIA
        elif _match(texto_lower, CONSULTAR) or texto_lower in {"2", "consultar"}:
            return MSG_CONSULTA
        else:
            return MSG_FUERA_TEMA

    # ── PASO 1: CATEGORÍA ────────────────────────
    if estado == "PASO_CATEGORIA":
        categoria = _map_categoria(texto_lower)
        if categoria:
            datos["categoria"] = categoria
            conv["estado"] = "PASO_TIPO"
            conv["datos"] = datos
            _save_conv(wa_id, conv)
            return MSG_TIPO
        else:
            return (
                "No reconocí la categoría. Por favor elige una opción:\n\n"
                "1️⃣ Inmueble  2️⃣ Vehículo  3️⃣ Empleo\n"
                "4️⃣ Producto  5️⃣ Servicio  6️⃣ Otro"
            )

    # ── PASO 2: TIPO ─────────────────────────────
    if estado == "PASO_TIPO":
        tipo = _map_tipo(texto_lower)
        if tipo:
            datos["tipo"] = tipo
            conv["estado"] = "PASO_TITULO"
            conv["datos"] = datos
            _save_conv(wa_id, conv)
            return MSG_TITULO
        else:
            return (
                "No reconocí la opción. Elige:\n\n"
                "1️⃣ Vender  2️⃣ Alquilar  3️⃣ Comprar  4️⃣ Ofrecer"
            )

    # ── PASO 3: TÍTULO ───────────────────────────
    if estado == "PASO_TITULO":
        if len(texto) < 5:
            return "El título es muy corto. Por favor escribe un título más descriptivo."
        datos["titulo"] = texto
        conv["estado"] = "PASO_DESCRIPCION"
        conv["datos"] = datos
        _save_conv(wa_id, conv)
        return MSG_DESCRIPCION

    # ── PASO 4: DESCRIPCIÓN ──────────────────────
    if estado == "PASO_DESCRIPCION":
        if len(texto) < 10:
            return "La descripción es muy corta. Agrega más detalles sobre tu aviso."
        datos["descripcion"] = texto
        conv["estado"] = "PASO_UBICACION"
        conv["datos"] = datos
        _save_conv(wa_id, conv)
        return MSG_UBICACION

    # ── PASO 5: UBICACIÓN ────────────────────────
    if estado == "PASO_UBICACION":
        datos["ubicacion"] = texto
        conv["estado"] = "PASO_PRECIO"
        conv["datos"] = datos
        _save_conv(wa_id, conv)
        return MSG_PRECIO

    # ── PASO 6: PRECIO ───────────────────────────
    if estado == "PASO_PRECIO":
        datos["precio"] = texto
        conv["estado"] = "PASO_CONTACTO"
        conv["datos"] = datos
        _save_conv(wa_id, conv)
        return MSG_CONTACTO

    # ── PASO 7: CONTACTO ─────────────────────────
    if estado == "PASO_CONTACTO":
        datos["contacto"] = texto
        conv["estado"] = "PASO_RESUMEN"
        conv["datos"] = datos
        _save_conv(wa_id, conv)
        return _generar_resumen(datos)

    # ── PASO 8: RESUMEN / CONFIRMACIÓN ───────────
    if estado == "PASO_RESUMEN":
        if any(p in texto_lower for p in ["listo", "publicar", "confirmar", "sí", "si", "ok", "correcto", "todo bien"]):
            _reset_conv(wa_id)
            return MSG_PUBLICADO
        elif any(p in texto_lower for p in ["modificar", "cambiar", "editar", "corregir"]):
            # Volver al inicio del flujo conservando datos
            conv["estado"] = "PASO_CATEGORIA"
            conv["datos"] = {}
            _save_conv(wa_id, conv)
            return (
                "Sin problema, empecemos de nuevo. 🔄\n\n" + MSG_CATEGORIA
            )
        else:
            return (
                "¿Confirmas que el aviso está listo para publicar?\n\n"
                "Escribe *listo* para publicar o *modificar* para corregir algún dato."
            )

    # Fallback
    _reset_conv(wa_id)
    return MSG_BIENVENIDA
