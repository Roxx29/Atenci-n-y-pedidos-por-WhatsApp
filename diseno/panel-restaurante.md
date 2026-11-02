# Panel del restaurante (.exe Windows): diseño v1

Objetivo: el restaurante ve los pedidos y avisos casi en tiempo real y marca `pagado`, `en_preparacion`, `listo` y `entregado`. Lee el mismo `servicios/<slug>/datos.sqlite` que el bot y reutiliza `Servicio` y `herramientas.marcar_pedido`; no hay servidor nuevo.

## 1. UI: tkinter de la stdlib
`tkinter` + `ttk.Treeview` (comprobado: Tk 8.6 y SQLite 3.49 en este Python). No hacen falta dependencias de ejecución. Se descartan PySide/Qt (más de 100 MB) y una web local (un servidor más). Pantalla: tabla de pedidos activos (#, hora, estado, cliente, modalidad, total, líneas), botones con los 4 estados y una lista de avisos abajo.

## 2. Tiempo real y alertas
- Sondeo con `root.after(3000, refrescar)`. Cada 3 s se leen con `sv.listar("pedido")` los pedidos que no están en `entregado`/`cancelado` y los `aviso` con `id > ultimo_aviso`. Para un restaurante cuesta muy poco.
- Al arrancar, `ultimo_aviso = max(id)`, para que no suenen todos los avisos viejos. Si llega un aviso o un pedido nuevo: `winsound.MessageBeep()` (stdlib) y fila resaltada. Si la ventana estaba minimizada, se trae al frente con `deiconify()` + `lift()`.
- Se colorean por etiqueta los pedidos que esperan acción: `pago_reportado` en amarillo y `confirmado` en azul. El "visto" se guarda en memoria y no se escribe en la base.

## 3. Concurrencia bot y panel (dos procesos): cambio mínimo
Hoy `herramientas._transicion` lee el estado con `sv._q`, lo comprueba y luego llama a `sv.actualizar`. Son dos transacciones protegidas por `citas_lock`, que solo vale dentro de un proceso. Si el panel marca `pagado` justo cuando el bot cancela, los dos leen `confirmado` y gana el último en escribir.
Cambios, todos en `motor/nucleo.py` salvo el último:
1. `Servicio.__init__`: `sqlite3.connect(..., timeout=10, check_same_thread=False)` y luego `self.db.execute("PRAGMA journal_mode=WAL")`. Con WAL, las lecturas del panel no bloquean al bot. El timeout de 10 s hace que una escritura espere al otro proceso en lugar de fallar con `database is locked`.
2. `Servicio.actualizar(self, id, cambios, tipo=None, desde=None)`: dentro de `with self._db_lock, self.db:` ejecutar primero `self.db.execute("BEGIN IMMEDIATE")`, que toma el bloqueo de escritura entre procesos antes de leer. Después: SELECT, comprobar el tipo y, si se pasa `desde`, comprobar `estado in desde` (si no, `ValueError`). Luego UPDATE y devolver el dict ya fusionado. El `with self.db` hace el COMMIT o el ROLLBACK. Así también se arreglan las fusiones JSON concurrentes de `actualizar_registro`.
3. `motor/herramientas.py::_transicion`: se reduce a `return sv.actualizar(pedido_id, {"estado": hacia, **extra}, tipo="pedido", desde=desde)`, conservando el mensaje de error actual. `citas_lock` ya no hace falta aquí.
Resultado: la segunda escritura espera a la primera, vuelve a leer y falla con "El pedido está 'cancelado'…". El panel muestra el error en un `messagebox`. Alternativa descartada: `UPDATE … WHERE json_extract(datos,'$.estado') IN (…)` también sirve, pero obliga a fusionar el JSON en SQL. `BEGIN IMMEDIATE` mantiene la lógica en Python.
Nota: el mensaje "su pedido está listo" lo inserta el panel con `programar_mensaje`, y lo envía el hilo de `whatsapp.servir`, que lee la base cada 60 s. Funciona entre procesos sin cambios.

## 4. Cómo encuentra la carpeta del servicio
Orden: 1) argumento `PanelPedidos.exe <carpeta>` (sirve para un acceso directo); 2) la carpeta del .exe, si contiene `servicio.yaml`; 3) `filedialog.askdirectory()`. Después se crea `Servicio(carpeta)`, que valida el yaml y el menú. Si hay error, se muestra un `messagebox` y se sale.

## 5. Empaquetado (.exe)
- **PyInstaller, aprobado por el capitán solo para compilar.** Va en un `requirements-build.txt` aparte (`pyinstaller`, más `-r requirements.txt`) y nunca en `requirements.txt`. Caché y build van en F:.
- No se importa `anthropic`. Comprobado: `import motor.nucleo, motor.herramientas` deja `'anthropic' in sys.modules == False`, porque `Servicio.cliente` lo importa de forma diferida. Por eso se excluye. `yaml` y `tzdata` sí se incluyen (`zoneinfo` en Windows necesita `tzdata`).
- `motor/panel.py` usa imports absolutos (`from motor.nucleo import Servicio`), para que PyInstaller lo pueda usar como script de entrada.
```powershell
$env:PIP_CACHE_DIR="F:\cache\pip"; python -m venv F:\build\venv-panel
F:\build\venv-panel\Scripts\pip install -r requirements-build.txt
F:\build\venv-panel\Scripts\pyinstaller --onefile --windowed --name PanelPedidos --paths . --collect-data tzdata --exclude-module anthropic --distpath F:\build\dist --workpath F:\build\work --specpath F:\build\spec motor\panel.py
```

## 6. Archivos
- **Nuevos:** `motor/panel.py`, `requirements-build.txt` y una prueba en `tests/test_motor.py`. La lógica no visual va separada de la GUI: `activos(sv)` devuelve los pedidos no terminados, ordenados; `avisos_nuevos(sv, desde_id)` devuelve una lista y el nuevo `desde_id`; `elegir_carpeta(argv, exe_dir)` devuelve una ruta o `None`; `main(argv)` monta la parte tkinter, que es fina.
- **Comando:** `python -m motor panel <carpeta>` en `__main__.py` (import diferido de `.panel` para no cargar tkinter en el resto de comandos).
- **Prueba sin GUI:** dos `Servicio` sobre el mismo `datos.sqlite` temporal. A cancela un pedido `confirmado`; luego `marcar_pedido(B, id, "pagado")` lanza `ValueError` y el estado sigue `cancelado`. Además, `activos` excluye entregados y cancelados, y `avisos_nuevos` solo devuelve los de `id > desde_id`.

## 7. Riesgos y fuera de v1
- **Riesgos:**
  - `--onefile` se extrae en `%TEMP%`, que suele estar en C:, al arrancar (~15 MB). Si el PC del restaurante va justo de disco, usar `--onedir`.
  - El antivirus puede marcar el .exe sin firmar.
  - WAL no funciona en unidades de red: bot y panel deben estar en la misma máquina.
  - Los 3 s de sondeo son un retraso aceptado.
- **Fuera de v1:**
  - login o usuarios;
  - varias PCs o acceso remoto (más adelante: web local o API);
  - "visto" persistente de avisos;
  - imprimir comandas;
  - editar el menú;
  - historial o filtros por fecha;
  - firma de código e instalador.
