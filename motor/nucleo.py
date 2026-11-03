"""Núcleo: carga un servicio (servicios/<slug>/servicio.yaml) y responde mensajes con Claude + herramientas."""
import datetime as dt
import json
import os
import re
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
CAIDA_S = 300  # tras fallar el principal, se usa el respaldo este tiempo antes de reintentar


def _de_proveedor(e):
    """Fallo del proveedor (saldo, clave, límite, caída, red), no error nuestro (400/404/422). Sin importar anthropic."""
    c = getattr(e, "status_code", None)
    if isinstance(c, int):
        return c in (401, 402, 403, 429) or c >= 500
    return isinstance(e, (ConnectionError, TimeoutError)) or type(e).__name__ in ("APIConnectionError", "APITimeoutError")
PREFIJO = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} · cliente [^\]]*\]\s*")  # eco del prefijo que el código antepone al mensaje

PROMPT = """Eres el asistente de {negocio}. Atiendes a sus clientes por WhatsApp en nombre del negocio.
Objetivo del servicio: {objetivo}

Reglas:
- Español de Panamá, trato de "usted", cordial y mensajes cortos (es WhatsApp).
- Usa SOLO la información de este mensaje y lo que devuelvan las herramientas. Nunca inventes precios, horarios, disponibilidad ni políticas.
- Si no sabes algo, el cliente pide una persona, está molesto o es una urgencia: usa escalar_a_humano.
- Antes de crear o cambiar algo (citas, cobros), confirma los datos con el cliente.
- No prometas tiempos de preparación, entrega o recogida de un pedido hasta que esté pagado (verificado por el equipo); mientras tanto di que el pago está pendiente de verificación.
- Antes de crear un pedido o cita, ten TODOS los datos (nombre real del cliente, modalidad, dirección y zona si es entrega, hora): pregunta lo que falte, nunca lo inventes ni uses el teléfono como nombre. No llames a crear_pedido ni ofrezcas confirmar hasta tenerlos.
- Nunca digas importes en centavos ni los calcules: usa solo los textos "B/. 9.36" que devuelven las herramientas.
- Pide solo los datos necesarios; no solicites datos sensibles que el servicio no requiera (Ley 81 de 2019).
- Cada mensaje del cliente empieza con [fecha hora · cliente <teléfono>]: úsalo para fechas relativas ("mañana") y como teléfono del cliente.
- Si el cliente responde a un recordatorio, busca sus registros con buscar usando su teléfono.

Horario de atención: {horario}

Instrucciones específicas del servicio:
{instrucciones}

Información del negocio:
{conocimiento}"""


class Servicio:
    def __init__(self, carpeta, db=None, cliente=None, cliente_respaldo=None):
        self.dir = Path(carpeta)
        self.cfg = yaml.safe_load((self.dir / "servicio.yaml").read_text(encoding="utf-8"))
        self.tz = ZoneInfo(self.cfg.get("zona_horaria", "America/Panama"))
        self.tools = herramientas.esquemas(self.cfg.get("herramientas", []))
        ruta = str(db or self.dir / "datos.sqlite")
        self.db = sqlite3.connect(ruta, timeout=10, check_same_thread=False)  # timeout: espera al otro proceso (bot/panel)
        if ruta != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")  # las lecturas del panel no bloquean al bot
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS registros(id INTEGER PRIMARY KEY, tipo TEXT, datos TEXT, creado TEXT);"
            "CREATE TABLE IF NOT EXISTS conversaciones(id TEXT PRIMARY KEY, mensajes TEXT, actualizado REAL);"
            "CREATE TABLE IF NOT EXISTS vistos(id TEXT PRIMARY KEY);"
        )
        self._db_lock = threading.Lock()
        self.citas_lock = threading.Lock()
        self._locks = defaultdict(threading.Lock)  # un turno a la vez por cliente
        self._cliente = cliente
        self._cliente_respaldo = cliente_respaldo or cliente  # en pruebas el mismo cliente falso sirve a ambos
        self._principal_caido_hasta = 0.0
        self._avisado = False
        self._reloj = time.monotonic
        self.menu = herramientas.cargar_menu(self.dir, self.cfg["menu"]) if self.cfg.get("menu") else {}
        kb = self.dir / self.cfg.get("conocimiento", "conocimiento.md")
        self.system = PROMPT.format(
            negocio=self.cfg.get("negocio", self.cfg.get("nombre", "el negocio")),
            objetivo=self.cfg.get("objetivo", ""),
            horario=json.dumps(self.cfg.get("horario", "no aplica"), ensure_ascii=False),
            instrucciones=self.cfg.get("instrucciones", ""),
            conocimiento=(kb.read_text(encoding="utf-8") if kb.exists() else "") + (f"\n\nMenú (id | nombre | precio | categoría):\n{herramientas.texto_menu(self.menu)}" if self.menu else ""),
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

    def actualizar(self, id, cambios, tipo=None, desde=None, antes=None):
        """Lee-comprueba-escribe en una transacción; devuelve el registro ya fusionado. `desde`: estados permitidos; `antes`: dict que recibe el registro previo."""
        with self._db_lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")  # bloqueo de escritura entre procesos antes de leer
            fila = self.db.execute("SELECT tipo, datos FROM registros WHERE id=?", (id,)).fetchall()
            if not fila or (tipo and fila[0][0] != tipo):
                raise ValueError(f"No existe el registro {id}" + (f" de tipo {tipo}" if tipo else ""))
            actual = json.loads(fila[0][1])
            if desde is not None and actual.get("estado") not in desde:
                raise ValueError(f"El pedido está '{actual.get('estado')}'; esta acción solo aplica a pedidos en: {', '.join(sorted(desde))}.")
            if antes is not None:
                antes.update(actual)
            nuevo = {**actual, **cambios}
            self.db.execute("UPDATE registros SET datos=? WHERE id=?", (json.dumps(nuevo, ensure_ascii=False), id))
            return {"id": id, **nuevo}

    def cerrar(self):
        self.db.close()

    def primera_vez(self, msg_id):
        """True si el id de mensaje no se había procesado (WhatsApp reenvía webhooks)."""
        with self._db_lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO vistos(id) VALUES (?)", (msg_id,)).rowcount == 1

    # --- agente ---
    @property
    def cliente(self):
        if self._cliente is None:
            import anthropic
            base, clave = os.environ.get("ANTHROPIC_BASE_URL", ""), os.environ.get("ANTHROPIC_API_KEY")
            if "deepseek" in base and not clave and os.environ.get("DEEPSEEK_API_KEY"):
                clave = os.environ["DEEPSEEK_API_KEY"]
            self._cliente = anthropic.Anthropic(api_key=clave) if clave else anthropic.Anthropic()
        return self._cliente

    @property
    def cliente_respaldo(self):
        if self._cliente_respaldo is None:
            import anthropic
            self._cliente_respaldo = anthropic.Anthropic(base_url=os.environ["LLM_RESPALDO_BASE_URL"], api_key=os.environ.get("LLM_RESPALDO_API_KEY", "ollama"))
        return self._cliente_respaldo

    def _historial(self, cid):
        fila = self._q("SELECT mensajes, actualizado FROM conversaciones WHERE id=?", (cid,))
        # shortcut: conversación nueva tras 24 h en vez de compactar; si hay chats muy largos, usar compaction.
        return json.loads(fila[0][0]) if fila and time.time() - fila[0][1] < VENTANA_S else []

    def responder(self, cid, texto):
        with self._locks[cid]:
            msgs = self._historial(cid)
            msgs.append({"role": "user", "content": f"[{self.ahora():%Y-%m-%d %H:%M} · cliente {cid}] {texto}"})
            respuesta = self._bucle(msgs, cid, texto)
            self._q("INSERT OR REPLACE INTO conversaciones VALUES (?,?,?)", (cid, json.dumps(msgs, ensure_ascii=False), time.time()))
            return respuesta

    def _llamar_modelo(self, msgs):
        """Llamada al proveedor del modelo. Devuelve (respuesta, compatible): compatible=True si respondió un servidor
        no-Anthropic (principal compatible o respaldo); entonces los bloques thinking no se reenvían."""
        base = os.environ.get("ANTHROPIC_BASE_URL", "")
        modelo = os.environ.get("LLM_MODEL") or self.cfg.get("modelo", "claude-opus-5-5")
        respaldo = os.environ.get("LLM_RESPALDO_BASE_URL") and os.environ.get("LLM_RESPALDO_MODEL")
        if not (respaldo and self._reloj() < self._principal_caido_hasta):
            try:
                r = self._llamar(self.cliente, modelo, msgs, bool(base and "anthropic.com" not in base))
                self._avisado = False  # el principal volvió: un próximo fallo vuelve a avisar
                return r
            except Exception as e:
                if not (respaldo and _de_proveedor(e)):
                    raise
                self._principal_caido_hasta = self._reloj() + CAIDA_S
                if not self._avisado:
                    self._avisado = True
                    texto = "IA principal sin saldo o caída: usando respaldo local"
                    self.insertar("aviso", {"texto": texto, "telefono": None, "pedido_id": None})
                    print(f"{texto} ({getattr(e, 'status_code', None) or type(e).__name__})")
        return self._llamar(self.cliente_respaldo, os.environ["LLM_RESPALDO_MODEL"], msgs, True)

    def _llamar(self, cliente, modelo, msgs, compatible):
        if compatible:  # servidor compatible (DeepSeek, Ollama): solo lo básico, sin betas ni tool_choice
            # thinking desactivado: con qwen3 el razonamiento hace cada vuelta ~40 s (sin él, ~1 s)
            r = cliente.messages.create(model=modelo, max_tokens=16000, system=self.system, tools=self.tools, messages=msgs,
                                        thinking={"type": "disabled"})
            return r, True
        r = cliente.beta.messages.create(
            model=modelo,
            max_tokens=16000,
            system=self.system,
            tools=self.tools,
            messages=msgs,
            output_config={"effort": self.cfg.get("esfuerzo", "low")},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        return r, False

    def _bucle(self, msgs, cid, texto_cliente=""):
        turno = {"texto": texto_cliente, "creados": set()}  # contexto de ESTE mensaje del cliente (sin estado compartido)
        for _ in range(MAX_VUELTAS):
            r, compatible = self._llamar_modelo(msgs)
            # servidores compatibles (qwen3 en Ollama): el razonamiento no se reenvía en el historial
            msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content if not (compatible and b.type in ("thinking", "redacted_thinking"))]})
            if r.stop_reason == "pause_turn":
                continue
            if r.stop_reason != "tool_use":
                texto = "\n".join(b.text for b in r.content if b.type == "text").strip()
                if r.stop_reason == "refusal" or not texto or any(b.type == "tool_use" for b in r.content):
                    msgs.pop()  # un turno vacío o con tool_use sin resultado haría fallar la API en todos los mensajes siguientes
                    return "Disculpe, no puedo ayudarle con eso por aquí. Si desea, le paso con una persona del equipo."
                return PREFIJO.sub("", texto)
            resultados = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                try:
                    res = {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(herramientas.ejecutar(self, b.name, b.input, cid, turno), ensure_ascii=False)}
                except (ValueError, TypeError, AttributeError, KeyError) as e:  # entrada mal formada del modelo
                    res = {"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}", "is_error": True}
                resultados.append(res)
            msgs.append({"role": "user", "content": resultados})
        return "Disculpe, se me complicó su solicitud. Una persona del equipo le contactará."

    # --- mensajes programados ---
    def pendientes(self):
        ahora = f"{self.ahora():%Y-%m-%d %H:%M}"
        return [m for m in self.listar("mensaje_programado") if m["estado"] == "pendiente" and m["enviar_en"] <= ahora]
