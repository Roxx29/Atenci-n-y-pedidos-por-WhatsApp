"""Registro de clientes alojados en un mismo proceso (`python -m motor alojar`). Cada cliente = una carpeta de servicio + un dominio.

clientes.yaml (no se sube al repo; ver clientes.ejemplo.yaml):
  clientes:
    delifood: {carpeta: servicios/restaurante-pedidos, dominio: delifood.com, activo: true}

Las variables del .env de cada cliente llevan su slug como sufijo (PANEL_PIN_DELIFOOD, REPARTIDORES_LAS_TABLAS...): ver `entorno`.
"""
import datetime as dt
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SLUG = re.compile(r"[a-z0-9-]+")
DOMINIO = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
# Lo que en `motor web` es global del proceso y aquí es de cada cliente. Sin sufijo no se lee nunca: un cliente no hereda las del proceso.
ENV_POR_CLIENTE = ("PANEL_PIN", "PANEL_PIN_HASH", "PANEL_PIN_PERSONAL", "PANEL_PIN_PERSONAL_HASH", "REPARTIDORES", "REPARTIDOR_PIN",
                   "RESTAURANTE_LAT", "RESTAURANTE_LON", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
                   # La única con valor por defecto global: PANEL_PUBLICO_<SLUG>=1/0 abre o cierra el panel de ESE cliente fuera de la LAN;
                   # sin ella vale la PANEL_PUBLICO del proceso (ver web.py `_panel`).
                   "PANEL_PUBLICO")
# "WHATSAPP_PHONE_ID" no está: WhatsApp multi-cliente (F3) enrutará por phone_number_id.


MODULOS = ("estadisticas",)  # catálogo de módulos extra que se pueden contratar (panel_web.modulos)
CAMPOS = {"carpeta", "dominio", "activo", "modulos", "pagado_hasta", "precio", "notas", "tope_ia_dia", "prueba_hasta"}
TOPE_IA_PRUEBA = 300  # turnos de IA al día de un cliente en prueba si nadie dice otra cosa (TOPE_IA_DIA lo cambia)


@dataclass(frozen=True)
class Cliente:
    slug: str
    carpeta: Path
    dominio: str
    activo: bool
    # Solo los pone el operador (clientes.yaml); el dueño del restaurante no los ve ni los cambia.
    modulos: list[str] | None = None  # None = manda el `modulos` de su servicio.yaml; [] = ninguno
    pagado_hasta: dt.date | None = None
    precio: float | None = None  # US$/mes
    notas: str = ""
    tope_ia_dia: int | None = None  # turnos de IA al día; 0 = sin tope; None = el valor por defecto (ver `tope_ia`)
    prueba_hasta: dt.date | None = None  # prueba gratis: pasado ese día, `alojar` lo trata como pausado


def tope_ia(c):
    """Tope efectivo de turnos de IA al día (0 = sin tope): el suyo; si no tiene, TOPE_IA_DIA (o 300) en prueba y sin tope los demás."""
    if c.tope_ia_dia is not None:
        return c.tope_ia_dia
    if c.prueba_hasta is None:
        return 0
    v = os.environ.get("TOPE_IA_DIA", "")
    return int(v) if v.isascii() and v.isdigit() else TOPE_IA_PRUEBA  # mal escrita: no deja la prueba sin tope


def _fecha(slug, c, campo):
    """Fecha sin hora de `campo` (date de YAML o texto ISO), None si no está. ValueError con el campo que falla."""
    v = c.get(campo)
    if isinstance(v, str):
        try:
            v = dt.date.fromisoformat(v)
        except ValueError:
            v = False
    if campo in c and (not isinstance(v, dt.date) or isinstance(v, dt.datetime)):
        raise ValueError(f"cliente {slug}: {campo} debe ser una fecha AAAA-MM-DD")
    return v


def _operador(slug, c):
    """Valida modulos, pagado_hasta, precio, notas, tope_ia_dia y prueba_hasta de un cliente. ValueError con el campo que falla."""
    mods, precio, notas, tope = c.get("modulos"), c.get("precio"), c.get("notas", ""), c.get("tope_ia_dia")
    if "modulos" in c and not (isinstance(mods, list) and all(isinstance(m, str) and m in MODULOS for m in mods)):
        raise ValueError(f"cliente {slug}: modulos debe ser una lista con solo {', '.join(MODULOS)} (o [] para ninguno)")
    hasta, prueba = _fecha(slug, c, "pagado_hasta"), _fecha(slug, c, "prueba_hasta")
    if "tope_ia_dia" in c and not (isinstance(tope, int) and not isinstance(tope, bool) and 0 <= tope <= 100000):
        raise ValueError(f"cliente {slug}: tope_ia_dia debe ser un entero entre 0 y 100000 (0 = sin tope)")
    if "precio" in c and not (isinstance(precio, (int, float)) and not isinstance(precio, bool) and 0 <= precio < math.inf):
        raise ValueError(f"cliente {slug}: precio debe ser un número mayor o igual que 0 (US$/mes)")
    if not (isinstance(notas, str) and len(notas) <= 500):
        raise ValueError(f"cliente {slug}: notas debe ser un texto de 500 caracteres como máximo")
    return {"modulos": list(dict.fromkeys(mods)) if "modulos" in c else None, "pagado_hasta": hasta,
            "precio": float(precio) if "precio" in c else None, "notas": notas, "tope_ia_dia": tope, "prueba_hasta": prueba}


class _SinRepetidos(yaml.SafeLoader):
    """yaml.safe_load se queda con la última clave repetida sin avisar: aquí un slug repetido es un error."""

    def construct_mapping(self, node, deep=False):
        m = super().construct_mapping(node, deep)
        if len(m) != len(node.value):
            raise ValueError("clientes.yaml: hay una clave repetida (¿slug repetido?)")
        return m


def _env(nombre, slug):
    return f"{nombre}_{slug.upper().replace('-', '_')}"


def entorno(slug):
    """Variables de ese cliente, ya sin sufijo: {"PANEL_PIN": os.environ["PANEL_PIN_<SLUG>"], ...}. Solo las suyas."""
    return {n: os.environ[_env(n, slug)] for n in ENV_POR_CLIENTE if os.environ.get(_env(n, slug))}


def _fuera(carpeta, cfg):
    """Primer archivo del servicio que cae fuera de su carpeta (ruta con `..`, absoluta o enlace simbólico), o None."""
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    refs = [str(r) for r in (cfg.get("menu"), cfg.get("conocimiento"), web.get("aviso")) if r]
    for f in [carpeta / r for r in refs] + list(carpeta.iterdir()):
        if not f.resolve().is_relative_to(carpeta):
            return f
    return None


def cargar(ruta="clientes.yaml"):
    """Lee y valida clientes.yaml. ValueError con el motivo si algo permitiría mezclar clientes; OSError si no existe."""
    ruta = Path(ruta)
    try:
        d = yaml.load(ruta.read_text(encoding="utf-8"), Loader=_SinRepetidos)  # noqa: S506 — subclase de SafeLoader
    except ValueError as e:
        if str(e).startswith("clientes.yaml"):
            raise
        raise ValueError(f"clientes.yaml: fecha imposible ({e}); pagado_hasta y prueba_hasta deben ser AAAA-MM-DD") from e  # p. ej. 2026-13-01: lo lanza el propio YAML
    if not (isinstance(d, dict) and isinstance(d.get("clientes"), dict)):
        raise ValueError("clientes.yaml debe tener `clientes:` con un cliente por slug")
    out, nombres_env = [], set(ENV_POR_CLIENTE)
    for slug, c in d["clientes"].items():
        if not (isinstance(slug, str) and SLUG.fullmatch(slug)):
            raise ValueError(f"slug inválido {slug!r}: solo minúsculas, números y guiones")
        if not isinstance(c, dict) or set(c) - CAMPOS:
            raise ValueError(f"cliente {slug}: solo admite {', '.join(sorted(CAMPOS))} (sobra {sorted(map(str, set(c) - CAMPOS)) if isinstance(c, dict) else c!r})")
        dominio = str(c.get("dominio") or "").lower()
        if not DOMINIO.fullmatch(dominio) or dominio.endswith(".localhost"):
            raise ValueError(f"cliente {slug}: dominio inválido {dominio!r} (sin https://, puerto ni ruta; .localhost está reservado)")
        if dominio in {x.dominio for x in out}:
            raise ValueError(f"cliente {slug}: el dominio {dominio} ya es de otro cliente")
        activo = c.get("activo", True)
        if not isinstance(activo, bool):
            raise ValueError(f"cliente {slug}: activo debe ser true o false")
        carpeta = (ruta.parent / str(c.get("carpeta") or "")).resolve()
        if not (c.get("carpeta") and (carpeta / "servicio.yaml").is_file()):
            raise ValueError(f"cliente {slug}: la carpeta {c.get('carpeta')!r} no existe o no tiene servicio.yaml")
        if any(carpeta == x.carpeta or carpeta.is_relative_to(x.carpeta) or x.carpeta.is_relative_to(carpeta) for x in out):
            raise ValueError(f"cliente {slug}: su carpeta es la de otro cliente o está dentro de ella (compartirían datos)")
        cfg = yaml.safe_load((carpeta / "servicio.yaml").read_text(encoding="utf-8"))
        if f := _fuera(carpeta, cfg if isinstance(cfg, dict) else {}):
            raise ValueError(f"cliente {slug}: {f.name} queda fuera de su carpeta")
        suyas = {_env(n, slug) for n in ENV_POR_CLIENTE}
        if suyas & nombres_env:  # p. ej. slug «personal»: PANEL_PIN_PERSONAL ya significa otra cosa
            raise ValueError(f"cliente {slug}: sus variables del .env se pisarían con las de otro ({', '.join(sorted(suyas & nombres_env))}); elija otro slug")
        nombres_env |= suyas
        out.append(Cliente(slug, carpeta, dominio, activo, **_operador(slug, c)))
    return out
