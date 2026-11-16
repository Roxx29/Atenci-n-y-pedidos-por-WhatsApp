"""Varios clientes en un proceso (`python -m motor alojar`): se elige por Host y nada se cruza. Ejecutar: python -m unittest tests.test_alojar"""
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock
from pathlib import Path

from motor import __main__ as cli
from motor import clientes, nucleo, panel_web, web
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso

PIN_A, PIN_B, PIN_GLOBAL = "482913", "739155", "555666"
ENV = {"WEB_SECRET": "maestro", "PANEL_PIN": PIN_GLOBAL, "PANEL_PIN_A": PIN_A, "PANEL_PIN_B": PIN_B,
       "REPARTIDORES_A": "Juan:271828", "REPARTIDORES_B": "Juan:314159",
       "RESTAURANTE_LAT": "1.0", "RESTAURANTE_LON": "1.0", "RESTAURANTE_LAT_A": "7.96", "RESTAURANTE_LON_A": "-80.43"}
FUERA = {"X-Forwarded-For": "8.8.8.8"}  # lo que Caddy añade a lo que reenvía de internet


class Base(unittest.TestCase):
    env = ENV

    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, self.env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        for slug in "abc":
            (self.d / slug).mkdir()
            (self.d / slug / "servicio.yaml").write_text(SERVICIO_PEDIDOS + f'marca: Marca-{slug}\nweb: {{aviso_version: "v1", aviso: "aviso.md"}}\n', encoding="utf-8")
            (self.d / slug / "menu.yaml").write_text(MENU, encoding="utf-8")
            (self.d / slug / "aviso.md").write_text("Aviso", encoding="utf-8")
        (self.d / "clientes.yaml").write_text("clientes:\n  a: {carpeta: a, dominio: a.com}\n  b: {carpeta: b, dominio: b.com}\n"
                                              "  c: {carpeta: c, dominio: c.com, activo: false}\n", encoding="utf-8")
        self.arrancar()

    def arrancar(self):
        self.srv = srv = web.crear_alojamiento(clientes.cargar(self.d / "clientes.yaml"), 0, ruta=self.d / "clientes.yaml")
        self.puerto = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [sv.cerrar() for sv in srv.servicios.values()])  # corre después de cerrar el servidor; incluye los cargados en caliente
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))

    def req(self, host, metodo, ruta, cuerpo=None, cookie=None, extra=None, con=None):
        c = con or http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        h = {"Content-Type": "application/json", **(extra or {})}
        if host:
            h["Host"] = host
        if cookie:
            h["Cookie"] = cookie
        c.putrequest(metodo, ruta, skip_host=True)
        data = json.dumps(cuerpo).encode() if cuerpo is not None else b""
        for k, v in {**h, "Content-Length": str(len(data))}.items():
            c.putheader(k, v)
        c.endheaders(data)
        r = c.getresponse()
        raw = r.read()
        if not con:
            c.close()
        return r.status, raw, (r.getheader("Set-Cookie") or "").split(";")[0]

    def entrar(self, host, tel="50760001111"):
        return self.req(host, "POST", "/entrar", {"nombre": "Ana", "telefono": tel, "consentimiento": True, "version_aviso": "v1"})

    def marca(self, host, **k):
        st, raw, _ = self.req(host, "GET", "/config", **k)
        return json.loads(raw).get("marca") if st == 200 else st


class TestHost(Base):
    def test_cada_dominio_sirve_a_su_cliente(self):
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.assertEqual(self.marca("b.com"), "Marca-b")

    def test_puerto_mayusculas_y_slug_localhost(self):
        for h in ("a.com:8080", "A.COM", "a.localhost", f"a.localhost:{self.puerto}", "a.com."):
            self.assertEqual(self.marca(h), "Marca-a", h)

    def test_host_desconocido_e_inactivo_dan_el_mismo_404(self):
        vistos = {self.req(h, m, r, c)[:2] for h in ("otro.com", "c.com", "c.localhost", None, "a.com.evil.com", "127.0.0.1", "xa.com")
                  for m, r, c in (("GET", "/", None), ("GET", "/config", None), ("POST", "/entrar", {"x": 1}), ("GET", "/panel", None))}
        self.assertEqual(len(vistos), 1, vistos)
        st, raw = vistos.pop()
        self.assertEqual(st, 404)
        self.assertNotIn(b"a.com", raw)

    def test_una_conexion_reutilizada_despacha_cada_peticion(self):
        """Caddy reutiliza conexiones entre dominios: el cliente lo decide cada petición, no la conexión."""
        con = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        self.addCleanup(con.close)
        self.assertEqual([self.marca(h, con=con) for h in ("a.com", "b.com", "a.com")], ["Marca-a", "Marca-b", "Marca-a"])

    def test_salud_comprueba_todas_las_bd(self):
        self.assertEqual(self.req("lo-que-sea", "GET", "/salud")[0], 200)
        self.srv.servicios["b"].cerrar()
        self.assertEqual(self.req("lo-que-sea", "GET", "/salud")[0], 503)

    def test_salud_desde_fuera_es_barato_y_no_toca_las_bd(self):
        """Lo que llega por Caddy (X-Forwarded-For) no puede forzar un SELECT por cliente ni saber si una BD está caída."""
        self.srv.servicios["b"].cerrar()
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.req("a.com", "GET", "/salud", extra=FUERA)[:2], (200, b'{"ok": true}'))
            self.assertEqual(self.req("a.com", "GET", "/salud")[0], 503)

    def test_proxy_delante_sin_PROXY_1_es_error_de_configuracion(self):
        """Caddy local sin PROXY=1: todos tendrían la IP 127.0.0.1 (panel abierto, bloqueo y límites compartidos). Mejor no servir."""
        for ruta in ("/config", "/panel", "/salud"):
            st, raw, _ = self.req("a.com", "GET", ruta, extra=FUERA)
            self.assertEqual(st, 500, ruta)
            self.assertIn("PROXY=1", json.loads(raw)["error"])
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.marca("a.com", extra=FUERA), "Marca-a")
        with unittest.mock.patch.object(web, "LOCALES", ()):  # no viene de un proxy de esta máquina: la cabecera la escribe cualquiera
            self.assertEqual(self.marca("a.com", extra=FUERA), "Marca-a")

    def test_escucha_solo_en_esta_maquina_por_defecto(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")


class TestConProxy(Base):
    env = {**ENV, "PROXY": "1", "PANEL_PUBLICO": "1", "PANEL_PUBLICO_B": "0", "ALOJAR_HOST": "0.0.0.0"}

    def test_slug_localhost_no_se_registra_tras_un_proxy(self):
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.assertEqual(self.marca("a.localhost"), 404)

    def test_panel_publico_por_cliente_con_la_global_por_defecto(self):
        self.assertEqual(self.req("a.com", "GET", "/panel", extra=FUERA)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/panel", extra=FUERA)[0], 404)

    def test_alojar_host(self):
        self.assertEqual(self.srv.server_address[0], "0.0.0.0")


class TestRecarga(Base):
    def escribir(self, texto):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text(texto, encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))  # que el mtime cambie aunque el reloj del disco sea grueso

    def clientes(self, a="true", c="false"):
        self.escribir(f"clientes:\n  a: {{carpeta: a, dominio: a.com, activo: {a}}}\n  b: {{carpeta: b, dominio: b.com}}\n"
                      f"  c: {{carpeta: c, dominio: c.com, activo: {c}}}\n")

    def ask(self, dominio):
        return self.req("127.0.0.1", "GET", f"/_ask?domain={dominio}")[0]

    def test_pausar_y_reactivar_sin_reiniciar(self):
        sv = self.srv.servicios["a"]
        self.assertEqual(self.marca("a.com"), "Marca-a")
        self.clientes(a="false")
        self.assertEqual([self.marca("a.com"), self.marca("a.localhost"), self.ask("a.com")], [404, 404, 404])
        self.assertEqual(self.marca("b.com"), "Marca-b")
        self.assertEqual(self.req("x", "GET", "/salud")[0], 200)
        self.clientes()
        self.assertEqual([self.marca("a.com"), self.ask("a.com")], ["Marca-a", 200])
        self.assertIs(self.srv.servicios["a"], sv)  # misma instancia: no se reabre la BD ni se pierden sesiones

    def test_activar_un_cliente_en_caliente(self):
        self.clientes(c="true")
        self.assertEqual([self.marca("c.com"), self.ask("c.com")], ["Marca-c", 200])

    def test_clientes_yaml_roto_en_caliente_conserva_la_lista(self):
        for roto in ("clientes: [", "clientes:\n  a: {carpeta: a, dominio: b.com}\n  b: {carpeta: b, dominio: b.com}\n", None):
            if roto is None:
                (self.d / "clientes.yaml").unlink()
            else:
                self.escribir(roto)
            self.assertEqual([self.marca("a.com"), self.marca("b.com"), self.marca("c.com")], ["Marca-a", "Marca-b", 404], roto)


class TestModulos(Base):
    """Módulo efectivo = lo que diga el operador en clientes.yaml; si no dice nada, el servicio.yaml del cliente."""

    def arrancar(self):
        with (self.d / "a" / "servicio.yaml").open("a", encoding="utf-8") as f:
            f.write("modulos: [estadisticas]\n")  # a lo contrató; b no
        super().arrancar()

    def escribir(self, a="", b=""):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text(f"clientes:\n  a: {{carpeta: a, dominio: a.com{a}}}\n  b: {{carpeta: b, dominio: b.com{b}}}\n", encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))

    def ver(self, host, ck):
        """(estado de la ruta de estadísticas, módulos que ve el panel) con la sesión ya abierta."""
        st, raw, _ = self.req(host, "GET", "/panel/estado", cookie=ck)
        self.assertEqual(st, 200)  # la sesión sigue viva tras cada recarga
        return self.req(host, "POST", "/panel/dueno/estadisticas", {}, cookie=ck)[0], json.loads(raw)["modulos"]

    def test_override_del_operador_en_caliente_sin_cerrar_sesiones(self):
        cka, ckb = (self.req(h, "POST", "/panel/entrar", {"pin": p})[2] for h, p in (("a.com", PIN_A), ("b.com", PIN_B)))
        con, sin = (200, ["estadisticas"]), (404, [])
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])  # sin override: servicio.yaml
        self.escribir(a=", modulos: []", b=", modulos: [estadisticas]")
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [sin, con])  # el operador manda
        self.escribir()
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])  # quitado: vuelve el servicio.yaml
        self.escribir(b=", modulos: [inventado]")  # inválido: sigue la lista anterior
        self.assertEqual([self.ver("a.com", cka), self.ver("b.com", ckb)], [con, sin])

    def test_el_dueno_no_puede_activarlo_desde_el_panel(self):
        ck = self.req("b.com", "POST", "/panel/entrar", {"pin": PIN_B})[2]
        self.req("b.com", "POST", "/panel/dueno/ajustes", {"modulos": ["estadisticas"], "modulos_operador": ["estadisticas"], "marca": "B"}, cookie=ck)
        self.assertEqual(self.ver("b.com", ck), (404, []))
        self.srv.servicios["b"].poner_ajuste("modulos", ["estadisticas"])  # ni aunque llegara a la tabla ajustes (la que escribe el panel)
        self.assertEqual(self.ver("b.com", ck), (404, []))
        self.escribir(b=", modulos: []")
        self.assertEqual(self.ver("b.com", ck), (404, []))


class TestBloqueo(unittest.TestCase):
    def test_ipv6_cuenta_por_64(self):
        """Un atacante con un /64 (lo que da cualquier proveedor) tiene 2^64 direcciones: cuentan como una."""
        b = panel_web.Bloqueo()
        for i in range(5):
            b.fallo(f"2001:db8:1:2::{i + 1}")
        self.assertTrue(b.bloqueada("2001:db8:1:2:ffff::9"))
        self.assertFalse(b.bloqueada("2001:db8:1:3::1"))
        b.acierto("2001:db8:1:3::1")

    def test_muchos_fallos_repartidos_solo_retrasan(self):
        b = panel_web.Bloqueo()
        for i in range(panel_web.Bloqueo.TOPE - 1):
            b.fallo(f"8.8.{i}.1")
        self.assertEqual(b.retraso(), 0)
        b.fallo("9.9.9.9")
        self.assertGreater(b.retraso(), 0)
        self.assertFalse(b.bloqueada("7.7.7.7"))  # el dueño, desde otra IP, entra (más despacio)
        with unittest.mock.patch.object(panel_web.time, "time", return_value=panel_web.time.time() + panel_web.Bloqueo.VENTANA_S + 1):
            self.assertEqual(b.retraso(), 0)


class TestAislamiento(Base):
    def test_cookie_de_cliente_de_a_no_vale_en_b(self):
        st, _, ck = self.entrar("a.com")
        self.assertEqual(st, 200)
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)
        self.assertEqual(self.req("b.localhost", "GET", "/pedidos", cookie=ck)[0], 401)

    def test_los_datos_van_a_la_bd_de_cada_carpeta(self):
        self.entrar("a.com")
        a, b = self.srv.servicios["a"], self.srv.servicios["b"]
        self.assertEqual(len(a.listar("consentimiento")), 1)
        self.assertEqual(b.listar("consentimiento"), [])
        self.assertTrue((self.d / "a" / "datos.sqlite").is_file() and (self.d / "b" / "datos.sqlite").is_file())
        self.assertNotIn("c", self.srv.servicios)  # el inactivo ni se abre
        self.assertFalse((self.d / "c" / "datos.sqlite").exists())

    def login(self, host, pin, ruta="/panel"):
        st, _, ck = self.req(host, "POST", ruta + "/entrar", {"pin": pin})
        return st, ck

    def test_pin_de_a_no_abre_b_ni_el_global_abre_ninguno(self):
        self.assertEqual(self.login("a.com", PIN_A)[0], 200)
        self.assertEqual(self.login("b.com", PIN_A)[0], 401)
        self.assertEqual(self.login("b.com", PIN_B)[0], 200)
        for h in ("a.com", "b.com"):
            self.assertEqual(self.login(h, PIN_GLOBAL)[0], 401)

    def test_cookie_del_panel_de_a_no_vale_en_b(self):
        _, ck = self.login("a.com", PIN_A)
        self.assertEqual(self.req("a.com", "GET", "/panel/estado", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/panel/estado", cookie=ck)[0], 401)

    def test_repartidor_del_mismo_nombre_no_cruza(self):
        st, ck = self.login("a.com", "271828", "/repartidor")
        self.assertEqual(st, 200)
        self.assertEqual(self.login("b.com", "271828", "/repartidor")[0], 401)
        self.assertEqual(self.req("a.com", "GET", "/repartidor/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/repartidor/pedidos", cookie=ck)[0], 401)

    def test_limites_por_cliente(self):
        for _ in range(10):
            self.req("a.com", "POST", "/entrar", {})
        self.assertEqual(self.req("a.com", "POST", "/entrar", {})[0], 429)
        self.assertEqual(self.req("b.com", "POST", "/entrar", {})[0], 400)

    def test_bloqueo_de_pin_por_cliente(self):
        for _ in range(5):
            self.login("a.com", "000111")
        self.assertEqual(self.login("a.com", PIN_A)[0], 429)
        self.assertEqual(self.login("b.com", PIN_B)[0], 200)

    def test_el_retraso_se_aplica_antes_de_comprobar_el_pin(self):
        with unittest.mock.patch.object(panel_web.Bloqueo, "retraso", return_value=0.3), unittest.mock.patch.object(web.time, "sleep") as dormir:
            self.assertEqual(self.login("a.com", PIN_A)[0], 200)
            self.assertEqual(self.login("a.com", "271828", "/repartidor")[0], 200)
        self.assertEqual([c.args for c in dormir.call_args_list], [(0.3,), (0.3,)])

    def test_ubicacion_del_restaurante_por_cliente(self):
        for host, pin, esperado in (("a.com", PIN_A, {"lat": 7.96, "lon": -80.43}), ("b.com", PIN_B, None)):
            _, ck = self.login(host, pin)
            rest = json.loads(self.req(host, "GET", "/panel/repartidores_mapa", cookie=ck)[1])["restaurante"]
            self.assertEqual(rest and {k: rest[k] for k in ("lat", "lon")}, esperado)


class TestSecreto(Base):
    env = {}  # sin WEB_SECRET: manda el .web_secret de cada carpeta

    def test_web_secret_copiado_con_la_carpeta_no_cruza_cookies(self):
        """«Nuevo cliente» copia una carpeta: aunque arrastre el .web_secret, el secreto de cada cliente sale del slug."""
        (self.d / "b" / ".web_secret").write_text((self.d / "a" / ".web_secret").read_text())
        self.srv.shutdown(), self.srv.server_close()
        for sv in self.srv.servicios.values():
            sv.cerrar()
        self.arrancar()
        _, _, ck = self.entrar("a.com")
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.req("b.com", "GET", "/pedidos", cookie=ck)[0], 401)

    def test_sin_pin_propio_no_hay_panel(self):
        self.assertEqual(self.req("a.com", "GET", "/panel")[0], 404)


class TestAsk(Base):
    def ask(self, dominio, **k):
        return self.req("127.0.0.1", "GET", f"/_ask?domain={dominio}", **k)[0]

    def test_solo_dominios_de_clientes_activos(self):
        self.assertEqual([self.ask(d) for d in ("a.com", "B.com", "c.com", "otro.com", "a.localhost", "")], [200, 200, 404, 404, 404, 404])

    def test_no_responde_a_peticiones_que_llegan_por_el_proxy(self):
        """Tras Caddy todo llega desde 127.0.0.1: lo de fuera se distingue por X-Forwarded-For."""
        with unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.ask("a.com", extra=FUERA), 404)
            self.assertEqual(self.req("a.com", "GET", "/_ask?domain=a.com", extra=FUERA)[0], 404)

    def test_no_responde_a_otra_maquina(self):
        with unittest.mock.patch.object(web, "LOCALES", ()):
            self.assertEqual(self.ask("a.com"), 404)

    def test_con_proxy_1_responde_al_proxy_de_la_red_privada(self):
        """Caddy en otro contenedor no llega desde 127.0.0.1 sino desde la red interna (docker-compose pone PROXY=1)."""
        with unittest.mock.patch.object(web, "LOCALES", ()), unittest.mock.patch.dict(os.environ, {"PROXY": "1"}):
            self.assertEqual(self.ask("a.com"), 200)
            self.assertEqual(self.ask("a.com", extra={"X-Forwarded-For": "8.8.8.8"}), 404)


class TestTurso(unittest.TestCase):
    def test_un_cliente_sin_credenciales_propias_no_usa_las_del_proceso(self):
        """Con las TURSO_* globales, los 20 clientes escribirían en la misma BD."""
        with unittest.mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://global", "TURSO_AUTH_TOKEN": "t", "TURSO_SOLO_LOCAL": "1"}):
            con, nube = nucleo.conectar(":memory:", nube=True, env={})
        con.close()
        self.assertFalse(nube)

    def test_alojar_abre_cada_servicio_con_su_entorno(self):
        visto = {}

        def falso(carpeta, env=None, **k):
            visto[Path(carpeta).name] = env
            return Servicio(carpeta, db=":memory:", cliente=ClienteFalso(()))

        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(web, "Servicio", falso), \
                unittest.mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://global", "TURSO_DATABASE_URL_A": "libsql://a"}, clear=True):
            (Path(tmp) / "a").mkdir()
            (Path(tmp) / "a" / "servicio.yaml").write_text(SERVICIO_PEDIDOS.replace("menu: menu.yaml\n", ""), encoding="utf-8")
            srv = web.crear_alojamiento([clientes.Cliente("a", Path(tmp) / "a", "a.com", True)], 0)
            srv.server_close()
            srv.servicios["a"].cerrar()
        self.assertEqual(visto, {"a": {"TURSO_DATABASE_URL": "libsql://a"}})


class TestComandos(unittest.TestCase):
    def setUp(self):
        p = unittest.mock.patch.object(cli, "cargar_env")  # el .env real no debe colarse en os.environ de las demás pruebas
        p.start()
        self.addCleanup(p.stop)

    def test_alojar_sin_clientes_yaml_da_error_claro(self):
        with self.assertRaises(SystemExit) as e:
            cli.main(["alojar", "no-existe-clientes.yaml"])
        self.assertIn("no-existe-clientes.yaml", str(e.exception))

    def test_alojar_pasa_archivo_y_puerto(self):
        with unittest.mock.patch.object(web, "alojar") as alojar:
            cli.main(["alojar"])
            cli.main(["alojar", "9001"])
            cli.main(["alojar", "otros.yaml", "9002"])
        self.assertEqual([c.args for c in alojar.call_args_list], [("clientes.yaml", 8080), ("clientes.yaml", 9001), ("otros.yaml", 9002)])

    def test_operador_sin_modulo_da_error_claro(self):
        with unittest.mock.patch.dict(sys.modules, {"motor.operador": None}), self.assertRaises(SystemExit) as e:
            cli.main(["operador"])
        self.assertIn("motor/operador.py", str(e.exception))

    def test_operador_llama_a_servir(self):
        llamadas = []
        falso = types.ModuleType("motor.operador")
        falso.servir = lambda ruta, puerto: llamadas.append((ruta, puerto))
        with unittest.mock.patch.dict(sys.modules, {"motor.operador": falso}):
            cli.main(["operador", "otros.yaml", "9100"])
        self.assertEqual(llamadas, [("otros.yaml", 9100)])


if __name__ == "__main__":
    unittest.main()
