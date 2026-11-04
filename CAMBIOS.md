# Cambios

Lo último que se agregó, cambió o actualizó en el proyecto. Lo más reciente arriba. Formato: `- AAAA-MM-DD — qué cambió (autor)`.

- 2026-11-04 — Integradas ramas nube y despliegue; PROXY=1 usa el último X-Forwarded-For (Fly-Client-IP solo en Fly), importación de comprobantes verifica bytes antes de borrar, PROXY=1 en render/fly, quitado .whl del repo (agentedeprogramacion)
- 2026-11-04 — Modo opusplan: `.claude/settings.json` con model "opusplan" (Opus planifica, Sonnet ejecuta) y regla 2 de CLAUDE.md: sonnet por defecto, opus moderador, haiku lo sencillo (usuario)
- 2026-11-04 — DECISIÓN de Ajustes (7 fases) acordada por disenador + agentedeprogramacion + comercial → diseno/propuestas/2026-11-04-ajustes-DECISION.md; falta aprobación del usuario (disenador)
- 2026-11-04 — Propuesta de Ajustes (datos y API): tabla `ajustes` sobre servicio.yaml, logo BLOB, `/config`, fases F1-F4 → diseno/propuestas/2026-11-04-ajustes-agentedeprogramacion.md (agentedeprogramacion)
- 2026-11-04 — IDEAS: quitadas 9 tareas repetidas (panel, web, token, campo) y añadida "Ajustes profesionales" (logo, nombre, menú, repartidores) (capitan)
- 2026-11-04 — Código listo para la nube, SIN conectar: Turso opcional (TURSO_DATABASE_URL/TURSO_AUTH_TOKEN; sin ellas SQLite local), comprobantes dentro de la BD (los archivos viejos se importan y borran), `python -m motor migrar`, `GET /salud`, `PROXY=1` (IP real detrás de Render/Fly, último X-Forwarded-For), Dockerfile, render.yaml, fly.toml, guía `despliegue/nube.md`. 256 pruebas OK (programador, devops, agentedeprogramacion)
- 2026-11-04 — IDEAS: quitada la tarea de arranque automático al encender el PC (usuario)
- 2026-11-04 — Reglas 7 y 8 en CLAUDE.md: este archivo y la orden "encender jarvis" (capitan)
- 2026-11-04 — `Encender.cmd` / `Apagar.cmd` en la raíz y accesos directos en el Escritorio: todo el sistema con doble clic (capitan)
- 2026-11-04 — `despliegue/vigilar.cmd`: web y túnel se reinician solos si se caen; registro en `despliegue/logs/`. `publicar.cmd` lo usa (capitan)
- 2026-11-04 — `.gitignore`: `*.sqlite-*` y `despliegue/logs/`; `.gitattributes`: `.cmd` con CRLF (capitan)
- 2026-11-04 — API DeepSeek revisada: clave válida, saldo USD 1.68, `deepseek-v4-flash` responde (capitan)
- 2026-11-04 — IDEAS: nube, copias de seguridad, PostgreSQL futuro y Ley 81 (ubicación de datos) (capitan)
- 2026-11-03 — Rediseño fase 3 y mapa en vivo: repartidor con turno, mapa de flota en el panel y mapa del repartidor en la web del cliente (disenador)
