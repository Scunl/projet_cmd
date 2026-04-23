import telebot
import gspread
import time
import os
from flask import Flask, request
from oauth2client.service_account import ServiceAccountCredentials
import json

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_NAME = "Orders"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

sessions = {}

STEPS = [
    "order_number", "contact", "details", "photo", "size", "flocked",
    "name", "number", "second_name", "personalization", "comment"
]

STEP_NAMES = {
    "order_number": "Numéro de Commande", "contact": "Contact", "details": "Détails du Maillot",
    "photo": "Photo", "size": "Taille", "flocked": "Floqué",
    "name": "Nom", "number": "Numéro", "second_name": "Nom en dessous",
    "personalization": "Personnalisation", "comment": "Commentaire"
}

PROMPTS = {
    "contact": "📞 Numéro de téléphone ou Snapchat :",
    "details": "🏆 Détails du maillot (Ex: Equipe de France Bleu Domicile 2026) :",
    "photo": "📸 Envoyez une photo du maillot (ou tapez /skip) :",
    "size": "👕 Taille (Ex: S, M, L, etc) :",
    "flocked": "🎨 Floqué ? (Oui/Non) :",
    "name": "🏷️ Nom (Entrez X si rien) :",
    "number": "🔢 Numéro (Entrez X si rien) :",
    "second_name": "🏷️ Nom en dessous du numéro (Entrez X si rien) :",
    "personalization": "✨ Personnalisation (Badge, Version pro, manches longues) :",
    "comment": "💬 Commentaire sur la commande ?"
}

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1

def generate_order_number(contact):
    last_digits = contact[-4:] if len(contact) >= 4 else contact
    timestamp = str(int(time.time()))
    return f"CMD-{last_digits}-{timestamp}"

def get_next_step(current, data):
    flow = {
        "contact": "details", "details": "photo", "photo": "size", "size": "flocked",
        "flocked": "name" if data.get("flocked", "").lower() in ["oui", "yes", "y"] else "personalization",
        "name": "number", "number": "second_name", "second_name": "personalization",
        "personalization": "comment", "comment": "recap"
    }
    return flow.get(current, "recap")

def generate_recap_text(data):
    msg = "📋 *Récapitulatif de votre commande :*\n\n"
    for i, step in enumerate(STEPS):
        val = data.get(step, "Non renseigné")
        if step == "photo" and val != "Pas de photo":
            val = "Photo reçue 🖼️"
        msg += f"{i+1}️⃣ {STEP_NAMES[step]} : {val}\n"
    msg += "\n✅ Tapez *0* pour VALIDER.\n✏️ Tapez un *numéro* (1 à 11) pour modifier."
    return msg

@bot.message_handler(commands=['order'])
def start_order(message):
    chat_id = message.chat.id
    sessions[chat_id] = {"current_step": "contact", "data": {}, "editing": False}
    bot.send_message(chat_id, PROMPTS["contact"])

@bot.message_handler(commands=['skip'])
def skip_photo(message):
    chat_id = message.chat.id
    if chat_id in sessions and sessions[chat_id].get("current_step") == "photo":
        sessions[chat_id]["data"]["photo"] = "Pas de photo"
        handle_next_step(chat_id)

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        bot.send_message(chat_id, "Tapez /order pour commencer.")
        return

    session = sessions[chat_id]
    current_step = session["current_step"]

    if current_step == "recap":
        handle_recap_logic(message, session)
        return

    if message.photo:
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        val = f'=IMAGE("https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}")'
    else:
        val = message.text

    session["data"][current_step] = val

    if session.get("editing"):
        if current_step == "flocked" and val.lower() not in ["oui", "yes", "y"]:
            for key in ["name", "number", "second_name"]: session["data"].pop(key, None)
        session["current_step"] = "recap"
        session["editing"] = False
        bot.send_message(chat_id, generate_recap_text(session["data"]), parse_mode="Markdown")
        return

    handle_next_step(chat_id)

def handle_next_step(chat_id):
    session = sessions[chat_id]
    next_step = get_next_step(session["current_step"], session["data"])
    session["current_step"] = next_step

    if next_step == "recap":
        bot.send_message(chat_id, generate_recap_text(session["data"]), parse_mode="Markdown")
    else:
        bot.send_message(chat_id, PROMPTS[next_step])

def handle_recap_logic(message, session):
    chat_id = message.chat.id
    text = message.text

    if text == "0":
        contact_info = session["data"].get("contact", "0000")
        order_num = generate_order_number(contact_info)
        session["data"]["order_number"] = order_num

        try:
            sheet = get_sheet()
            row = [str(session["data"].get(s, "")) for s in STEPS]

            col_b_values = sheet.col_values(2)
            next_row = max(len(col_b_values) + 1, 2)

            if next_row > sheet.row_count:
                sheet.add_rows(10)

            range_name = f"B{next_row}:L{next_row}"
            sheet.update(values=[row], range_name=range_name, value_input_option="USER_ENTERED")

            bot.send_message(chat_id, f"✅ Commande enregistrée !\nNuméro de commande : {order_num}")
            del sessions[chat_id]
        except Exception as e:
            bot.send_message(chat_id, f"❌ Erreur lors de l'enregistrement : {str(e)}")
        return

    if text and text.isdigit() and 1 <= int(text) <= len(STEPS):
        idx = int(text) - 1
        target_step = STEPS[idx]
        session["current_step"] = target_step
        session["editing"] = True
        bot.send_message(chat_id, f"Modification de {STEP_NAMES[target_step]} :\n{PROMPTS.get(target_step, 'Entrez la nouvelle valeur :')}")
        return

    bot.send_message(chat_id, "Choix invalide.\n" + generate_recap_text(session["data"]), parse_mode="Markdown")

@app.route('/set_webhook')
def set_webhook():
    url = f"https://Scunl.pythonanywhere.com/{BOT_TOKEN}"
    result = bot.set_webhook(url=url)
    return f"Webhook set: {result}"

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'error'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
