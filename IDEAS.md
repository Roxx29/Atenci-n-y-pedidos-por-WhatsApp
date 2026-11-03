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
- [devops] cloudflared: para enseñar la demo fuera de la red local y para el webhook de WhatsApp (pendiente).
- [ ] 2026-11-02 — Web: en modo demo mostrar "el bot está pensando, puede tardar ~30 s" mientras responde el modelo local (capitan) (disenador)
- [ ] Web: probar en un móvil real la subida del comprobante (galería, cámara y fotos HEIC de iPhone).
