# Motor de servicios

Un solo motor y muchos servicios. Cada servicio es una carpeta de configuración, no un programa nuevo.

```
Tú: "/crear-servicio cobranza por WhatsApp para administradoras de PH"
      │
      ▼  skill crear-servicio (agentes: servicio-* → programacion + marketing + logistica → software-qa → capitan)
servicios/cobranza-ph/
  servicio.yaml     qué hace, horario, herramientas permitidas, instrucciones
  conocimiento.md   información del negocio del cliente
  prueba.yaml       conversaciones de prueba
  oferta.md         a quién se vende, precio, ROI, guion
  operacion.md      checklist de puesta en marcha
      │
      ▼  python -m motor servir servicios/cobranza-ph
WhatsApp ⇄ webhook ⇄ Claude (claude-opus-5-5) + herramientas ⇄ SQLite
                                   └─ recordatorios y seguimientos programados (cada minuto)
```

## Herramientas del catálogo (`motor/herramientas.py`)

| Herramienta | Para qué |
|---|---|
| `consultar_disponibilidad`, `agendar_cita`, `cancelar_cita` | Citas con validación de horario y choques; recordatorio automático |
| `programar_mensaje` | Seguimientos, cobros y recordatorios futuros |
| `registrar`, `actualizar_registro`, `buscar` | Cualquier dato: leads, cobros, pedidos, siniestros, encuestas… |
| `escalar_a_humano` | Pasa la conversación al equipo |
| `crear_pedido`, `modificar_pedido`, `confirmar_pedido`, `reportar_pago`, `cancelar_pedido` | Pedidos con menú (`menu: menu.yaml`); el código calcula el total en centavos (ITBMS y envío), el modelo no puede marcar `pagado` |

Pedidos: un solo `por_confirmar` por cliente (otra llamada a `crear_pedido` lo actualiza, misma id). Caduca a `caducado` tras `pedidos.caduca_min` min (30 por defecto) sin confirmar; el modelo nunca ve centavos (`*_c`).
Un cliente no puede tener dos pedidos activos (`por_confirmar`, `confirmado`, `pago_reportado`, `pagado`, `en_preparacion`): `crear_pedido` falla y pide `modificar_pedido`; solo `aparte=true` crea otro (tope 3, y no si hay uno por confirmar).
`modificar_pedido` cambia un pedido existente recalculando todo en código: `confirmado` con total distinto vuelve a `por_confirmar` (`reconfirmar: true`, no se confirma en el mismo turno); con total igual sigue confirmado y avisa al restaurante. Ya pagado o en curso solo admite dirección, hora y notas; cambiar ítems, zona o modalidad crea un `escalado` y un aviso sin tocar el pedido. El panel muestra la columna Entrega (dirección, zona, hora, notas).

Con estas 8 herramientas se cubren la mayoría de los servicios del informe: citas, leads, cobranza, seguimiento, encuestas, reservas y recepción de reclamos.

## Comandos

```bash
pip install -r requirements.txt
python -m unittest discover tests              # pruebas sin red
python -m motor validar servicios/clinica-citas
export ANTHROPIC_API_KEY=...                   # para lo que sigue
python -m motor chat servicios/clinica-citas   # tú haces de cliente
python -m motor probar servicios/clinica-citas # corre prueba.yaml
python -m motor pedidos servicios/restaurante-pedidos [marcar <id> pagado|en_preparacion|listo|entregado]
python -m motor servir servicios/clinica-citas 8080   # producción (WhatsApp)
```

`servir` necesita `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET`. Se niega a arrancar sin la verificación de firma. Si faltan el token o el ID, los mensajes se imprimen en pantalla (modo seco) en lugar de enviarse.

## Canal web

`python -m motor web servicios/restaurante-pedidos [puerto]` (8080 por defecto) sirve una página de pedidos con chat y muestra `http://localhost:<puerto>/` y `http://<IP-local>:<puerto>/`. Identidad: cookie firmada (HMAC) con el teléfono como cid, igual que en WhatsApp. Secreto: `WEB_SECRET` o `<carpeta>/.web_secret` (autogenerado). Sin HTTPS (LAN). Rutas: `/`, `/menu`, `/aviso`, `/entrar`, `/chat`, `/avisos`, `/pedidos`. Aviso de privacidad: `web: {aviso_version: "2026-11-02", aviso: "aviso.md"}` en `servicio.yaml`; cada entrada guarda un registro `consentimiento`.

**Riesgo v1:** el teléfono no se verifica; quien escribe un teléfono ajeno entra con ese cid. Resolver con verificación por WhatsApp/SMS.

## Decisiones

- **Modelo `claude-opus-5-5` con esfuerzo `low`**: el chat necesita poco razonamiento. Cada servicio puede cambiarlo en `servicio.yaml`. Los *fallbacks* del servidor están activados: si el modelo rechaza una petición, la API reintenta con otro modelo.
- **Una tabla genérica `registros`**: añadir un servicio no requiere migraciones.
- **Conversación nueva tras 24 h sin mensajes**: coincide con la ventana de servicio de WhatsApp y evita tener que compactar el historial.
- **Seguridad**: firma HMAC del webhook obligatoria y tope de tamaño; mensajes duplicados descartados; solo las herramientas permitidas por el servicio; **aislamiento por cliente**: el teléfono lo pone el motor, no el modelo, así que un cliente no puede leer, cambiar ni enviar mensajes sobre datos de otro, aunque intente engañar al bot; datos mínimos según la Ley 81.
- **Coste**: con `claude-opus-5-5`, unos US$56/mes por cliente con 1.000 conversaciones (estimación, ver `investigacion/11`). Con `modelo: claude-haiku-5-5` serían unos US$5. Prueba la calidad antes de cambiar.

## Modelo y proveedor

- `LLM_MODEL` (variable de entorno) manda sobre `modelo` de `servicio.yaml`. Producción: `claude-haiku-5-5`, sin tocar código.
- `ANTHROPIC_BASE_URL` la lee el SDK. Si apunta a un servidor que no es anthropic.com (demo con Ollama: `http://localhost:11434`), el motor llama a `messages.create` solo con model, max_tokens, system, tools y messages (sin betas, fallbacks, caché ni `tool_choice`).
- `.env` en la raíz del repo: `python -m motor ...` lo carga al iniciar (`motor/entorno.py`; no pisa variables ya definidas, no se imprime). Si `ANTHROPIC_BASE_URL` contiene `deepseek` y no hay `ANTHROPIC_API_KEY`, se usa `DEEPSEEK_API_KEY`.
- DeepSeek: `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `LLM_MODEL=deepseek-v4-flash`.
- Respaldo: `LLM_RESPALDO_BASE_URL` + `LLM_RESPALDO_MODEL` (+ `LLM_RESPALDO_API_KEY`, por defecto `ollama`). Si el principal falla por 401/402/403/429, 5xx o red/timeout, se repite la petición en el respaldo (modo compatible, sin thinking), se registra un `aviso` una vez y no se reintenta el principal durante 300 s. 400/404/422 no activan el respaldo. Sin respaldo configurado, el error se propaga.
- Ollama: ponga `OLLAMA_MODELS` en F:/ o E: (C: está lleno) antes de descargar modelos.

## Comprobante de Yappy
- `POST /comprobante?pedido=N&referencia=` (cookie; cuerpo = imagen JPEG/PNG cruda, máx. 5 MB). Pasa el pedido a `pago_reportado` y avisa al restaurante.
- Se le quitan los metadatos (EXIF, texto, fecha), se guarda en `<servicio>/comprobantes/<nombre aleatorio>` (fuera de lo que sirve la web, ignorada por git) y solo la ve el personal desde el panel ("Ver comprobante"). Nunca llega al modelo, a los logs ni a los avisos.
- Se borra al marcar el pedido (pagado o posterior), al cancelarlo o a los `comprobantes.max_dias` (7) días; `purgar_comprobantes` también borra huérfanos y corre al arrancar web/panel y cada hora en el panel. Del pedido quedan en texto método, referencia, monto y fecha.

## Límites conocidos

- Un proceso por servicio, con SQLite local. Para muchos clientes: una base compartida y despliegue en contenedor.
- Fuera de la ventana de 24 h, Meta exige plantillas aprobadas. Hay que configurar `whatsapp.plantilla_recordatorio`.
- Los recordatorios con más de 6 h de retraso (tras una caída) se marcan como `vencido` y no se envían.
- Solo lee mensajes de texto. Audios e imágenes se reconocen pero no se procesan.
- No hay panel web: los datos se consultan en SQLite o se piden al bot.

## Compilar el panel (.exe, solo con PyInstaller de build)
```powershell
$env:PIP_CACHE_DIR="F:\cache\pip"; python -m venv F:\build\venv-panel
F:\build\venv-panel\Scripts\pip install -r requirements-build.txt
F:\build\venv-panel\Scripts\pyinstaller --onefile --windowed --name PanelPedidos --paths . --collect-data tzdata --exclude-module anthropic --distpath F:\build\dist --workpath F:\build\work --specpath F:\build\spec motor\panel.py
```

## Panel web (`/panel`)
Mismo servidor que `motor web`. Solo LAN (IP privada/loopback) y solo si hay PIN en `.env`: `PANEL_PIN` (6 dígitos, rol dueno) o `PANEL_PIN_HASH` (generar con `python -m motor.panel_web 123456`), y opcional `PANEL_PIN_PERSONAL[_HASH]` (rol personal). 5 fallos → bloqueo 5 min por IP; cookie `panel` (HttpOnly, SameSite=Strict, 30 min de inactividad, máx. 12 h).
API: `POST /panel/entrar {pin}`, `POST /panel/salir`, `GET /panel/estado?desde=N` (sondeo 3 s), `GET /panel/pedido/<id>` (teléfono y dirección completos), `GET /panel/comprobante/<id>` (solo con pago pendiente), `POST /panel/marcar {pedido_id, estado}` (pagado, pago_rechazado, en_preparacion, listo, entregado, cancelado; 409 si no procede), `POST /panel/escalado/<id>/atendido`. Lista con teléfono enmascarado y sin dirección. Registro en tabla `auditoria` (nombre de la persona, acción, pedido, hora) y `<estado>_por/_at` en el pedido.
- **Usuarios (un PIN por persona):** tabla `usuarios(nombre, rol dueno|personal|repartidor, pin_hash, activo)`, PIN solo con hash pbkdf2 y único entre activos. Semilla al arrancar: `PANEL_PIN[_HASH]` → usuario `Dueno`, `PANEL_PIN_PERSONAL[_HASH]` → `Personal`, `REPARTIDORES` → repartidores; solo si ese nombre aún no está en la BD (nunca pisa cambios). Cambiar el PIN o desactivar cierra las sesiones de esa persona. Debe quedar un dueño activo.
- **Ajustes (solo rol dueno, 403 para el resto; botón Ajustes):** `GET /panel/dueno/usuarios`, `POST /panel/dueno/usuario {nombre, rol?, pin?, activo?}` (crea o edita por nombre; reactivar exige PIN nuevo), `GET /panel/dueno/menu`, `POST /panel/dueno/plato {id, nombre?, precio?, categoria?, disponible?}`. Nunca devuelve PIN ni hash. Cada cambio se audita (`usuario_pin:Juan`, `plato_editar:sancocho`…).
- **Menú en la BD:** tabla `platos`; `menu.yaml` es semilla solo si la tabla está vacía. Después manda la BD (web `/menu`, bot y prompt leen la BD en cada turno). Si no hay platos en la BD, se usa el yaml.
- **Fotos de platos (opcional):** columnas `foto`, `foto2x` (BLOB WebP 160/320 px) y `foto_v` (hash) en `platos`, NULL = sin foto; van dentro de `datos.sqlite` (se respaldan con la BD). Subida: `POST /panel/dueno/foto?plato=<id>` (solo dueno), cuerpo = bytes crudos con `Content-Type` image/jpeg|png|webp (obligatorio: frena formularios ajenos), máx. 3 MB (413), tipo real comprobado con Pillow (415), máx. 40 Mpx/bomba (400), plato inexistente (404); se descartan EXIF/metadatos, recorte cuadrado centrado y WebP (≤12 KB el de 160). Responde `{ok, foto, foto2x}`; errores `{error}`. Quitar: `POST /panel/dueno/foto_borrar {plato}` → `{ok}`. Ambas se auditan (`foto_subir:<id>`, `foto_borrar:<id>`). Público: `GET /menu/foto/<id>.webp?v=<hash>` (160) y `...&s=2` (320), `image/webp`, `nosniff`, `Cache-Control: public, max-age=31536000, immutable`; límite propio de 300/min por IP. `GET /menu` y `GET /panel/dueno/menu` añaden por plato `foto` y `foto2x` (esas URL o `null`); la versión cambia con cada subida.
**Semilla inicial:** los PIN y el menú se cambian desde Ajustes; `.env` y `menu.yaml` solo sirven de semilla inicial (tras el primer arranque cambiarlos no hace nada). Si el PIN del `.env` no coincide con el de la BD, el arranque imprime un aviso (sin mostrar PIN).

## Ubicación del cliente y repartidor
- **Ubicación (opcional):** `POST /ubicacion {lat, lon, precision?}` (navegador) o `{enlace}` (Google Maps largo/corto o "lat, lon"; solo dominios de Google, los cortos se siguen con timeout de 4 s). Se valida solo el rango; si queda a más de `ubicacion.radio_km` (30 km de Chitré/Los Santos por defecto) se marca `fuera_de_zona` sin bloquear, y un enlace de Google sin coordenadas se guarda como enlace y se guarda en el pedido de entrega abierto como `ubicacion`; sin pedido aún, queda en memoria y se une al siguiente turno. El bot solo puede usar `guardar_ubicacion(pedido_id, enlace)` con un enlace que el cliente escribió; el modelo nunca ve coordenadas. Se borra al entregar, cancelar o caducar y como tope a las 24 h de crear el pedido (purga). WhatsApp: `whatsapp.ubicaciones_entrantes` + `guardar_ubicacion_wa` (mensajes `location`).
- **Repartidor:** `REPARTIDORES="Juan:482913,Pedro:739104"` (PIN de 6 dígitos o `pbkdf2$…`; nombres A-Za-z0-9_-; PIN únicos) o `REPARTIDOR_PIN`. Ruta `/repartidor` (PWA: manifest, sw.js, icono.svg; no es solo-LAN: PIN + bloqueo 5 fallos/5 min; sesión 12 h; HTTPS en producción). `GET /repartidor/pedidos` (solo los suyos en en_preparacion/listo/en_camino, sin historial), `POST /repartidor/marcar {pedido_id, estado}` (listo→en_camino→entregado), `/repartidor/entrar`, `/repartidor/salir`. Panel: `POST /panel/asignar {pedido_id, repartidor|""}` (entrega pagada o en curso); `/panel/estado` trae `repartidores`. Estado nuevo `en_camino` (avisa al cliente por mensaje).
