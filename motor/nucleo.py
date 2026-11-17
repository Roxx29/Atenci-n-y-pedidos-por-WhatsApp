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
- Para cambiar un pedido existente usa modificar_pedido, nunca crees otro; si ya está pagado y cambian ítems, zona o modalidad, la herramienta escala a una persona: díselo al cliente.
- Nunca digas importes en centavos ni los calcules: usa solo los textos "B/. 9.36" que devuelven las herramientas.
- Pide solo los datos necesarios; no solicites datos sensibles que el servicio no requiera (Ley 81 de 2019).
- Cada mensaje del cliente empieza con [fecha hora · cliente <teléfono>]: úsalo para fechas relativas ("mañana") y como teléfono del cliente.
- Si el cliente responde a un recordatorio, busca sus registros con buscar usando su teléfono.

Horario de atención: {horario}

Instrucciones específicas del servicio:
{instrucciones}

Información del negocio:
{conocimiento}"""


def conectar(ruta, nube=False, env=None):
    """Conexión a la BD. SQLite local salvo `nube` (solo cuando no se pasa db=): con TURSO_DATABASE_URL y TURSO_AUTH_TOKEN, réplica embebida libsql
    (archivo local + sync con Turso: sync al abrir y cada 2 s; las escrituras van a Turso). TURSO_SOLO_LOCAL=1: libsql sobre el archivo, sin red (diagnóstico).
    `env`: de dónde salen las TURSO_* (por defecto os.environ; en `motor alojar`, solo las del cliente: nunca comparten BD)."""
    env = os.environ if env is None else env
    url, token = env.get("TURSO_DATABASE_URL"), env.get("TURSO_AUTH_TOKEN")
    if nube and (url and token or env.get("TURSO_SOLO_LOCAL") == "1"):
        import libsql  # solo en este caso: el paquete es opcional (requirements-nube.txt)
        ruta = str(Path(ruta).with_name("datos-nube.db"))  # réplica propia: no se mezcla con datos.sqlite
        if url and token:
            return libsql.connect(ruta, timeout=10, _check_same_thread=False, sync_url=url, auth_token=token, sync_interval=2), True
        return libsql.connect(ruta, timeout=10, _check_same_thread=False), True
    return sqlite3.connect(ruta, timeout=10, check_same_thread=False), False  # timeout: espera al otro proceso (bot/panel)


class Servicio:
    def __init__(self, carpeta, db=None, cliente=None, cliente_respaldo=None, env=None):
        self.dir = Path(carpeta)
        self._cfg = yaml.safe_load((self.dir / "servicio.yaml").read_text(encoding="utf-8"))
        self.tz = ZoneInfo(self._cfg.get("zona_horaria", "America/Panama"))
        self.tools = herramientas.esquemas(self._cfg.get("herramientas", []))
        ruta = str(db or self.dir / "datos.sqlite")
        self.db, self.nube = conectar(ruta, nube=db is None, env=env)
        if ruta != ":memory:" and not self.nube:
            self.db.execute("PRAGMA journal_mode=WAL")  # las lecturas del panel no bloquean al bot
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS registros(id INTEGER PRIMARY KEY, tipo TEXT, datos TEXT, creado TEXT);"
            "CREATE TABLE IF NOT EXISTS conversaciones(id TEXT PRIMARY KEY, mensajes TEXT, actualizado REAL);"
            "CREATE TABLE IF NOT EXISTS vistos(id TEXT PRIMARY KEY);"
            "CREATE TABLE IF NOT EXISTS platos(orden INTEGER PRIMARY KEY, id TEXT UNIQUE, datos TEXT);"
            "CREATE TABLE IF NOT EXISTS usuarios(nombre TEXT PRIMARY KEY, rol TEXT NOT NULL, pin_hash TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1);"
            "CREATE TABLE IF NOT EXISTS aviso_repartidor(nombre TEXT PRIMARY KEY, version TEXT NOT NULL, at TEXT NOT NULL);"  # aviso de ubicación aceptado
            "CREATE TABLE IF NOT EXISTS comprobantes(nombre TEXT PRIMARY KEY, datos BLOB NOT NULL, creado REAL NOT NULL);"  # fotos de comprobantes de Yappy (antes archivos)
            "CREATE TABLE IF NOT EXISTS ajustes(clave TEXT PRIMARY KEY, valor TEXT NOT NULL);"  # lo que el dueño cambia desde el panel; pisa esa clave de servicio.yaml
            "CREATE TABLE IF NOT EXISTS qr_codigos(id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL, etiqueta TEXT NOT NULL, tipo TEXT NOT NULL, destino TEXT NOT NULL DEFAULT '', mesa INTEGER, activo INTEGER NOT NULL DEFAULT 1, creado TEXT NOT NULL, escaneos INTEGER NOT NULL DEFAULT 0);"  # módulo mesas: QR dinámicos (motor/mesa.py)
            "CREATE TABLE IF NOT EXISTS qr_dias(codigo_id INTEGER NOT NULL, dia TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(codigo_id, dia));"  # escaneos por código y día, sin datos personales
            "CREATE TABLE IF NOT EXISTS qr_config(clave TEXT PRIMARY KEY, valor NOT NULL);"  # estilo y logo de los QR de este cliente
            "CREATE TABLE IF NOT EXISTS uso_ia(dia TEXT PRIMARY KEY, mensajes INTEGER NOT NULL);"  # turnos de cliente que llegaron al modelo, por día (la consola del operador la lee)
        )
        if "foto" not in {c[1] for c in self.db.execute("PRAGMA table_info(platos)").fetchall()}:  # migración: fotos opcionales (WebP 160 y 320 px), NULL = sin foto
            self.db.executescript("ALTER TABLE platos ADD COLUMN foto BLOB; ALTER TABLE platos ADD COLUMN foto2x BLOB; ALTER TABLE platos ADD COLUMN foto_v TEXT;")
        self._db_lock = threading.Lock()
        herramientas.importar_comprobantes(self)  # archivos de versiones anteriores → tabla
        self.citas_lock = threading.Lock()
        self.pedidos_lock = threading.RLock()
        self._locks = defaultdict(threading.Lock)  # un turno a la vez por cliente
        self._cliente = cliente
        self._cliente_respaldo = cliente_respaldo or cliente  # en pruebas el mismo cliente falso sirve a ambos
        self._principal_caido_hasta = 0.0
        self._avisado = False
        self.tope_ia = 0  # turnos de IA al día; 0 = sin tope. Lo pone `alojar` desde clientes.yaml (clientes.tope_ia)
        self._tope_avisado = None  # día del último aviso de tope (ahorra la consulta en cada mensaje ya topado)
        self._reloj = time.monotonic
        self._menu_yaml = herramientas.cargar_menu(self.dir, self.cfg["menu"]) if self.cfg.get("menu") else {}
        if self._menu_yaml and not self._q("SELECT 1 FROM platos LIMIT 1"):  # semilla: menu.yaml solo la primera vez; luego manda la BD (panel)
            for it in self._menu_yaml.values():
                self.guardar_plato(it["id"], {k: v for k, v in it.items() if k not in ("id", "precio")})
        kb = self.dir / self.cfg.get("conocimiento", "conocimiento.md")
        self._kb = kb.read_text(encoding="utf-8") if kb.exists() else ""

    @property
    def cfg(self):
        """servicio.yaml con los ajustes del panel encima (tabla ajustes, leída en vivo: otro proceso los ve al momento). Sin ajustes es el dict del yaml tal cual."""
        a = {k: json.loads(v) for k, v in self._q("SELECT clave, valor FROM ajustes")}
        return {**self._cfg, **a} if a else self._cfg

    @cfg.setter
    def cfg(self, v):
        self._cfg = v

    def poner_ajuste(self, clave, valor):
        self._q("INSERT OR REPLACE INTO ajustes(clave, valor) VALUES (?,?)", (clave, json.dumps(valor, ensure_ascii=False)))

    @property
    def _prompt(self):
        """Se arma en cada turno: horario, cierre manual, teléfono y anuncio pueden cambiar desde el panel. El menú va al final (tras el conocimiento)."""
        c = self.cfg
        horario = json.dumps(c.get("horario", "no aplica"), ensure_ascii=False)
        if c.get("horario") and c.get("menu"):
            horario += ("\nSe puede pedir para más tarde o para otro día dentro del horario: pasa la hora en hora_deseada como AAAA-MM-DD HH:MM."
                        " Fuera de horario no digas que no se puede: ofrece programarlo para cuando abra.")
        if c.get("cerrado"):
            horario += "\nAHORA EL LOCAL ESTÁ CERRADO TEMPORALMENTE (lo cerró el equipo): no se reciben pedidos hasta que lo abran; díselo al cliente."
        if c.get("telefono"):
            horario += f"\nTeléfono del restaurante: {c['telefono']}. Si el cliente quiere hablar con una persona, además de escalar, dile que puede llamar a ese número."
        if c.get("anuncio"):
            horario += f"\nAviso del restaurante para los clientes: {c['anuncio']}"
        return PROMPT.format(negocio=c.get("negocio", c.get("nombre", "el negocio")), objetivo=c.get("objetivo", ""), horario=horario,
                             instrucciones=c.get("instrucciones", ""), conocimiento=self._kb)

    @property
    def menu(self):
        """{id: plato} de la BD (editable desde el panel); sin platos en la BD, el de menu.yaml."""
        bd = {i: {**json.loads(d), "id": i} for i, d in self._q("SELECT id, datos FROM platos ORDER BY orden")}
        return bd or self._menu_yaml

    def guardar_plato(self, iid, datos):
        """Inserta o reemplaza un plato (tabla platos; el orden de alta se conserva)."""
        self._q("INSERT INTO platos(id, datos) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET datos=excluded.datos", (iid, json.dumps(datos, ensure_ascii=False)))

    def borrar_plato(self, iid):
        """Quita un plato de la BD; False si no estaba."""
        with self._db_lock, self.db:
            return self.db.execute("DELETE FROM platos WHERE id=?", (iid,)).rowcount == 1

    def fotos(self):
        """{id: versión} de los platos con foto."""
        return dict(self._q("SELECT id, foto_v FROM platos WHERE foto_v IS NOT NULL"))

    def poner_foto(self, iid, foto=None, foto2x=None, v=None):
        """Guarda (o borra, con None) la foto de un plato existente; False si el plato no está en la BD."""
        with self._db_lock, self.db:
            return self.db.execute("UPDATE platos SET foto=?, foto2x=?, foto_v=? WHERE id=?", (foto, foto2x, v, iid)).rowcount == 1

    def foto(self, iid, grande=False):
        fila = self._q(f"SELECT {'foto2x' if grande else 'foto'} FROM platos WHERE id=? AND foto_v IS NOT NULL", (iid,))
        return fila[0][0] if fila else None

    @property
    def system(self):
        m = self.menu
        return self._prompt + (f"\n\nMenú (id | nombre | precio | categoría):\n{herramientas.texto_menu(m)}" if m else "")

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
            if not self.nube:  # ponytail: en la nube un solo proceso escribe (el lock de arriba); Turso no admite BEGIN IMMEDIATE en réplica
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

    def _cupo_ia(self):
        """Cuenta este turno en uso_ia y dice si cabe en el tope de hoy (día en la zona del servicio). Una sola sentencia:
        ni dos hilos ni dos procesos sobre la misma BD pasan del tope. Al toparse deja UN aviso al panel por día."""
        dia = f"{self.ahora():%Y-%m-%d}"
        tope = self.tope_ia
        with self._db_lock, self.db:
            if self.db.execute("INSERT INTO uso_ia(dia, mensajes) VALUES (?, 1) ON CONFLICT(dia) DO UPDATE SET mensajes = mensajes + 1"
                               " WHERE ? = 0 OR mensajes < ?", (dia, tope, tope)).rowcount == 1:
                return True
        aviso = "Se alcanzó el tope de IA de hoy"
        if self._tope_avisado != dia:
            with self._db_lock, self.db:  # el NOT EXISTS hace que sea uno solo: entre hilos, tras un reinicio o con otro proceso en la misma BD
                self.db.execute("INSERT INTO registros(tipo, datos, creado) SELECT 'aviso', ?, ? WHERE NOT EXISTS"
                                " (SELECT 1 FROM registros WHERE tipo='aviso' AND substr(creado, 1, 10)=? AND json_extract(datos, '$.texto')=?)",
                                (json.dumps({"texto": aviso, "telefono": None, "pedido_id": None}, ensure_ascii=False), self.ahora().isoformat(), dia, aviso))
            self._tope_avisado = dia
        return False

    def responder(self, cid, texto):
        if not self._cupo_ia():  # sin llamar al modelo ni tocar la conversación
            tel = self.cfg.get("telefono")
            return f"Hoy el asistente alcanzó su límite. Llama al restaurante{f' al {tel}' if tel else ''} para tu pedido."
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
