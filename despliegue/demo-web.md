# Demo web (un clic)

`despliegue\demo-web.cmd [puerto]` (por defecto 8080) abre 3–4 ventanas:

| Ventana | Qué es |
|---|---|
| Ollama demo | `demo-ollama.cmd` (puerto 11435, modelos en `F:\Modelo de IA`); solo si no responde ya |
| Web pedidos | `python -m motor web servicios/restaurante-pedidos <puerto>` |
| Panel | `python -m motor panel servicios/restaurante-pedidos` |
| Tunel publico | solo si existe `F:\herramientas\cloudflared\cloudflared.exe` |

Modelo: si `.env` (raíz del repo, ignorado por git) tiene `DEEPSEEK_API_KEY=`, el script usa DeepSeek (`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `LLM_MODEL=deepseek-v4-flash`) y deja Ollama como respaldo (`LLM_RESPALDO_BASE_URL=http://127.0.0.1:11435`, `LLM_RESPALDO_MODEL=qwen3:8b`).
La clave la lee el motor del `.env`; el script no la toca ni la muestra.
Si DeepSeek falla por saldo (402), clave (401/403), límite (429), caída (5xx) o red, el motor pasa a Ollama, avisa una vez en el panel y reintenta DeepSeek a los 5 min.
Sin clave en `.env`: solo Ollama (`ANTHROPIC_BASE_URL=http://127.0.0.1:11435`, `LLM_MODEL=qwen3:8b`, `ANTHROPIC_API_KEY=ollama` si no había otra).
Cerrar: cerrar las ventanas.

## Red local
`http://<IP-del-PC>:<puerto>` (IP con `ipconfig`). La primera vez Windows pide permiso de firewall para Python: aceptar solo **Red privada**.

## Desde fuera: Cloudflare quick tunnel (gratis, sin cuenta)
- Un único `.exe` (~50 MB), sin instalador ni servicio.
- Descarga: https://github.com/cloudflare/cloudflared/releases/latest → `cloudflared-windows-amd64.exe`
- Guardar como `F:\herramientas\cloudflared\cloudflared.exe`. Nada en C:.
- El script lo detecta solo; la URL `https://<algo>.trycloudflare.com` aparece en la ventana "Tunel publico". Cambia en cada arranque.

Cuidado:
- La URL es pública y sin contraseña: cualquiera con el enlace hace pedidos. Usar solo con datos de prueba y cerrar el túnel al terminar la demo.
- Quick tunnel no tiene SLA (solo demos). Producción: túnel con nombre + cuenta Cloudflare (pendiente en IDEAS.md).

Alternativa sin instalar nada: ninguna gratis y fiable; ngrok exige cuenta y token.
