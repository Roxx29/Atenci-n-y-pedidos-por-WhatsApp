# Modelo Ollama más rápido (respaldo gratuito) — 2026-11-02

**Estado: sin medir.** La demo pasó a deepseek-v4-flash (1–2 s). Las descargas se cancelaron (memoria del sistema casi agotada) y no se pidió reintentar. En el servidor de demo (127.0.0.1:11435) sigue solo `qwen3:8b`. Tamaños y calidad abajo son *estimación* (sin medir ni verificar en la web).

| Candidato | Tamaño aprox. | Tool calling | Español | Nota |
|---|---|---|---|---|
| **qwen3:4b** | ~2.5 GB | Bueno | Bueno | Misma familia que el 8b: cambio mínimo. **Primera opción.** |
| qwen2.5:7b | ~4.7 GB | Bueno | Bueno | No es más rápido que qwen3:8b: solo vale si thinking da problemas |
| llama3.2:3b | ~2.0 GB | Aceptable | Medio | Más rápido, elige peor la herramienta |
| granite4:3b | ~2.1 GB | Bueno (pensado para tools) | Medio | Alternativa si falla qwen3:4b |

**Recomendación:** si hace falta respaldo gratuito, probar `qwen3:4b` (thinking desactivado, contexto 16k). Debería dar aproximadamente la mitad de tiempo que el 8b (*estimación*). No usarlo sin pasar la prueba: `quiero 2 tasajos para recoger` → `crear_pedido` (cantidad 2, recoger).

**Para medir cuando haya memoria:** `OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen3:4b` (solo en el servidor de demo, nunca en C: ni puerto 11434) y ejecutar un script que mida el tiempo de una llamada con herramienta y compruebe que elige `crear_pedido` con cantidad 2 y entrega "recoger". Pídemelo y lo hago.
