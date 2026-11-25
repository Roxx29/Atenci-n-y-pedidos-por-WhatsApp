"""Seguridad transversal (solo stdlib + cryptography opcional):
- registro de eventos de seguridad (`evento`): JSON por línea, sin datos personales; con SEGURIDAD_LOG rota a diario y guarda SEGURIDAD_DIAS (≥ 90);
  las alertas también van por POST a ALERTA_URL si está definida;
- cifrado en reposo de datos personales (`cifrar`/`descifrar`): Fernet con DATOS_CLAVE (claves separadas por comas: la primera cifra, todas
  descifran → rotación). Sin DATOS_CLAVE se guarda en claro (desarrollo). Lo viejo en claro se sigue leyendo (compatible);
- inicio de sesión con Google (`google_email`): verifica el id_token en tokeninfo de Google (aud, iss, email_verified, exp).
"""
import datetime as dt
import functools
import hashlib
import json
import logging
import logging.handlers
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PREFIJO = "enc1:"
EMAIL = re.compile(r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}")
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo?"
EMISORES = ("accounts.google.com", "https://accounts.google.com")
GSI = "https://accounts.google.com/gsi/"


# --- registro de eventos ---
def _logger():
    lg = logging.getLogger("motor.seguridad")
    if not lg.handlers:
        ruta = os.environ.get("SEGURIDAD_LOG")
        if ruta:
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
            dias = max(90, int(os.environ.get("SEGURIDAD_DIAS") or 180))  # nunca menos de 90 días
            h = logging.handlers.TimedRotatingFileHandler(ruta, when="midnight", backupCount=dias, encoding="utf-8", utc=True)
        else:
            h = logging.StreamHandler()  # stderr: lo recoge `docker logs` (rotación en docker-compose)
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg


def evento(tipo, alerta=False, **campos):
    """Un evento de seguridad. Nunca PIN, claves, tokens, teléfonos ni correos: solo tipo, cliente, IP, usuario del equipo y resultado."""
    d = {"fecha": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "nivel": "alerta" if alerta else "info", "evento": tipo, **campos}
    linea = json.dumps(d, ensure_ascii=False)
    try:
        _logger().log(logging.WARNING if alerta else logging.INFO, linea)
    except Exception:  # el registro nunca tumba una petición
        pass
    if alerta and os.environ.get("ALERTA_URL"):
        threading.Thread(target=_avisar, args=(os.environ["ALERTA_URL"], linea.encode()), daemon=True).start()


def _avisar(url, cuerpo):
    try:
        urllib.request.urlopen(urllib.request.Request(url, cuerpo, {"Content-Type": "application/json"}), timeout=5).close()
    except Exception:
        pass


# --- cifrado en reposo ---
@functools.lru_cache(maxsize=4)
def _fernet(claves):
    if not claves:
        return None
    from cryptography.fernet import Fernet, MultiFernet
    return MultiFernet([Fernet(k.strip().encode()) for k in claves.split(",") if k.strip()])


def cifrar(texto):
    f = _fernet(os.environ.get("DATOS_CLAVE", ""))
    if not texto or not f or str(texto).startswith(PREFIJO):
        return texto
    return PREFIJO + f.encrypt(str(texto).encode()).decode()


def descifrar(texto):
    """Lo cifrado → texto; lo viejo en claro tal cual; cifrado sin la clave buena → None (nunca se muestra el cifrado)."""
    if not (isinstance(texto, str) and texto.startswith(PREFIJO)):
        return texto
    f = _fernet(os.environ.get("DATOS_CLAVE", ""))
    try:
        return f.decrypt(texto[len(PREFIJO):].encode()).decode() if f else None
    except Exception:  # InvalidToken: otra clave
        return None


def migrar_contactos(sv):
    """Cifra el `contacto` en claro de consentimientos y pedidos viejos (solo con DATOS_CLAVE). Cada fila con un UPDATE condicionado:
    si otro hilo la cambió entretanto, no se pisa. Devuelve cuántas cifró."""
    if not os.environ.get("DATOS_CLAVE"):
        return 0
    n = 0
    for iid, c in sv._q("SELECT id, json_extract(datos, '$.contacto') FROM registros WHERE tipo IN ('consentimiento', 'pedido')"
                        " AND json_extract(datos, '$.contacto') IS NOT NULL AND json_extract(datos, '$.contacto') NOT LIKE 'enc1:%'"):
        sv._q("UPDATE registros SET datos=json_set(datos, '$.contacto', ?) WHERE id=? AND json_extract(datos, '$.contacto')=?", (cifrar(c), iid, c))
        n += 1
    return n


# --- inicio de sesión con Google ---
def huella_email(email):
    """Índice para buscar un correo sin guardarlo en claro (el correo va cifrado aparte)."""
    return hashlib.sha256(b"google:" + email.strip().lower().encode()).hexdigest()


def email_valido(email):
    return isinstance(email, str) and len(email) <= 254 and bool(EMAIL.fullmatch(email.strip().lower()))


def google_email(token, client_id, abrir=urllib.request.urlopen):
    """Correo verificado del id_token de Google, o None. Comprueba aud == client_id, emisor de Google, email_verified y que no haya caducado."""
    if not (client_id and isinstance(token, str) and 20 < len(token) < 4096):
        return None
    try:
        with abrir(TOKENINFO + urllib.parse.urlencode({"id_token": token}), timeout=5) as r:
            d = json.loads(r.read(65536))
        exp = int(d.get("exp", 0))
    except (OSError, ValueError, TypeError, AttributeError):  # HTTPError/URLError son OSError; 400 = token no válido
        return None
    if d.get("aud") != client_id or d.get("iss") not in EMISORES or str(d.get("email_verified")).lower() != "true" or exp <= time.time():
        return None
    e = str(d.get("email") or "").strip().lower()
    return e if email_valido(e) else None


def poner_email(sv, nombre, email):
    """Autoriza (o quita, con "") el correo de Google de una persona del equipo. ValueError si no vale o lo tiene otra persona."""
    email = (email or "").strip().lower()
    if email and not email_valido(email):
        raise ValueError("Correo no válido")
    h = email and huella_email(email)
    if email and sv._q("SELECT 1 FROM google WHERE email_h=? AND nombre<>?", (h, nombre)):
        raise ValueError("Ese correo ya lo usa otra persona")
    sv._q("DELETE FROM google WHERE nombre=?", (nombre,))
    if email:
        sv._q("INSERT INTO google(email_h, nombre, email) VALUES (?,?,?)", (h, nombre, cifrar(email)))


def emails(sv):
    """{nombre: correo} de quienes tienen Google autorizado."""
    return {n: descifrar(e) for n, e in sv._q("SELECT nombre, email FROM google")}


def usuario_de_email(sv, email):
    f = sv._q("SELECT nombre FROM google WHERE email_h=?", (huella_email(email),))
    return f[0][0] if f else None


def con_google(csp):
    """CSP de la página de entrada con el botón de Google (Google Identity Services); solo si hay GOOGLE_CLIENT_ID."""
    return (csp.replace("script-src 'self'", f"script-src 'self' {GSI}client", 1).replace("style-src 'self'", f"style-src 'self' {GSI}style", 1)
            + f"; frame-src {GSI}; connect-src 'self' {GSI}")
