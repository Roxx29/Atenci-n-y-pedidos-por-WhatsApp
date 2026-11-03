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
| `crear_pedido`, `confirmar_pedido`, `reportar_pago`, `cancelar_pedido` | Pedidos con menú (`menu: menu.yaml`); el código calcula el total en centavos (ITBMS y envío), el modelo no puede marcar `pagado` |

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
- Ollama: ponga `OLLAMA_MODELS` en F:/ o E: (C: está lleno) antes de descargar modelos.

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
