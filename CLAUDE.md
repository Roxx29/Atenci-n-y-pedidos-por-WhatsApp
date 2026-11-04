# Atención y pedidos por WhatsApp

Remoto: https://github.com/Roxx29/Atenci-n-y-pedidos-por-WhatsApp

## Reglas (siempre)
1. **Habla poco.** Solo lo necesario.
2. **Modelo según dificultad:** difícil → opus, intermedio → sonnet, fácil → haiku. Si la sesión no puede cambiar de modelo, delega a un agente de ese modelo.
3. **Skills:** en cada mensaje carga y usa todas las skills disponibles que apliquen.
4. **Ideas y pendientes → `IDEAS.md`.** Toda idea (del usuario o tuya) y toda tarea futura se apunta ahí al momento.
5. **Agentes:** trabaja siempre con los de `.claude/agents/`. Responde en español.
6. **Memory-Shard al día:** tras cada cambio relevante (decisión, tarea terminada, hallazgo, agente nuevo) actualiza la bóveda con el MCP `memoria` (`escribir_nota`, autor `capitan` o `usuario`, tag `whatsapp`). La nota central es `tareas/2026-11-02-whatsapp-restaurante`: añade ahí el avance (`modo:'agregar'`) y enlaza cada nota nueva desde ella o desde el `INDEX`. Cierra con `salud_grafo`.
7. **Cambios → `CAMBIOS.md`.** Todo lo que se agrega, cambia o actualiza en el proyecto se apunta arriba del todo (lo más reciente primero), al momento, con fecha y autor.
8. **"encender jarvis":** aplica todas estas reglas, carga las skills que apliquen, lee `IDEAS.md` y `CAMBIOS.md` y deja al usuario las ideas pendientes resumidas. Él elige con qué seguir y tú lo planteas en fases.

## Agentes y espacios de Orca
Cada espacio de Orca es una rama. Tu rol depende de la rama en la que estás (`git branch --show-current`):

| Rama / espacio | Agente principal | Agentes que usa |
|---|---|---|
| `master` | `capitan` | reparte a todos |
| `Roxx29/AgenteServicios` | `agenteservicios` | conocimiento de Investigacion-de-servicios |
| `Roxx29/AgenteDeProgramacion` | `agentedeprogramacion` | `arquitecto` (opus), `programador` (sonnet), `programador-rapido` (haiku), `tester`, `revisor` |
| `Roxx29/AgenteInvestigacion` | `agenteinvestigacion` | `investigador-profundo` (opus), `investigador-web` (sonnet), `investigador-rapido` (haiku) |
| `Roxx29/CreadorAgentes` | `creadoragentes` | crea agentes nuevos |
| `Roxx29/AgenteDespliegue` | `devops` | apoya a `agentedeprogramacion`: despliegue y operación |
| `Roxx29/AgenteLegal` | `legal-datos` | apoya a `comercial` y `agentedeprogramacion`: Ley 81, privacidad, términos |
| `Roxx29/AgenteComercial` | `comercial` | usa `agenteservicios`, `agenteinvestigacion`, `legal-datos`, `disenador` |
| `Roxx29/AgenteDiseno` | `disenador` | apoya a `comercial` y `agentedeprogramacion`: diseño y marca |

- El capitán encarga trabajo a otros espacios con `ListAgents` + `SendMessage`, e integra sus ramas en `master`.
- Los subagentes no pueden lanzar otros subagentes: el reparto lo hace la sesión principal de cada espacio.
- Agente nuevo: créalo en `.claude/agents/`, añádelo a esta tabla e intégralo en `master`.
- Memoria compartida entre espacios: MCP `memoria` (Memory-Shard).
