# Ideas y pendientes

Apuntar al momento. Formato: `- [ ] AAAA-MM-DD — texto (autor)`. Marcar `[x]` al terminar.

## Ideas
- 2026-11-02 — Reservas de mesa con capacidad por turno (fuera de v1) (agenteservicios)
- 2026-11-02 — Factura electrónica DGI por pedido (fuera de v1) (agenteservicios)
- 2026-11-02 — Link de pago (Tilopay/PagueloFacil, requiere cuenta de comercio) (agenteservicios)
- 2026-11-02 — qwen3:8b repite al cliente la nota interna de reportar_pago ("No lo des por confirmado ni prometas…"): separar en la respuesta un texto para el cliente y una instrucción para el bot (agentedeprogramacion)
- 2026-11-02 — Calidad de qwen3:8b en la demo: escala ítems agotados sin necesidad y mezcla "efectivo" en el pago; mejorar instrucciones o probar un modelo mayor si molesta en demos (Haiku en producción) (agentedeprogramacion)

## Próximo por hacer
- [x] 2026-11-02 — Definir el proyecto: restaurante; v1 = responder preguntas, tomar pedidos, pasar a humano, cobrar (capitan)
- [x] 2026-11-02 — Diseñar `restaurante-pedidos` sobre el motor: diseno/restaurante-pedidos.md (agenteservicios)
- [x] 2026-11-02 — Dónde va el motor: se copia a este repo; Investigacion-de-servicios no se toca (capitan)
- [x] 2026-11-02 — Copiar motor + implementar §2 de diseno/restaurante-pedidos.md + chat de prueba local (agentedeprogramacion)
- [x] 2026-11-02 — Dudas §4 respondidas (usuario): demo como si fuera real (para vender a restaurantes); menú y precios de ejemplo; dirección en Chitré, Herrera; horario típico de Chitré; entregas solo Chitré y Los Santos; ITBMS opcional por restaurante; envío fijo o por zona (configurable); pedido mínimo configurable; pago antes de preparar; el personal verifica el Yappy tras una notificación; pedidos en un panel ejecutable de escritorio; modelo gratuito para la demo y Haiku cuando haya cliente
- [x] 2026-11-02 — Investigar modelo gratuito con herramientas para la demo, repartidor propio vs PedidosYa y horario típico en Chitré: Ollama (qwen3:8b), repartidor propio + recoger en local; ver investigacion/2026-11-02-demo-restaurante.md (agenteinvestigacion)
- [x] 2026-11-02 — Panel de escritorio (.exe) para pedidos y verificación de Yappy: F:\build\dist\PanelPedidos.exe (agentedeprogramacion)
- [x] 2026-11-02 — Panel: la caja de avisos carga los pendientes al abrir. Firma del .exe: no por ahora (capitan) (agentedeprogramacion)
- [ ] 2026-11-02 — Conectar WhatsApp (Meta Cloud API): token temporal en .env; faltan WHATSAPP_PHONE_ID y WHATSAPP_APP_SECRET, que el motor cargue .env, túnel HTTPS y webhook (usuario/capitan)
- [x] 2026-11-02 — Canal alternativo mientras no hay Meta: el usuario eligió la WEB de pedidos con chat (Telegram descartado) (capitan)
- [ ] 2026-11-02 — Web de pedidos: API motor/web.py (agentedeprogramacion), diseño (disenador), privacidad Ley 81 (legal-datos), arranque y túnel de demo (devops)
- [ ] 2026-11-02 — Pasar `enviar` como parámetro a `enviar_pendientes` (whatsapp.py:60) para compartirlo entre canales (agenteservicios)
- [ ] 2026-11-02 — Token permanente (usuario del sistema) antes de un restaurante real (usuario)
- [ ] 2026-11-02 — Confirmar en campo: horario y día de cierre del restaurante demo, tarifa de envío Chitré↔La Villa, y si PedidosYa cubre Chitré (agenteinvestigacion)
- [x] 2026-11-02 — Motor: modelo por env LLM_MODEL + ANTHROPIC_BASE_URL para usar Ollama en demo; quitar tool_choice (agentedeprogramacion)
- [ ] 2026-11-02 — Revisión de abogado de legal/2026-11-02-web-pedidos.md (6 pendientes al final del archivo) (legal-datos)

- [ ] 2026-11-02 — Web de pedidos: completar los [corchetes] del aviso de privacidad (RUC, dirección, proveedor IA, plazo, contacto) y revisión de abogado.
- [ ] 2026-11-02 — Canal alternativo mientras no hay Meta: Telegram (long polling, recomendado por agenteinvestigacion) o web de pedidos con chat (recomendado por agenteservicios). Decide el usuario (capitan)
- [x] 2026-11-02 — Pasar `enviar` como parámetro a `enviar_pendientes` (whatsapp.py:60) para compartirlo entre canales (agenteservicios)
- [ ] 2026-11-02 — Token permanente (usuario del sistema) antes de un restaurante real (usuario)
- [ ] 2026-11-02 — Confirmar en campo: horario y día de cierre del restaurante demo, tarifa de envío Chitré↔La Villa, y si PedidosYa cubre Chitré (agenteinvestigacion)
- [x] 2026-11-02 — Motor: modelo por env LLM_MODEL + ANTHROPIC_BASE_URL para usar Ollama en demo; quitar tool_choice (agentedeprogramacion)
- [ ] 2026-11-02 — Legal §2.3-2.4 (legal/2026-11-02-web-pedidos.md, rama AgenteLegal): tarea al arrancar que borre chats a los 90 días, anonimice pedidos y borre clientes a los 12 meses; en el panel buscar por teléfono, exportar y borrar (ARCO) con registro de la solicitud y secure_delete; texto del aviso con datos del restaurante en servicios/<slug>/aviso.md (agentedeprogramacion + legal-datos)
- [ ] 2026-11-02 — Canal web: verificar el teléfono (WhatsApp/SMS) para que nadie entre con un número ajeno; HTTPS obligatorio en producción (devops) (agentedeprogramacion)
- [ ] 2026-11-02 — Canal web: verificar el teléfono (WhatsApp/SMS); en v1 quien escribe un teléfono ajeno entra con ese cid y ve/usa sus datos (agentedeprogramacion)
- [ ] 2026-11-02 — Canal web: retención/borrado/ARCO de consentimientos y datos, y redactar aviso.md con legal-datos (legal-datos)

- [devops] Producción: túnel Cloudflare con nombre (cuenta + dominio) en vez de quick tunnel; y contraseña/limitar la web pública de demo.
- [devops] cloudflared: ver despliegue/cloudflare.md (túnel con nombre listo; falta que el usuario cree túnel y token). Webhook de WhatsApp pendiente.
- [ ] 2026-11-02 — Web: en modo demo mostrar "el bot está pensando, puede tardar ~30 s" mientras responde el modelo local (capitan) (disenador)
- [ ] Web: probar en un móvil real la subida del comprobante (galería, cámara y fotos HEIC de iPhone).
- [ ] 2026-11-02 — Medir qwen3:4b como respaldo Ollama (tiempo + acierto crear_pedido) cuando haya memoria libre; ver investigacion/2026-11-02-modelo-rapido.md (agenteinvestigacion)
- Panel web: avisos tipo 'sistema' (IA sin saldo / WhatsApp caído), PIN por persona, rol dueno (buscar/exportar/ARCO), auditoría visible 12 meses, estado_at al caducar, HTTPS.
- BLOQUEANTE PARA PRODUCCIÓN (legal, revisión de e48b732): (1) un PIN por persona: tabla usuarios(nombre, rol, pin_hash) y nombre en auditoria/_por; (2) vía ARCO de borrado/exportación para dueno (mientras tanto, procedimiento manual SQL documentado); (3) PIN solo con hash (quitar PANEL_PIN en texto o convertirlo al arrancar). No urgente: retención de 12 meses de auditoría y registro de cambio de PIN. La auditoría hoy no se expone por ninguna ruta (punto 5 cumplido).
- [ ] Panel web: pantallas del rol dueño (buscar cliente por teléfono, exportar/borrar datos, gestionar PINs, registro de acciones) — legal 2.8.
- [ ] Panel web: probar en la tablet real del local (sonido tras el PIN, tamaño de botones, luz de día).
- [ ] 2026-11-02 — Panel web antes de producción (legal §2.8): un PIN por persona, ARCO exportar/borrar para el dueno, PIN solo con hash, teléfono enmascarado en pedidos cerrados, auditoría solo para el dueno, retención de 12 meses (agentedeprogramacion)
- Ubicación/repartidor: seguimiento GPS del repartidor, ubicación por WhatsApp ya enchufada (probar con Meta), PIN de repartidor solo hash en producción, campo externo sí/no (NDA, legal §2.9), página /repartidor y botón de ubicación de disenador.
- [ ] Repartidor: probar en un móvil real al sol (contraste, botones) e instalar la PWA (necesita HTTPS).
- [ ] Ubicación: probar el GPS por HTTPS y el pegado de enlaces maps.app.goo.gl reales.

- [ ] Panel web: crear/editar repartidores (con su PIN), configurar el menú y demás ajustes sin tocar .env ni YAML. (usuario, 2026-11-03)
- [ ] Arreglar test_recordatorio_y_cancelacion (test_motor): depende del día; falla si el próximo miércoles 10:00 está a menos de 24 h. (capitán, 2026-11-03)
- [x] 2026-11-03 — Panel responsive móvil (≤700px) hecho; falta probar en vivo tras integrar y revisar pantalla Ajustes a 375px (disenador)
- [ ] Antes del primer cliente: pasar a la nube (Render/Railway/Fly.io, auto-deploy desde GitHub, un servidor por restaurante, dominio vía Cloudflare). Túnel solo para demos. Revisar con legal-datos Ley 81 (ubicación de datos) y con comercial el precio que cubra 5-7 USD/mes de servidor. (usuario/capitán, 2026-11-03)
- [ ] Web: el chat conserva la conversación del pedido anterior al reabrir la página; empezar limpio tras cerrar pedido o visita nueva (usuario, 2026-11-03) (agentedeprogramacion)
- [ ] Panel adaptado a teléfono (usuario, 2026-11-03) (disenador)
- [ ] APARCADO (usuario, 2026-11-03) — Panel dueño ARCO + auditoría visible (legal-datos, legal/2026-11-02-web-pedidos.md §2.3-2.4, 2.8): buscar cliente por teléfono; EXPORTAR JSON descargable (no se guarda): cliente (nombre, teléfono, consentimiento_at, version_aviso), pedidos (fecha, ítems y notas, total, estado, entrega, dirección, método, pago_ref, monto), mensajes vivos, ubicación/comprobante solo como existe/fecha, solicitudes ARCO previas; NO auditoría, _por ni hashes. BORRAR: cliente, mensajes, capturas, ubicación, notas de ítems; ANONIMIZAR pedidos (nombre, teléfono, dirección → NULL; quedan cifras, estado, método, pago_ref, _por/_at); con pedido abierto no borrar ("cierra o cancela el pedido primero"); PRAGMA secure_delete=ON. Tabla arco_solicitud(id, tipo acceso|rectificacion|cancelacion|oposicion|portabilidad, canal, recibida_at, respondida_at, estado pendiente|hecha|rechazada, usuario, telefono_hmac = HMAC-SHA256 con clave secreta), 24 meses; auditoría arco_exportar|arco_borrar con solicitud_id. RETENCIÓN (constantes en un sitio, tarea diaria al arrancar): auditoría 12 meses, mensajes 90 días, cliente 12 meses desde último pedido (borrar + anonimizar), pedido anonimizado 5 años, ARCO 24 meses. Auditoría visible solo para el dueño, no editable.
- [ ] APARCADO (usuario, 2026-11-03) — Avisos tipo 'sistema' en el panel (IA sin saldo, WhatsApp caído): banda roja ya existe en panel_web.html; falta que estado_panel marque tipo "sistema" a los avisos sin pedido_id del motor.
- [ ] Web: al recargar, el teléfono sale prellenado como 50760000001; comprobar que al reentrar no se duplique el prefijo +507 (tester, 2026-11-03) (agentedeprogramacion)
- [ ] Ajustes: no se pueden borrar personas, solo desactivarlas (queda 'PruebaQA' inactivo de la prueba) (tester, 2026-11-03)

- [x] Páginas premium (usuario, 2026-11-03): quick wins + menú con fotos y mini-carrito (W5/W6) integrados en master con arreglos del revisor. Falta prueba en vivo con fotos reales.
- [ ] 2026-11-03 — Completar referentes de investigacion/2026-11-03-paginas-premium.md: Rappi, PedidosYa, Fresh KDS, Toast Online Ordering, Mobbin/Awwwards y "deslizar para confirmar"; verificar en caniuse las cifras marcadas * (agenteinvestigacion)
- [x] 2026-11-03 — Quick wins de propuesta-premium implementados (W1 W2 W7 P3 P6 R2 R3 P4 R5). Falta W5/W6 menú con fotos + mini-carrito (disenador + programación)
- [ ] 2026-11-03 — W13 Repetir último pedido y P11 borde por modalidad (propuesta premium, disenador)
- [x] 2026-11-03 — W5 Ajustes: subir/cambiar/quitar foto por plato hecho (disenador)
- [~] Rediseño estilo maquetas 'Fuego & Brasa' (usuario, 2026-11-03): tokens brasa M3, Epilogue/Plus Jakarta/JetBrains Mono autoalojadas, kanban por estados, catálogo+chat 8/4, repartidor con tarjeta activa. Se mantiene marca real y funciones reales; sin Tailwind CDN. Fases: web → panel → repartidor (disenador)
- [~] Mapa en vivo del repartidor (usuario, 2026-11-03): lo ven panel y cliente; OSM + Leaflet autoalojado; solo última posición mientras en_camino. legal-datos (criterios Ley 81) + agentedeprogramacion (API) + disenador (3 páginas)
- [ ] 2026-11-03 — [ABOGADO] ARCO: confirmar que pago_ref puede quedar en el pedido anonimizado, el plazo de 24 meses del registro ARCO y los 5 años del pedido anonimizado (legal §2.4)
- [ ] 2026-11-03 — Mapa en vivo en producción: mosaicos propios (PMTiles Panamá) o proxy con caché para que OSM no vea la IP del cliente (devops, legal §2.10). [ABOGADO] base de la ubicación del repartidor empleado (pendiente 9)
- [x] 2026-11-03 — Rediseño M3 fases 1-3 y mapa en vivo (OSM/Leaflet) hechos en las 3 páginas (disenador)
- [ ] 2026-11-03 — Maqueta sin datos en el sistema, omitido: promo con demora estimada, descripción de plato, insignias (popular, picante), valoraciones (disenador)
- [ ] 2026-11-03 — Panel: casillas por ítem en cocina y botón llamar al repartidor (maqueta); sin datos/persistencia en el sistema (disenador)
- [ ] 2026-11-04 — Pruebas inestables bajo carga: test_fotos.test_demasiado_grande (ConnectionAbortedError) y test_mapa 409/429 por tiempos; pasan solas 3/3 (capitán) (agentedeprogramacion)
- [ ] 2026-11-04 — Probar mapa en vivo en un móvil real: watchPosition del repartidor con un pedido en_camino y ver el mapa en panel y web del cliente (usuario)
- [x] 2026-11-04 — Web y túnel vigilados: despliegue/vigilar.cmd los reinicia y registra en despliegue/logs (capitan)
- [ ] 2026-11-04 — Pasar el motor (bot + web + panel) a un servidor en la nube (USD 5–6/mes) en vez del PC del restaurante (devops)
- [ ] 2026-11-04 — Copia continua de datos.sqlite y comprobantes/ a la nube (Litestream → Cloudflare R2) con prueba de restauración (devops)
- [ ] 2026-11-04 — Con muchos restaurantes: PostgreSQL gestionado (Supabase/Neon) + fotos en R2, datos separados por restaurante (arquitecto)
- [ ] 2026-11-04 — Ley 81: dónde se guardan los datos de clientes (país del servidor), tiempo de conservación y borrado (legal-datos)
