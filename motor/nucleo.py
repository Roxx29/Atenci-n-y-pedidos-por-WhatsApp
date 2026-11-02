"""Núcleo: carga un servicio (servicios/<slug>/servicio.yaml) y responde mensajes con Claude + herramientas."""
import datetime as dt
import json
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import herramientas

VENTANA_S = 24 * 3600  # conversación nueva tras 24 h sin mensajes (= ventana de servicio de WhatsApp)
MAX_VUELTAS = 10

PROMPT = """Eres el asistente de {negocio}. Atiendes a sus clientes por WhatsApp en nombre del negocio.
Objetivo del servicio: {objetivo}

Reglas:
- Español de Panamá, trato de "usted", cordial y mensajes cortos (es WhatsApp).
- Usa SOLO la información de este mensaje y lo que devuelvan las herramientas. Nunca inventes precios, horarios, disponibilidad ni políticas.
- Si no sabes algo, el cliente pide una persona, está molesto o es una urgencia: usa escalar_a_humano.
- Antes de crear o cambiar algo (citas, cobros), confirma los datos con el cliente.
- Pide solo los datos necesarios; no solicites datos sensibles que el servicio no requiera (Ley 81 de 2019).
- Cada mensaje del cliente empieza con [fecha hora · cliente <teléfono>]: úsalo para fechas relativas ("mañana") y como teléfono del cliente.
- Si el cliente responde a un recordatorio, busca sus registros con buscar usando su teléfono.

Horario de atención: {horario}

Instrucciones específicas del servicio:
{instrucciones}

Información del negocio:
{conocimiento}"""


class Servicio:
    def __init__(self, carpeta, db=None, cliente=None):
        self.dir = Path(carpeta)
        self.cfg = yaml.safe_load((self.dir / "servicio.yaml").read_text(encoding="utf-8"))
        self.tz = ZoneInfo(self.cfg.get("zona_horaria", "America/Panama"))
        self.tools = herramientas.esquemas(self.cfg.get("herramientas", []))
        self.db = sqlite3.connect(str(db or self.dir / "datos.sqlite"), check_same_thread=False)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS registros(id INTEGER PRIMARY KEY, tipo TEXT, datos TEXT, creado TEXT);"
            "CREATE TABLE IF NOT EXISTS conversaciones(id TEXT PRIMARY KEY, mensajes TEXT, actualizado REAL);"
            "CREATE TABLE IF NOT EXISTS vistos(id TEXT PRIMARY KEY);"
        )
        self._db_lock = threading.Lock()
        self.citas_lock = threading.Lock()
        self._locks = defaultdict(threading.Lock)  # un turno a la vez por cliente
        self._cliente = cliente
        kb = self.dir / self.cfg.get("conocimiento", "conocimiento.md")
        self.system = PROMPT.format(
            negocio=self.cfg.get("negocio", self.cfg.get("nombre", "el negocio")),
            objetivo=self.cfg.get("objetivo", ""),
            horario=json.dumps(self.cfg.get("horario", "no aplica"), ensure_ascii=False),
            instrucciones=self.cfg.get("instrucciones", ""),
            conocimiento=kb.read_text(encoding="utf-8") if kb.exists() else "",
        )

    # --- almacenamiento ---
    def ahora(self):
        return dt.datetime.now(self.tz).replace(second=0, microsecond=0)

    def _q(self, sql, args=()):
        with self._db_lock, self.db:
            return self.db.execute(sql, args).fetchall()

    def insertar(self, tipo, datos):
        with self._db_lock, self.db:
            return self.db.execute("INSERT INTO registros(tipo, datos, creado) VALUES (?,?,?)", (tipo, json.dumps(datos, ensure_ascii=False), self.ahora().isoformat())).lastrowid

    def listar(self, tipo):
        return [{"id": i, **json.loads(d)} for i, d in self._q("SELECT id, datos FROM registros WHERE tipo=? ORDER BY id", (tipo,))]

    def actualizar(self, id, cambios, tipo=None):
        with self._db_lock, self.db:  # leer+escribir bajo el mismo bloqueo: sin pérdida de cambios concurrentes
            fila = self.db.execute("SELECT tipo, datos FROM registros WHERE id=?", (id,)).fetchall()
            if not fila or (tipo and fila[0][0] != tipo):
                raise ValueError(f"No existe el registro {id}" + (f" de tipo {tipo}" if tipo else ""))
            self.db.execute("UPDATE registros SET datos=? WHERE id=?", (json.dumps({**json.loads(fila[0][1]), **cambios}, ensure_ascii=False), id))

    def primera_vez(self, msg_id):
        """True si el id de mensaje no se había procesado (WhatsApp reenvía webhooks)."""
        with self._db_lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO vistos(id) VALUES (?)", (msg_id,)).rowcount == 1

    # --- agente ---
    @property
    def cliente(self):
        if self._cliente is None:
            import anthropic
            self._cliente = anthropic.Anthropic()
        return self._cliente

    def _historial(self, cid):
        fila = self._q("SELECT mensajes, actualizado FROM conversaciones WHERE id=?", (cid,))
        # shortcut: conversación nueva tras 24 h en vez de compactar; si hay chats muy largos, usar compaction.
        return json.loads(fila[0][0]) if fila and time.time() - fila[0][1] < VENTANA_S else []

    def responder(self, cid, texto):
        with self._locks[cid]:
            msgs = self._historial(cid)
            msgs.append({"role": "user", "content": f"[{self.ahora():%Y-%m-%d %H:%M} · cliente {cid}] {texto}"})
            respuesta = self._bucle(msgs, cid)
            self._q("INSERT OR REPLACE INTO conversaciones VALUES (?,?,?)", (cid, json.dumps(msgs, ensure_ascii=False), time.time()))
            return respuesta

    def _bucle(self, msgs, cid):
        for _ in range(MAX_VUELTAS):
            r = self.cliente.beta.messages.create(
                model=self.cfg.get("modelo", "claude-opus-5-5"),
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=msgs,
                output_config={"effort": self.cfg.get("esfuerzo", "low")},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content]})
            if r.stop_reason == "pause_turn":
                continue
            if r.stop_reason != "tool_use":
                texto = "\n".join(b.text for b in r.content if b.type == "text").strip()
                if r.stop_reason == "refusal" or not texto or any(b.type == "tool_use" for b in r.content):
                    msgs.pop()  # un turno vacío o con tool_use sin resultado haría fallar la API en todos los mensajes siguientes
                    return "Disculpe, no puedo ayudarle con eso por aquí. Si desea, le paso con una persona del equipo."
                return texto
            resultados = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                try:
                    res = {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(herramientas.ejecutar(self, b.name, b.input, cid), ensure_ascii=False)}
                except (ValueError, TypeError) as e:
                    res = {"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}", "is_error": True}
                resultados.append(res)
            msgs.append({"role": "user", "content": resultados})
        return "Disculpe, se me complicó su solicitud. Una persona del equipo le contactará."

    # --- mensajes programados ---
    def pendientes(self):
        ahora = f"{self.ahora():%Y-%m-%d %H:%M}"
        return [m for m in self.listar("mensaje_programado") if m["estado"] == "pendiente" and m["enviar_en"] <= ahora]
