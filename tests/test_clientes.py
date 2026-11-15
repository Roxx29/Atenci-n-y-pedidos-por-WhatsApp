"""Registro de clientes (clientes.yaml). Ejecutar: python -m unittest tests.test_clientes"""
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from motor import clientes

RAIZ = Path(__file__).resolve().parent.parent


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name).resolve()
        for n in ("a", "b"):
            self.carpeta(n)

    def carpeta(self, nombre, yaml_="nombre: x\n"):
        (self.d / nombre).mkdir()
        (self.d / nombre / "servicio.yaml").write_text(yaml_, encoding="utf-8")

    def cargar(self, texto):
        f = self.d / "clientes.yaml"
        f.write_text(texto, encoding="utf-8")
        return clientes.cargar(f)

    def falla(self, texto, trozo):
        with self.assertRaises(ValueError) as e:
            self.cargar(texto)
        self.assertIn(trozo, str(e.exception))


class TestCargar(Base):
    def test_carga_clientes_con_carpeta_resuelta_y_dominio_en_minusculas(self):
        cs = self.cargar("clientes:\n  uno: {carpeta: a, dominio: Uno.COM, activo: true}\n  dos-2: {carpeta: b, dominio: dos.com, activo: false}\n")
        self.assertEqual(cs, [clientes.Cliente("uno", self.d / "a", "uno.com", True), clientes.Cliente("dos-2", self.d / "b", "dos.com", False)])

    def test_activo_por_defecto(self):
        self.assertTrue(self.cargar("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n")[0].activo)

    def test_el_ejemplo_del_repo_carga(self):
        cs = clientes.cargar(RAIZ / "clientes.ejemplo.yaml")
        self.assertEqual([(c.slug, c.dominio) for c in cs], [("delifood", "delifood.com")])

    def test_sin_archivo(self):
        with self.assertRaises(OSError):
            clientes.cargar(self.d / "no-existe.yaml")

    def test_slug_invalido(self):
        for slug in ("Uno", "u_no", "u.no", '"../a"', '""'):
            self.falla(f"clientes:\n  {slug}: {{carpeta: a, dominio: uno.com}}\n", "slug")

    def test_slug_repetido(self):
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n  uno: {carpeta: b, dominio: dos.com}\n", "repetid")

    def test_dominio_repetido_aunque_cambien_mayusculas(self):
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n  dos: {carpeta: b, dominio: UNO.com}\n", "dominio")

    def test_dominio_invalido(self):
        for dom in ("uno", "uno.com:80", "http://uno.com", "uno.com/x", "uno.localhost", "", "a b.com"):
            self.falla(f'clientes:\n  uno: {{carpeta: a, dominio: "{dom}"}}\n', "dominio")

    def test_carpeta_inexistente_o_sin_servicio_yaml(self):
        (self.d / "vacia").mkdir()
        for c in ("nada", "vacia"):
            self.falla(f"clientes:\n  uno: {{carpeta: {c}, dominio: uno.com}}\n", "servicio.yaml")

    def test_dos_clientes_no_comparten_carpeta_ni_una_dentro_de_otra(self):
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n  dos: {carpeta: ./a, dominio: dos.com}\n", "carpeta")
        self.carpeta("a/dentro")
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n  dos: {carpeta: a/dentro, dominio: dos.com}\n", "carpeta")

    def test_archivos_del_servicio_no_salen_de_su_carpeta(self):
        (self.d / "b" / "secreto.md").write_text("de b", encoding="utf-8")
        for clave in ("menu: ../b/secreto.md", "conocimiento: ../b/secreto.md", "web: {aviso: ../b/secreto.md}", f"conocimiento: {(self.d / 'b' / 'secreto.md').as_posix()}"):
            (self.d / "a" / "servicio.yaml").write_text(f"nombre: x\n{clave}\n", encoding="utf-8")
            self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n", "fuera")

    def test_enlace_simbolico_hacia_otro_cliente(self):
        try:
            os.symlink(self.d / "b" / "servicio.yaml", self.d / "a" / "tema.css")
        except OSError:
            self.skipTest("sin permiso para crear enlaces simbólicos")
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com}\n", "fuera")

    def test_activo_debe_ser_booleano_y_formato_valido(self):
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com, activo: quiza}\n", "activo")
        self.falla("clientes: [uno]\n", "clientes")
        self.falla("otra: cosa\n", "clientes")
        self.falla("clientes:\n  uno: {carpeta: a, dominio: uno.com, sobra: 1}\n", "sobra")


class TestEntorno(Base):
    def test_cada_cliente_solo_ve_sus_variables_con_sufijo(self):
        env = {"PANEL_PIN": "111222", "PANEL_PIN_UNO": "482913", "PANEL_PIN_DOS_2": "739155", "REPARTIDORES_UNO": "Juan:271828",
               "TURSO_DATABASE_URL": "libsql://global", "TURSO_DATABASE_URL_DOS_2": "libsql://dos", "ANTHROPIC_API_KEY": "k"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(clientes.entorno("uno"), {"PANEL_PIN": "482913", "REPARTIDORES": "Juan:271828"})
            self.assertEqual(clientes.entorno("dos-2"), {"PANEL_PIN": "739155", "TURSO_DATABASE_URL": "libsql://dos"})

    def test_panel_publico_es_por_cliente_y_whatsapp_aun_no(self):
        with unittest.mock.patch.dict(os.environ, {"PANEL_PUBLICO_UNO": "1", "WHATSAPP_PHONE_ID_UNO": "123"}, clear=True):
            self.assertEqual(clientes.entorno("uno"), {"PANEL_PUBLICO": "1"})

    def test_slug_que_pisaria_variables_de_otro_no_carga(self):
        # PANEL_PIN_PERSONAL es el PIN del personal: un cliente «personal» lo heredaría como PIN de dueño
        self.falla("clientes:\n  personal: {carpeta: a, dominio: uno.com}\n", "variables")
        self.falla("clientes:\n  x: {carpeta: a, dominio: uno.com}\n  hash-x: {carpeta: b, dominio: dos.com}\n", "variables")


if __name__ == "__main__":
    unittest.main()
