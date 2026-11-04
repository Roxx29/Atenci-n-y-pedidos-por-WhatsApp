# Publicar con dominio propio (Cloudflare Tunnel)

Pasos del usuario (una vez). `cloudflared` = un `.exe`, sin instalador: guardarlo como `F:\herramientas\cloudflared\cloudflared.exe` (https://github.com/cloudflare/cloudflared/releases/latest).

1. Dominio en Cloudflare con DNS activo (nameservers apuntando a Cloudflare).
2. Dashboard → **Zero Trust** → **Networks → Tunnels** → *Create a tunnel* → *Cloudflared* → nombre `restaurante`.
3. Copiar el **token** (cadena larga tras `--token`). No lo pegues en chats ni en git.
4. Pestaña **Published application routes** (Public hostname) → añadir: `demo.tudominio.com` → servicio **HTTP** → `localhost:8080`.
5. En `.env` (raíz del repo, ignorado por git) añadir:
   ```
   TUNNEL_TOKEN=<token>
   PANEL_PIN=292931
   DEEPSEEK_API_KEY=<clave>
   ```
6. Doble clic en `despliegue\publicar.cmd [puerto]`. Abre la web y el túnel; `https://demo.tudominio.com` ya responde, y `/panel` pide PIN.

## Cómo queda protegido
- `publicar.cmd` activa `TUNEL=1`: el motor cree `CF-Connecting-IP` **solo** si la conexión viene de este mismo PC (cloudflared). Así el bloqueo (5 fallos de PIN → 5 min) y los límites de peticiones son por cliente real, no por `127.0.0.1`.
- `PANEL_PUBLICO=1` abre `/panel` y `/repartidor` a internet; sin esa variable `/panel` es solo LAN (404 desde fuera). Sin PIN válido sigue deshabilitado.
- Las cookies llevan `Secure` cuando la petición llegó por HTTPS (`X-Forwarded-Proto`/`CF-Visitor`); la dirección `http://localhost` sigue funcionando sin `Secure`.
- Cloudflare termina el HTTPS; el tramo túnel→PC va cifrado. El origen no se abre al exterior (no hay puertos que redirigir en el router).

## Recomendado: Cloudflare Access sobre `/panel*` (gratis hasta 50 usuarios)
Zero Trust → Access → Applications → *Self-hosted* → `demo.tudominio.com`, path `/panel*` (y `/repartidor*` si se quiere) → política *Allow* solo para los correos del personal (código por email). Así el PIN es la segunda capa y los bots ni llegan a verlo.

## Seguridad / pendientes
- 6 dígitos + bloqueo es suficiente para demo; en producción usar `PANEL_PIN_HASH` y Access.
- Quien tenga el token puede suplantar el túnel: si se filtra, *Refresh token* en el dashboard.
- Para parar la demo: cerrar la ventana "Tunel Cloudflare" (o desactivar la ruta en el dashboard).
