import asyncio
import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, filters

# 📌 Configuración del bot de Telegram y API de Philips Hue
TELEGRAM_BOT_TOKEN = ""
HUE_BRIDGE_IP = "192.168.0.191"
HUE_USERNAME = ""

# 📌 Habitaciones y sus luces
habitaciones = {
    "Dormitorio": [25],
    "Dormitorio Hugo": [3],
    "Pasillo": [7, 8, 9, 10, 20, 32],
    "Baño": [16, 34],
    "Cocina": [22, 27, 28, 29, 33, 38, 23, 24],
    "Terraza": [26, 30, 37],
    "Habitación PC": [39],
    "Comedor": [36, 31, 11, 12, 13, 14]
}

# Diccionarios globales para gestionar estados y jobs
PANEL_STATES = {}       # Estado actual del panel por chat_id ("main", "room:<habitacion>", "color:<habitacion>")
PANEL_LAST_STATE = {}   # Último texto y markup enviado (para evitar actualizaciones innecesarias)
PANEL_JOBS = {}         # Job de actualización periódica por chat_id
EXPIRATION_JOBS = {}    # Job de expiración del panel por chat_id

# 📌 Configuración del logger
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ─────────────────────────────────────────────────────────────
# Funciones auxiliares para manejo seguro de peticiones HTTP
# ─────────────────────────────────────────────────────────────

def safe_get(url, timeout=5):
    try:
        response = requests.get(url, timeout=timeout)
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error conectando con {url}: {e}")
        return None

def safe_put(url, data, timeout=5):
    try:
        requests.put(url, json=data, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error enviando datos a {url}: {e}")

# ─────────────────────────────────────────────────────────────
# Funciones para obtener estado, brillo y ct de las luces
# ─────────────────────────────────────────────────────────────

async def obtener_estado_habitacion(habitacion):
    for luz_id in habitaciones[habitacion]:
        url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
        response = safe_get(url)
        if response and response["state"]["on"]:
            return "🟡"
    return "⚫️"

async def obtener_brillo_habitacion(habitacion):
    total_bri = 0
    count = 0
    for luz_id in habitaciones[habitacion]:
        url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
        response = safe_get(url)
        if response:
            if response["state"]["on"]:
                bri = response["state"].get("bri", 0)
                porcentaje = int(bri * 100 / 254)
            else:
                porcentaje = 0
            total_bri += porcentaje
            count += 1
    return total_bri // count if count else 0

async def obtener_ct_habitacion(habitacion):
    total_ct = 0
    count = 0
    for luz_id in habitaciones[habitacion]:
        url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
        response = safe_get(url)
        if response and response["state"]["on"] and "ct" in response["state"]:
            total_ct += response["state"]["ct"]
            count += 1
    return total_ct // count if count else None

# ─────────────────────────────────────────────────────────────
# Funciones para generar los paneles de control
# ─────────────────────────────────────────────────────────────

async def generar_panel_principal():
    keyboard = []
    for habitacion in habitaciones.keys():
        estado = await obtener_estado_habitacion(habitacion)
        brillo = await obtener_brillo_habitacion(habitacion)
        keyboard.append([InlineKeyboardButton(f"{estado} {habitacion} ({brillo}%)", callback_data=f"room:{habitacion}")])
    keyboard.append([InlineKeyboardButton("🛑 Apagar Todo", callback_data="apagar_todo")])
    # Botón para cerrar panel y poner el bot en reposo
    keyboard.append([InlineKeyboardButton("❌ Cerrar panel", callback_data="cerrar_panel")])
    markup = InlineKeyboardMarkup(keyboard)
    texto = "💡 **Control de Luces Philips Hue**\n\nSelecciona una habitación:"
    return texto, markup

async def generar_panel_habitacion(habitacion):
    estado = await obtener_estado_habitacion(habitacion)
    brillo = await obtener_brillo_habitacion(habitacion)
    texto = f"💡 **Controles para {habitacion}**\n\nEstado: {estado}\nBrillo: {brillo}%"
    keyboard = [
        [InlineKeyboardButton("🔌 Encender/Apagar", callback_data=f"toggle:{habitacion}")],
        [InlineKeyboardButton("🔆 Brillo +", callback_data=f"bright_inc:{habitacion}"),
         InlineKeyboardButton("🔅 Brillo -", callback_data=f"bright_dec:{habitacion}")],
        [InlineKeyboardButton("📉 Brillo 25%", callback_data=f"bright_set:25:{habitacion}"),
         InlineKeyboardButton("📊 Brillo 50%", callback_data=f"bright_set:50:{habitacion}"),
         InlineKeyboardButton("📈 Brillo 100%", callback_data=f"bright_set:100:{habitacion}")],
        [InlineKeyboardButton("🎨 Tono de Color", callback_data=f"color:{habitacion}")],
        [InlineKeyboardButton("⬅ Volver", callback_data="volver")],
        # Botón para cerrar panel
        [InlineKeyboardButton("❌ Cerrar panel", callback_data="cerrar_panel")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    return texto, markup

async def generar_panel_color(habitacion):
    if habitacion in ["Terraza", "Comedor"]:
        texto = f"🎨 **Selecciona el tono de color para {habitacion}**"
        colores = [
            ("🟥 Rojo", 0, 254),
            ("🟩 Verde", 25500, 254),
            ("🟦 Azul", 46920, 254),
            ("🟨 Amarillo", 12750, 254),
            ("🟪 Morado", 56100, 254),
            ("⚪ Blanco", 0, 0)
        ]
        keyboard = []
        for nombre, hue_val, sat in colores:
            keyboard.append([InlineKeyboardButton(nombre, callback_data=f"setcolor:{hue_val}:{sat}:{habitacion}")])
        keyboard.append([InlineKeyboardButton("⬅ Volver", callback_data=f"backroom:{habitacion}")])
        # Botón para cerrar panel
        keyboard.append([InlineKeyboardButton("❌ Cerrar panel", callback_data="cerrar_panel")])
    else:
        current_ct = await obtener_ct_habitacion(habitacion)
        if current_ct is None:
            current_ct = 300  # Valor por defecto
        texto = f"🎨 **Modifica la tonalidad para {habitacion}**\nTemperatura actual: {current_ct}"
        keyboard = [
            [InlineKeyboardButton("➕ Más Amarillo", callback_data=f"ct_inc:{habitacion}")],
            [InlineKeyboardButton("➖ Más Blanco", callback_data=f"ct_dec:{habitacion}")],
            [InlineKeyboardButton("⬅ Volver", callback_data=f"backroom:{habitacion}")]
        ]
        # Botón para cerrar panel
        keyboard.append([InlineKeyboardButton("❌ Cerrar panel", callback_data="cerrar_panel")])
    markup = InlineKeyboardMarkup(keyboard)
    return texto, markup

async def actualizar_mensaje(context, chat_id, message_id, texto, markup):
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=texto,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except BadRequest as e:
        error_message = str(e)
        if "Message is not modified" in error_message or "Message to edit not found" in error_message:
            return
        else:
            raise

# ─────────────────────────────────────────────────────────────
# Actualización periódica del panel
# ─────────────────────────────────────────────────────────────

async def actualizar_panel_periodicamente(context: CallbackContext):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    message_id = job_data["message_id"]

    # Si el panel ya fue cerrado, no se actualiza
    if chat_id not in PANEL_STATES:
        return

    panel_actual = PANEL_STATES.get(chat_id, "main")
    if panel_actual == "main":
        new_text, new_markup = await generar_panel_principal()
    elif panel_actual.startswith("room:"):
        habitacion = panel_actual.split("room:")[1]
        new_text, new_markup = await generar_panel_habitacion(habitacion)
    elif panel_actual.startswith("color:"):
        habitacion = panel_actual.split("color:")[1]
        new_text, new_markup = await generar_panel_color(habitacion)
    else:
        new_text, new_markup = await generar_panel_principal()

    last_state = PANEL_LAST_STATE.get(chat_id, {})
    last_text = last_state.get("text")
    last_markup = last_state.get("markup")

    new_markup_dict = new_markup.to_dict()

    if new_text == last_text and new_markup_dict == last_markup:
        return

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            reply_markup=new_markup,
            parse_mode="Markdown"
        )
        PANEL_LAST_STATE[chat_id] = {"text": new_text, "markup": new_markup_dict}
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logging.error(f"Error actualizando panel: {e}")

# ─────────────────────────────────────────────────────────────
# Función para eliminar el panel y cancelar sus jobs
# ─────────────────────────────────────────────────────────────

async def expirar_panel(context: CallbackContext, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.error(f"Error eliminando mensaje: {e}")
    update_job = PANEL_JOBS.pop(chat_id, None)
    if update_job:
        update_job.schedule_removal()
    exp_job = EXPIRATION_JOBS.pop(chat_id, None)
    if exp_job:
        try:
            exp_job.schedule_removal()
        except Exception as e:
            logging.info(f"El job de expiración ya fue removido: {e}")
    PANEL_STATES.pop(chat_id, None)
    PANEL_LAST_STATE.pop(chat_id, None)

def schedule_expiration(context: CallbackContext, chat_id: int, message_id: int, seconds: int = 60):
    exp_job = EXPIRATION_JOBS.get(chat_id)
    if exp_job:
        exp_job.schedule_removal()
    new_job = context.job_queue.run_once(
        lambda ctx: asyncio.create_task(expirar_panel(ctx, chat_id, message_id)),
        when=seconds
    )
    EXPIRATION_JOBS[chat_id] = new_job

# ─────────────────────────────────────────────────────────────
# Handlers para comandos y callbacks
# ─────────────────────────────────────────────────────────────

async def hue(update: Update, context: CallbackContext) -> None:
    if update.effective_message is None:
        return
    try:
        await update.effective_message.delete()
    except Exception as e:
        logging.error(f"Error borrando comando: {e}")

    texto, markup = await generar_panel_principal()
    message = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=texto,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    PANEL_STATES[update.effective_chat.id] = "main"
    context.chat_data["control_message"] = message.message_id

    job_context = {"chat_id": update.effective_chat.id, "message_id": message.message_id}
    update_job = context.job_queue.run_repeating(
        actualizar_panel_periodicamente,
        interval=10,
        first=10,
        data=job_context
    )
    PANEL_JOBS[update.effective_chat.id] = update_job

    schedule_expiration(context, update.effective_chat.id, message.message_id, seconds=60)

async def callback_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    message_id = context.chat_data.get("control_message")
    data = query.data

    # Cada vez que el usuario interactúa (excepto al cerrar) se reprograma la expiración
    def reprogramar():
        schedule_expiration(context, chat_id, message_id, seconds=60)

    if data == "cerrar_panel":
        # Se cancela el panel y se ponen en reposo (se elimina el mensaje y se cancelan los jobs)
        await query.answer("Cerrando panel...")
        await expirar_panel(context, chat_id, message_id)
        return

    if data == "volver":
        PANEL_STATES[chat_id] = "main"
        texto, markup = await generar_panel_principal()
        await actualizar_mensaje(context, chat_id, message_id, texto, markup)
        reprogramar()
        return

    if data.startswith("backroom:"):
        habitacion = data.split("backroom:")[1]
        PANEL_STATES[chat_id] = f"room:{habitacion}"
        texto, markup = await generar_panel_habitacion(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, texto, markup)
        reprogramar()
        return

    if data == "apagar_todo":
        for luces in habitaciones.values():
            for luz_id in luces:
                url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state"
                safe_put(url, {"on": False})
        PANEL_STATES[chat_id] = "main"
        texto, markup = await generar_panel_principal()
        await actualizar_mensaje(context, chat_id, message_id, "🛑 **Todas las luces han sido apagadas**", markup)
        reprogramar()
        return

    if data.startswith("room:"):
        habitacion = data.split("room:")[1]
        PANEL_STATES[chat_id] = f"room:{habitacion}"
        texto, markup = await generar_panel_habitacion(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, texto, markup)
        reprogramar()
        return

    if data.startswith("toggle:"):
        habitacion = data.split("toggle:")[1]
        estados = []
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
            resp = safe_get(url)
            if resp is not None:
                estados.append(resp["state"]["on"])
        nuevo_estado = not any(estados)
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state"
            safe_put(url, {"on": nuevo_estado})
        estado_texto = "encendidas" if nuevo_estado else "apagadas"
        texto, markup = await generar_panel_habitacion(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, f"✅ **Luces en {habitacion} {estado_texto}**", markup)
        PANEL_STATES[chat_id] = f"room:{habitacion}"
        reprogramar()
        return

    if data.startswith("bright_inc:"):
        habitacion = data.split("bright_inc:")[1]
        step = 25
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
            response = safe_get(url)
            if response:
                current_bri = response["state"].get("bri", 0) if response["state"]["on"] else 0
                current_pct = int(current_bri * 100 / 254)
                nuevo_pct = min(current_pct + step, 100)
                nuevo_bri = int(nuevo_pct * 254 / 100)
                safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                         {"on": True, "bri": nuevo_bri})
        PANEL_STATES[chat_id] = f"room:{habitacion}"
        texto, markup = await generar_panel_habitacion(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, f"🔆 **Brillo aumentado en {habitacion}**", markup)
        reprogramar()
        return

    if data.startswith("bright_dec:"):
        habitacion = data.split("bright_dec:")[1]
        step = 25
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
            response = safe_get(url)
            if response:
                current_bri = response["state"].get("bri", 0) if response["state"]["on"] else 0
                current_pct = int(current_bri * 100 / 254)
                nuevo_pct = max(current_pct - step, 0)
                nuevo_bri = int(nuevo_pct * 254 / 100) if nuevo_pct > 0 else 1
                safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                         {"on": True, "bri": nuevo_bri})
        PANEL_STATES[chat_id] = f"room:{habitacion}"
        texto, markup = await generar_panel_habitacion(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, f"🔅 **Brillo disminuido en {habitacion}**", markup)
        reprogramar()
        return

    if data.startswith("bright_set:"):
        parts = data.split(":")
        if len(parts) == 3:
            try:
                valor_pct = int(parts[1])
                habitacion = parts[2]
                nuevo_bri = int(valor_pct * 254 / 100)
                for luz_id in habitaciones[habitacion]:
                    safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                             {"on": True, "bri": nuevo_bri})
                PANEL_STATES[chat_id] = f"room:{habitacion}"
                texto, markup = await generar_panel_habitacion(habitacion)
                await actualizar_mensaje(context, chat_id, message_id, f"🔆 **Brillo ajustado al {valor_pct}% en {habitacion}**", markup)
                reprogramar()
            except ValueError:
                logging.error("Valor de brillo inválido")
        return

    if data.startswith("color:"):
        habitacion = data.split("color:")[1]
        PANEL_STATES[chat_id] = f"color:{habitacion}"
        texto, markup = await generar_panel_color(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, texto, markup)
        reprogramar()
        return

    if data.startswith("setcolor:"):
        parts = data.split(":")
        if len(parts) == 4:
            try:
                hue_val = int(parts[1])
                sat_val = int(parts[2])
                habitacion = parts[3]
                for luz_id in habitaciones[habitacion]:
                    safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                             {"on": True, "hue": hue_val, "sat": sat_val})
                PANEL_STATES[chat_id] = f"room:{habitacion}"
                texto, markup = await generar_panel_habitacion(habitacion)
                await actualizar_mensaje(context, chat_id, message_id, f"🎨 **Tono de color aplicado en {habitacion}**", markup)
                reprogramar()
            except ValueError:
                logging.error("Valor de color inválido")
        return

    if data.startswith("ct_inc:"):
        habitacion = data.split("ct_inc:")[1]
        step_ct = 20
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
            response = safe_get(url)
            if response and "ct" in response["state"]:
                current_ct = response["state"]["ct"]
                new_ct = min(current_ct + step_ct, 500)
                safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                         {"on": True, "ct": new_ct})
        PANEL_STATES[chat_id] = f"color:{habitacion}"
        texto, markup = await generar_panel_color(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, f"🎨 **Tono ajustado en {habitacion} (más amarillo)**", markup)
        reprogramar()
        return

    if data.startswith("ct_dec:"):
        habitacion = data.split("ct_dec:")[1]
        step_ct = 20
        for luz_id in habitaciones[habitacion]:
            url = f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}"
            response = safe_get(url)
            if response and "ct" in response["state"]:
                current_ct = response["state"]["ct"]
                new_ct = max(current_ct - step_ct, 153)
                safe_put(f"http://{HUE_BRIDGE_IP}/api/{HUE_USERNAME}/lights/{luz_id}/state",
                         {"on": True, "ct": new_ct})
        PANEL_STATES[chat_id] = f"color:{habitacion}"
        texto, markup = await generar_panel_color(habitacion)
        await actualizar_mensaje(context, chat_id, message_id, f"🎨 **Tono ajustado en {habitacion} (más blanco)**", markup)
        reprogramar()
        return

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("hue", hue, filters=filters.ALL))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
