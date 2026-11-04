"""python -m motor migrar <carpeta> [--forzar]: copia las tablas de servicios/<slug>/datos.sqlite a la BD Turso del entorno."""
import os
import sqlite3
from pathlib import Path

from .nucleo import conectar


def migrar(carpeta, forzar=False, destino=None):
    """Copia todas las tablas de datos.sqlite a `destino` (por defecto, Turso según el entorno). Falla si no hay variables o si el destino ya tiene datos (salvo forzar)."""
    origen = Path(carpeta) / "datos.sqlite"
    if not origen.is_file():
        raise SystemExit(f"No existe {origen}")
    if destino is None:
        if not (os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN")):
            raise SystemExit("Faltan TURSO_DATABASE_URL y TURSO_AUTH_TOKEN en el entorno.")
        destino = conectar(str(origen), nube=True)[0]
    hay = [t for (t,) in destino.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    llenas = [t for t in hay if destino.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchall()]
    if llenas and not forzar:
        raise SystemExit(f"La BD remota ya tiene datos ({', '.join(llenas)}). Use --forzar para sobrescribir.")
    src = sqlite3.connect(origen)
    try:
        for t, sql in src.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            if t not in hay:
                destino.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
            cols = [c[1] for c in src.execute(f'PRAGMA table_info("{t}")').fetchall()]
            lista = ", ".join(f'"{c}"' for c in cols)
            filas = src.execute(f'SELECT {lista} FROM "{t}"').fetchall()
            with destino:
                destino.execute(f'DELETE FROM "{t}"')
                destino.executemany(f'INSERT INTO "{t}"({lista}) VALUES ({",".join("?" * len(cols))})', filas)
            print(f"{t}: {len(filas)} filas")
    finally:
        src.close()
