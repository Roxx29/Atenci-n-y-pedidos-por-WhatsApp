# Demo restaurante (Chitré) — investigación 2026-11-02

Agente: agenteinvestigacion (investigador-profundo, investigador-web, investigador-rapido). *Estimación* = sin fuente.

## 1. IA gratuita con tool calling para la demo

**Recomendación: Ollama con su API compatible con Anthropic** (`/v1/messages`, soporta tools con el SDK oficial de Anthropic). El motor no se reescribe: solo cambian base URL y modelo.

| Opción | Límites gratis | Tarjeta | Tools / español | Cambio de código |
|---|---|---|---|---|
| **Ollama local** (`qwen3:8b`, `llama3.1:8b`; `qwen3:4b` en PC floja) | Sin límite | No | Buenas (*estimación*) | Ninguno |
| Ollama Cloud Free | Uso ligero, cupos por 5 h/semana | No | Modelos grandes | Ninguno |
| Gemini Flash free | ~5–15 RPM, cifras varían | No | Muy buenas | LiteLLM o reescribir. Usa los datos para entrenar |
| Groq free | ~30 RPM, ~1000 RPD | No | Buenas, muy rápido | LiteLLM (API OpenAI) |
| OpenRouter `:free` | 20 RPM, 50/día | No | Varía, modelos rotan | Endpoint compatible Anthropic (no verificado) |
| Cerebras | Sin free desde jul-2026 | Sí | — | — |

- PC (*estimación*): 8B Q4 ≈ 6 GB RAM/VRAM. Solo CPU: 5–15 s por respuesta. Con GPU de 8 GB: 1–3 s. Desactivar el "thinking" de qwen3.
- Limitaciones de Ollama: no soporta `tool_choice`, conteo de tokens ni caché de prompts.
- Plan B: Gemini Flash detrás de LiteLLM.

**Cambio mínimo:**
1. `model=os.environ.get("LLM_MODEL", "claude-haiku-5-5")`
2. El SDK ya lee `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY`.
3. Demo: `ANTHROPIC_BASE_URL=http://localhost:11434`, `ANTHROPIC_API_KEY=ollama`, `LLM_MODEL=qwen3:8b`.
4. Producción: quitar `ANTHROPIC_BASE_URL`, clave real, `LLM_MODEL=claude-haiku-5-5`.
5. Quitar o condicionar `tool_choice`. Probar 10–20 pedidos en español antes de la demo.

## 2. Entregas Chitré / Los Santos

- **PedidosYa** (antes Appetito24): en 2021 llegaba a Las Tablas. **Chitré no confirmado para 2026**; probar una dirección en la app.
- **API de PedidosYa:** no hay API pública verificada para Panamá; "PedidosYa Envíos" se tramita vía ventas.
- **Comisión de PedidosYa:** sin cifra pública en Panamá (*estimación* 20–30%).
- **Riesgo:** paro de repartidores en abril de 2026; comercios volviendo a su propio delivery.
- **Otras apps locales en Azuero:** no se encontraron fuentes. Validar en campo con restaurantes y motorizados.
- **Recomendación v1:** el bot recibe el pedido y avisa por WhatsApp al repartidor propio o a un motorizado local, más la opción de recoger en local. Guardar dirección, tarifa y estado para una integración futura.

## 3. Horarios y costo de envío

- **Horarios:** sin datos verificados (Maps/TripAdvisor bloqueados). *Estimación* habitual en Panamá: almuerzo ~11:00–15:00, cena ~18:00–22:00, cierre lunes o domingo noche. **Confirmar con el restaurante de la demo.**
- **Envío dentro de Chitré:** *estimación* US$1.50–3.
- **Envío Chitré ↔ La Villa de Los Santos** (~4 km, cruzando el río La Villa): *estimación* US$2–3. Sin tarifa publicada. Para comparar, Chitré → Las Tablas ≈ US$3–5 (*estimación*).

## Fuentes
- https://docs.ollama.com/api/anthropic-compatibility
- https://aistudio.google.com/docs/rate-limits · https://aistudio.google.com/docs/available-regions · https://ai.google.dev/gemini-api/terms_preview
- https://console.groq.com/docs/rate-limits
- https://openrouter.ai/blog/tutorials/free-llm-apis-compared/
- https://www.usagepricing.com/blueprint/activity/cerebras-2026-07-21-free-tier-credit-trial
- https://devtoolhub.com/ollama-cloud-free-vs-pro-limits-pricing-2026/
- https://docs.litellm.ai/docs/anthropic_unified/index.md
- https://www.revistaeyn.com/empresasymanagement/appetito24-cambia-su-nombre-a-pedidosya-KVEN1422282
- https://mercado.com.ar/protagonistas/pedidosya-consolida-su-expansion-en-el-continente-americano
- https://www.laestrella.com.pa/panama/nacional/crisis-en-delivery-paro-de-pedidosya-impacta-al-comercio-en-panama-CH21731157
- https://www.panacompu.com/en/delivery-locations
