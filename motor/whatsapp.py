"""Canal WhatsApp (Cloud API de Meta): webhook entrante + envío de mensajes.

Variables de entorno: WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET,
WHATSAPP_API_VERSION (opcional; revisa la versión vigente en developers.facebook.com).
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def firma_valida(cuerpo, cabecera, secreto):
    esperada = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return bool(cabecera) and hmac.compare_digest(esperada.encode(), cabecera.encode())


def mensajes_entrantes(payload):
    """(id, teléfono, texto) de cada mensaje de un webhook de WhatsApp."""
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            for m in ch.get("value", {}).get("messages", []):
                if m.get("type") in ("reaction", "sticker"):
                    continue
                texto = m["text"]["body"] if m.get("type") == "text" else f"[El cliente envió un mensaje de tipo {m.get('type')}; solo podemos leer texto]"
                yield m["id"], m["from"], texto


def enviar(telefono, texto, plantilla=None):
    """Envía texto. Fuera de la ventana de 24 h Meta exige plantilla aprobada (con 1 parámetro de cuerpo)."""
    token, phone_id = os.environ.get("WHATSAPP_TOKEN"), os.environ.get("WHATSAPP_PHONE_ID")
    if not (token and phone_id):
        print(f"[modo seco → {telefono}] {texto}")
        return
    cuerpo = {"messaging_product": "whatsapp", "to": telefono}
    if plantilla:
        cuerpo |= {"type": "template", "template": {"name": plantilla, "language": {"code": "es"}, "components": [{"type": "body", "parameters": [{"type": "text", "text": texto}]}]}}
    else:
        cuerpo |= {"type": "text", "text": {"body": texto}}
    url = f"https://graph.facebook.com/{os.environ.get('WHATSAPP_API_VERSION', 'v25.0')}/{phone_id}/messages"
    req = urllib.request.Request(url, json.dumps(cuerpo).encode(), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def enviar_pendientes(sv, enviar=enviar):
    limite = f"{sv.ahora() - dt.timedelta(hours=6):%Y-%m-%d %H:%M}"
    for m in sv.pendientes():
        if m["enviar_en"] < limite:  # tras una caída no mandar avisos viejos, ni reintentar para siempre
            sv.actualizar(m["id"], {"estado": "vencido"})
            continue
        try:
            enviar(m["telefono"], m["texto"], sv.cfg.get("whatsapp", {}).get("plantilla_recordatorio"))
            sv.actualizar(m["id"], {"estado": "enviado"})
        except Exception as e:  # sigue con los demás; se reintenta en la próxima pasada
            print(f"Error enviando mensaje {m['id']}: {e}")


def _atender(sv, msg_id, tel, texto):
    if not sv.primera_vez(msg_id):
        return
    try:
        respuesta = sv.responder(tel, texto)
    except Exception:
        traceback.print_exc()
        respuesta = "Disculpe, tuvimos un problema técnico. Intente de nuevo en unos minutos."
    enviar(tel, respuesta)


def servir(sv, puerto=8080):
    secreto, verify = os.environ.get("WHATSAPP_APP_SECRET"), os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if not (secreto and verify):
        raise SystemExit("Faltan WHATSAPP_APP_SECRET y WHATSAPP_VERIFY_TOKEN: sin ellos cualquiera podría enviar mensajes falsos al webhook.")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # verificación del webhook por Meta
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            ok = q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", "").encode(), verify.encode())
            self.send_response(200 if ok else 403)
            self.end_headers()
            if ok:
                self.wfile.write(q.get("hub.challenge", "").encode())

        def do_POST(self):
            largo = int(self.headers.get("Content-Length") or 0)
            if not 0 <= largo <= 1_000_000:  # tope antes de leer: evita bloquear hilos con cuerpos enormes
                self.send_response(413)
                self.end_headers()
                return
            cuerpo = self.rfile.read(largo)
            if not firma_valida(cuerpo, self.headers.get("X-Hub-Signature-256"), secreto):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)  # responder rápido; Meta reintenta si tardamos
            self.end_headers()
            for msg_id, tel, texto in mensajes_entrantes(json.loads(cuerpo or b"{}")):
                threading.Thread(target=_atender, args=(sv, msg_id, tel, texto), daemon=True).start()

    def recordatorios():
        while True:
            enviar_pendientes(sv)
            time.sleep(60)

    threading.Thread(target=recordatorios, daemon=True).start()
    print(f"Servicio '{sv.cfg.get('nombre')}' escuchando en :{puerto}")
    ThreadingHTTPServer(("", puerto), H).serve_forever()
