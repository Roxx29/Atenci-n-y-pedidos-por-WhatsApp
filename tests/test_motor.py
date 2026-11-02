"""Pruebas sin red: cliente de Claude falso. Ejecutar: python -m unittest discover tests"""
import datetime as dt
import hashlib
import hmac
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from motor import herramientas, whatsapp
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


if __name__ == "__main__":
    unittest.main()
