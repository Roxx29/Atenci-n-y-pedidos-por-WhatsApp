"""Pruebas sin red: cliente de Claude falso. Ejecutar: python -m unittest discover tests"""
import datetime as dt
import hashlib
import hmac
import json
import tempfile
import contextlib
import io
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace as NS

import yaml

from motor.__main__ import main
from motor import herramientas, panel, whatsapp
from motor.nucleo import Servicio

DEMO = Path(__file__).resolve().parent.parent / "servicios" / "_plantilla"


class Bloque(NS):
    def model_dump(self, exclude_none=False):
        return dict(vars(self))


class ClienteFalso:
    """Devuelve respuestas guionizadas y guarda lo que recibe."""

    def __init__(self, respuestas):
        self.respuestas, self.llamadas = list(respuestas), []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._create_simple)

    def _create_simple(self, **kw):
        self.simple = kw
        return self._create(**kw)

    def _create(self, **kw):
        self.llamadas.append(json.loads(json.dumps(kw["messages"])))
        return self.respuestas.pop(0)


def texto(t):
    return NS(stop_reason="end_turn", content=[Bloque(type="text", text=t)])


def herramienta(nombre, entrada, id="t1"):
    return NS(stop_reason="tool_use", content=[Bloque(type="tool_use", id=id, name=nombre, input=entrada)])


def proximo(sv, dia_semana, hora="09:00"):
    d = sv.ahora().date() + dt.timedelta(days=1)
    while d.weekday() != dia_semana:
        d += dt.timedelta(days=1)
    return f"{d:%Y-%m-%d} {hora}"


class Pruebas(unittest.TestCase):
    def sv(self, respuestas=()):
        return Servicio(DEMO, db=":memory:", cliente=ClienteFalso(respuestas))

    def test_agenda_valida_choque_y_horario(self):
        sv = self.sv()
        fh = proximo(sv, 1)  # martes
        r = herramientas.agendar_cita(sv, "Ana", "507", fh)
        self.assertTrue(r["ok"])
        with self.assertRaisesRegex(ValueError, "ocupada"):
            herramientas.agendar_cita(sv, "Luis", "508", fh)
        with self.assertRaisesRegex(ValueError, "horario"):
            herramientas.agendar_cita(sv, "Luis", "508", proximo(sv, 6))  # domingo cerrado
        with self.assertRaisesRegex(ValueError, "horario"):
            herramientas.agendar_cita(sv, "Luis", "508", fh[:11] + "09:10")  # no es inicio de turno
        libres = herramientas.consultar_disponibilidad(sv, fh[:10])["horas_libres"]
        self.assertNotIn("09:00", libres)
        self.assertIn("09:30", libres)

    def test_recordatorio_y_cancelacion(self):
        sv = self.sv()
        cid = herramientas.agendar_cita(sv, "Ana", "507", proximo(sv, 2, "10:00"))["cita_id"]
        [m] = sv.listar("mensaje_programado")
        self.assertEqual(m["cita_id"], cid)
        herramientas.cancelar_cita(sv, cid)
        self.assertEqual(sv.listar("mensaje_programado")[0]["estado"], "cancelado")
        self.assertIn(proximo(sv, 2, "10:00")[11:], herramientas.consultar_disponibilidad(sv, proximo(sv, 2)[:10])["horas_libres"])

    def test_registro_generico_y_busqueda(self):
        sv = self.sv()
        rid = herramientas.ejecutar(sv, "registrar", {"tipo": "lead", "datos": {"nombre": "Ana"}}, "507")["id"]
        herramientas.ejecutar(sv, "actualizar_registro", {"id": rid, "cambios": {"estado": "contactado"}}, "507")
        self.assertEqual(herramientas.ejecutar(sv, "buscar", {"tipo": "lead", "texto": "ana"}, "507")[0]["estado"], "contactado")
        with self.assertRaises(ValueError):
            herramientas.registrar(sv, "cita", {})

    def test_bucle_ejecuta_herramienta_y_devuelve_texto(self):
        sv = self.sv([herramienta("registrar", {"tipo": "lead", "datos": {"n": 1}}), texto("¡Listo!")])
        self.assertEqual(sv.responder("507", "hola"), "¡Listo!")
        ultimo = sv._cliente.llamadas[-1][-1]
        self.assertEqual(ultimo["content"][0]["type"], "tool_result")
        self.assertNotIn("is_error", ultimo["content"][0])
        self.assertEqual(len(sv.listar("lead")), 1)

    def test_error_de_herramienta_vuelve_al_modelo(self):
        sv = self.sv([herramienta("agendar_cita", {"nombre": "A", "telefono": "1", "fecha_hora": "mañana"}), texto("Indíqueme la fecha")])
        sv.responder("507", "cita")
        self.assertTrue(sv._cliente.llamadas[-1][-1]["content"][0]["is_error"])

    def test_herramienta_no_permitida(self):
        sv = self.sv()
        sv.cfg["herramientas"] = ["buscar"]
        with self.assertRaises(ValueError):
            herramientas.ejecutar(sv, "registrar", {"tipo": "x", "datos": {}}, "507")

    def test_historial_persiste_por_cliente(self):
        sv = self.sv([texto("a"), texto("b"), texto("c")])
        sv.responder("507", "uno")
        sv.responder("507", "dos")
        sv.responder("999", "otro")
        self.assertEqual(len(sv._cliente.llamadas[1]), 3)  # user, assistant, user
        self.assertEqual(len(sv._cliente.llamadas[2]), 1)

    def test_rechazo_da_respuesta_segura(self):
        sv = self.sv([NS(stop_reason="refusal", content=[])])
        self.assertIn("persona", sv.responder("507", "x"))

    def test_fecha_no_canonica_no_dobla_cita_y_rechazo_no_envenena_historial(self):
        sv = self.sv([NS(stop_reason="refusal", content=[]), texto("ok")])
        fh = proximo(sv, 1)
        herramientas.agendar_cita(sv, "Ana", "507", fh)
        d = f"{fh[:8]}{int(fh[8:10])} 9:00"  # "2026-11-3 9:00"
        with self.assertRaisesRegex(ValueError, "ocupada"):
            herramientas.agendar_cita(sv, "Luis", "508", d)
        sv.responder("507", "x")
        sv.responder("507", "y")
        self.assertNotIn([], [m["content"] for m in sv._cliente.llamadas[-1]])
        self.assertFalse(whatsapp.firma_valida(b"{}", "sha256=ñ", "s"))  # no debe lanzar TypeError

    def test_firma_y_deduplicado_webhook(self):
        cuerpo = b'{"a":1}'
        firma = "sha256=" + hmac.new(b"s", cuerpo, hashlib.sha256).hexdigest()
        self.assertTrue(whatsapp.firma_valida(cuerpo, firma, "s"))
        self.assertFalse(whatsapp.firma_valida(cuerpo, firma, "otro"))
        self.assertFalse(whatsapp.firma_valida(cuerpo, None, "s"))
        sv = self.sv()
        self.assertTrue(sv.primera_vez("wamid.1"))
        self.assertFalse(sv.primera_vez("wamid.1"))
        payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "w", "from": "507", "type": "text", "text": {"body": "hola"}}]}}]}]}
        self.assertEqual(list(whatsapp.mensajes_entrantes(payload)), [("w", "507", "hola")])


class Aislamiento(unittest.TestCase):
    def test_un_cliente_no_ve_ni_toca_datos_de_otro(self):
        sv = Servicio(DEMO, db=":memory:", cliente=ClienteFalso([]))
        e = lambda n, x, c: herramientas.ejecutar(sv, n, x, c)
        cid = e("agendar_cita", {"nombre": "Ana", "telefono": "999", "fecha_hora": proximo(sv, 1)}, "507")["cita_id"]
        self.assertEqual(sv.listar("cita")[0]["telefono"], "507")  # el motor ignora el teléfono que pase el modelo
        self.assertEqual(e("buscar", {"tipo": "cita"}, "666"), [])
        for n, x in (("cancelar_cita", {"cita_id": cid}), ("actualizar_registro", {"id": cid, "cambios": {"estado": "x"}})):
            with self.assertRaisesRegex(ValueError, "este cliente"):
                e(n, x, "666")
        with self.assertRaisesRegex(ValueError, "No se puede cambiar"):
            e("actualizar_registro", {"id": cid, "cambios": {"fecha_hora": "2030-01-01 09:00"}}, "507")
        e("programar_mensaje", {"telefono": "666", "enviar_en": proximo(sv, 1), "texto": "spam"}, "507")
        self.assertTrue(all(m["telefono"] == "507" for m in sv.listar("mensaje_programado")))

    def test_recordatorio_viejo_se_marca_vencido(self):
        sv = Servicio(DEMO, db=":memory:", cliente=ClienteFalso([]))
        herramientas.programar_mensaje(sv, "507", f"{sv.ahora() - dt.timedelta(hours=7):%Y-%m-%d %H:%M}", "tarde")
        whatsapp.enviar_pendientes(sv)
        self.assertEqual(sv.listar("mensaje_programado")[0]["estado"], "vencido")


SERVICIO_PEDIDOS = """nombre: Fonda
negocio: Fonda
horario: {dias: [lun, mar, mie, jue, vie, sab, dom], desde: "11:00", hasta: "21:00"}
menu: menu.yaml
pedidos: {impuesto_pct: 7, pedido_minimo: 8.00, cierre_cocina_min: 30, envio: {costo: 2.50}}
pago: {yappy: "@fonda", link: null}
herramientas: [crear_pedido, confirmar_pedido, reportar_pago, cancelar_pedido, buscar, actualizar_registro, registrar]
"""
MENU = """- {id: sancocho, nombre: Sancocho, precio: 5.00, disponible: true}
- {id: chicha, nombre: Chicha, precio: 1.75}
- {id: tres-leches, nombre: Tres leches, precio: 3.50, disponible: false}
"""


class Salvaguardas(unittest.TestCase):
    def sv(self, respuestas):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (Path(tmp.name) / "menu.yaml").write_text(MENU, encoding="utf-8")
        sv = Servicio(tmp.name, db=":memory:", cliente=ClienteFalso(respuestas))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        return sv

    NUEVO = {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "recogida", "nombre": "L"}

    def error_de(self, sv, vuelta):
        return sv.cliente.llamadas[vuelta][-1]["content"][0]

    def test_no_confirma_en_el_mismo_turno_de_crear(self):
        sv = self.sv([herramienta("crear_pedido", self.NUEVO), herramienta("confirmar_pedido", {"pedido_id": 1}, "t2"), texto("¿confirma?")])
        sv.responder("A", "quiero 2 sancochos")
        self.assertIn("espera su 'sí'", self.error_de(sv, 2)["content"])
        self.assertEqual(sv.listar("pedido")[0]["estado"], "por_confirmar")

    def test_confirma_en_turno_posterior(self):
        sv = self.sv([herramienta("crear_pedido", self.NUEVO), texto("¿confirma?"), herramienta("confirmar_pedido", {"pedido_id": 1}), texto("hecho")])
        sv.responder("A", "quiero 2 sancochos")
        with contextlib.redirect_stdout(io.StringIO()):
            sv.responder("A", "sí")
        self.assertEqual(sv.listar("pedido")[0]["estado"], "confirmado")

    def test_referencia_de_pago_debe_venir_del_cliente(self):
        sv = self.sv([herramienta("reportar_pago", {"pedido_id": 1, "metodo": "yappy", "referencia": "@elchitreano"}), texto("¿ref?")])
        herramientas.ejecutar(sv, "crear_pedido", {**self.NUEVO}, "A")
        herramientas.confirmar_pedido(sv, 1)
        sv.responder("A", "ya pagué por yappy")
        self.assertIn("no lo inventes", self.error_de(sv, 1)["content"])
        self.assertEqual(sv.listar("pedido")[0]["estado"], "confirmado")

    def test_referencia_presente_en_el_mensaje_se_acepta_sin_espacios_ni_mayusculas(self):
        sv = self.sv([herramienta("reportar_pago", {"pedido_id": 1, "metodo": "yappy", "referencia": "ab 123"}), texto("ok")])
        herramientas.ejecutar(sv, "crear_pedido", {**self.NUEVO}, "A")
        herramientas.confirmar_pedido(sv, 1)
        with contextlib.redirect_stdout(io.StringIO()):
            sv.responder("A", "pagué, ref AB123")
        self.assertEqual(sv.listar("pedido")[0]["estado"], "pago_reportado")

    def test_quita_prefijo_repetido_y_tolera_thinking(self):
        resp = NS(stop_reason="end_turn", content=[Bloque(type="thinking", thinking="mmm", signature="x"),
                                                    Bloque(type="text", text="[2026-11-02 17:55 · cliente 50760000002] Hola, ¿qué desea?")])
        sv = self.sv([resp])
        self.assertEqual(sv.responder("50760000002", "hola"), "Hola, ¿qué desea?")

    def test_thinking_no_se_reenvia_en_servidor_compatible(self):
        resp = NS(stop_reason="tool_use", content=[Bloque(type="thinking", thinking="mmm", signature="x"), Bloque(type="tool_use", id="t1", name="buscar", input={"tipo": "pedido"})])
        sv = self.sv([resp, texto("ok")])
        with mock.patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "http://localhost:11434"}):
            self.assertEqual(sv.responder("A", "hola"), "ok")
        self.assertEqual([b["type"] for b in sv.cliente.llamadas[1][1]["content"]], ["tool_use"])

    def test_probar_cierra_la_base(self):
        carpeta = Path(__file__).resolve().parent.parent / "servicios" / "restaurante-pedidos"
        casos = len(yaml.safe_load((carpeta / "prueba.yaml").read_text(encoding="utf-8"))["casos"])
        mensajes = sum(len(c["mensajes"]) for c in yaml.safe_load((carpeta / "prueba.yaml").read_text(encoding="utf-8"))["casos"])
        cerrado = []
        original = Servicio.cerrar
        with mock.patch.object(Servicio, "cerrar", lambda self: (cerrado.append(1), original(self))), contextlib.redirect_stdout(io.StringIO()):
            main(["probar", str(carpeta)], cliente=ClienteFalso([texto("ok")] * mensajes))
        self.assertEqual(cerrado, [1])


class Pedidos(unittest.TestCase):
    def sv(self, menu=MENU):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (Path(tmp.name) / "menu.yaml").write_text(menu, encoding="utf-8")
        sv = Servicio(tmp.name, db=":memory:", cliente=ClienteFalso([]))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        return sv

    def crear(self, sv, cliente="A", items=None, **kw):
        datos = {"items": items if items is not None else [{"id": "sancocho", "cantidad": 2}, {"id": "chicha", "cantidad": 2}], "modalidad": "entrega", "nombre": "Luis", "direccion": "Calle 53", **kw}
        return herramientas.ejecutar(sv, "crear_pedido", datos, cliente)

    def test_total_con_impuesto_y_envio(self):
        sv = self.sv()
        r = self.crear(sv)
        self.assertEqual(r["total_c"], 1695)  # 1350 + ITBMS 95 (94.5 redondea hacia arriba) + envío 250
        self.assertEqual(r["total"], "B/. 16.95")
        [p] = sv.listar("pedido")
        self.assertEqual((p["subtotal_c"], p["itbms_c"], p["envio_c"], p["estado"], p["telefono"]), (1350, 95, 250, "por_confirmar", "A"))
        self.assertEqual(self.crear(sv, modalidad="recogida")["total_c"], 1445)  # sin envío

    def test_rechazos_al_crear(self):
        sv = self.sv()
        for kw, msg in (({"items": [{"id": "pizza", "cantidad": 1}]}, "No existe"), ({"items": [{"id": "tres-leches", "cantidad": 3}]}, "no está disponible"),
                        ({"items": [{"id": "chicha", "cantidad": 2}]}, "mínimo"), ({"items": []}, "ítems"), ({"items": [{"id": "sancocho", "cantidad": 0}]}, "Cantidad"),
                        ({"items": [{"id": "sancocho", "cantidad": 2}], "direccion": ""}, "dirección"), ({"modalidad": "volando"}, "Modalidad"),
                        ({"hora_deseada": "2026-11-02 11:00"}, "ya pasó"), ({"hora_deseada": "2026-11-02 20:45"}, "cerrado")):
            with self.assertRaisesRegex(ValueError, msg):
                self.crear(sv, **kw)
        self.assertTrue(self.crear(sv, hora_deseada="2026-11-02 20:30")["ok"])  # 20:30 es el último minuto
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 20, 45, tzinfo=sv.tz)
        with self.assertRaisesRegex(ValueError, "cerrado"):
            self.crear(sv)
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        self.assertTrue(self.crear(sv, hora_deseada="lo antes posible")["ok"])  # texto libre: se usa la hora actual

    def test_cliente_b_no_ve_ni_toca_pedido_de_a(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        e = lambda n, x, c: herramientas.ejecutar(sv, n, x, c)
        self.assertEqual(e("buscar", {"tipo": "pedido"}, "B"), [])
        self.assertEqual(len(e("buscar", {"tipo": "pedido"}, "A")), 1)
        for n in ("confirmar_pedido", "cancelar_pedido"):
            with self.assertRaisesRegex(ValueError, "este cliente"):
                e(n, {"pedido_id": pid}, "B")
        with self.assertRaisesRegex(ValueError, "este cliente"):
            e("reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "1"}, "B")
        self.assertEqual(sv.listar("pedido")[0]["estado"], "por_confirmar")

    def test_el_modelo_no_puede_marcar_pagado(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        e = lambda n, x: herramientas.ejecutar(sv, n, x, "A")
        with self.assertRaisesRegex(ValueError, "Los pedidos no se cambian"):
            e("actualizar_registro", {"id": pid, "cambios": {"estado": "pagado"}})
        with self.assertRaisesRegex(ValueError, "específica"):
            e("registrar", {"tipo": "pedido", "datos": {"estado": "pagado", "total_c": 1}})
        with self.assertRaisesRegex(ValueError, "confirmado"):
            e("reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "9"})  # aún por_confirmar
        r = e("confirmar_pedido", {"pedido_id": pid})
        self.assertEqual(r["pago"], {"yappy": "@fonda"})  # sin el link null
        self.assertEqual(len(sv.listar("aviso")), 1)
        with self.assertRaisesRegex(ValueError, "por_confirmar"):
            e("confirmar_pedido", {"pedido_id": pid})
        e("reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "884512"})
        p = sv.listar("pedido")[0]
        self.assertEqual((p["estado"], p["referencia"]), ("pago_reportado", "884512"))
        with self.assertRaises(ValueError):
            e("cancelar_pedido", {"pedido_id": pid})
        aviso = sv.listar("aviso")[1]
        with self.assertRaises(ValueError):
            e("actualizar_registro", {"id": aviso["id"], "cambios": {"texto": "x"}})

    def test_marcar_listo_programa_mensaje_y_cancelado_no_se_marca(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        with self.assertRaisesRegex(ValueError, "inválido"):
            herramientas.marcar_pedido(sv, pid, "por_confirmar")
        for est in ("pagado", "en_preparacion", "listo"):  # por_confirmar: nada de esto aplica
            with self.assertRaises(ValueError):
                herramientas.marcar_pedido(sv, pid, est)
        herramientas.ejecutar(sv, "confirmar_pedido", {"pedido_id": pid}, "A")
        with self.assertRaises(ValueError):  # sin pagar no se prepara
            herramientas.marcar_pedido(sv, pid, "en_preparacion")
        herramientas.marcar_pedido(sv, pid, "pagado")
        herramientas.marcar_pedido(sv, pid, "en_preparacion")
        herramientas.marcar_pedido(sv, pid, "listo")
        [m] = sv.listar("mensaje_programado")
        self.assertEqual((m["telefono"], m["estado"]), ("A", "pendiente"))
        self.assertIn(f"#{pid}", m["texto"])
        pid2 = self.crear(sv)["pedido_id"]
        herramientas.ejecutar(sv, "cancelar_pedido", {"pedido_id": pid2}, "A")
        with self.assertRaisesRegex(ValueError, "cancelado"):
            herramientas.marcar_pedido(sv, pid2, "pagado")

    def test_zonas_e_impuesto_cero(self):
        sv = self.sv()
        sv.cfg["pedidos"] = {"envio": {"zonas": {"Chitré": 1.50, "Los Santos": 3.00}}}  # sin impuesto ni mínimo
        r = self.crear(sv, zona="chitre")  # sin mayúsculas ni tildes
        self.assertEqual((r["total_c"], sv.listar("pedido")[0]["itbms_c"]), (1350 + 150, 0))
        self.assertNotIn("ITBMS", r["resumen"])
        self.assertEqual(self.crear(sv, zona="LOS SANTOS")["total_c"], 1650)
        for zona in ("", "Panamá"):
            with self.assertRaisesRegex(ValueError, "Zona sin entrega"):
                self.crear(sv, zona=zona)
        self.assertEqual(self.crear(sv, modalidad="recogida")["total_c"], 1350)  # recogida no pide zona
        sv.cfg["pedidos"] = {"envio": {"costo": 2.5, "zonas": ["Obarrio"]}}
        self.assertEqual(self.crear(sv, "B", zona="obarrio")["total_c"], 1600)  # otro cliente: tope de 3 sin confirmar por cliente
        with self.assertRaisesRegex(ValueError, "Zona sin entrega"):
            self.crear(sv, zona="Costa del Este")

    def test_menu_invalido_falla(self):
        for menu in ("- {id: a, nombre: A, precio: 1}\n- {id: a, nombre: B, precio: 2}", "- {id: a, nombre: A, precio: 0}", "- {id: a, nombre: A, precio: 1.005}", "- {id: a, nombre: A}"):
            with self.assertRaises(ValueError, msg=menu):
                self.sv(menu)
        self.assertIn("AGOTADO", self.sv().system)

class Panel(unittest.TestCase):
    def dos_servicios(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (Path(tmp.name) / "menu.yaml").write_text(MENU, encoding="utf-8")
        a, b = Servicio(tmp.name), Servicio(tmp.name)  # dos "procesos" sobre el mismo datos.sqlite
        self.addCleanup(b.cerrar)
        self.addCleanup(a.cerrar)
        for s in (a, b):
            s.ahora = lambda s=s: dt.datetime(2026, 11, 2, 12, 0, tzinfo=s.tz)
        return a, b

    def test_cancelar_y_marcar_pagado_concurrentes(self):
        a, b = self.dos_servicios()
        pid = herramientas.ejecutar(a, "crear_pedido", {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "recogida", "nombre": "Luis"}, "A")["pedido_id"]
        herramientas.confirmar_pedido(a, pid)
        herramientas.cancelar_pedido(a, pid)
        with self.assertRaisesRegex(ValueError, "cancelado"):
            herramientas.marcar_pedido(b, pid, "pagado")
        self.assertEqual(b.listar("pedido")[0]["estado"], "cancelado")

    def test_activos_y_avisos_nuevos(self):
        a, b = self.dos_servicios()
        ids = [herramientas.ejecutar(a, "crear_pedido", {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "recogida", "nombre": "N"}, "A")["pedido_id"] for _ in range(3)]
        for i in ids[:2]:
            herramientas.confirmar_pedido(a, i)
        herramientas.cancelar_pedido(a, ids[0])
        herramientas.marcar_pedido(a, ids[1], "pagado")
        for e in ("en_preparacion", "listo", "entregado"):
            herramientas.marcar_pedido(a, ids[1], e)
        self.assertEqual([p["id"] for p in panel.activos(b)], [ids[2]])
        nuevos, ultimo = panel.avisos_nuevos(b, 0)
        self.assertEqual(len(nuevos), 2)
        self.assertEqual(panel.avisos_nuevos(b, ultimo), ([], ultimo))
        self.assertIsNone(panel.elegir_carpeta([], Path(a.dir) / "nada"))
        self.assertEqual(panel.elegir_carpeta([], a.dir), a.dir)
        self.assertEqual(panel.elegir_carpeta(["x"], a.dir), Path("x"))


if __name__ == "__main__":
    unittest.main()
