# Auditoria operativa MiroFish ESP

Fecha: 2026-05-05

Objetivo: estabilizar MiroFish ESP como motor de simulaciones predictivas para uso comercial, reduciendo fallos, dobles procesos, gasto inutil de tokens y estados falsos en la interfaz.

## Estado ejecutivo

El sistema ya esta funcionando de nuevo en la VPS y el backend responde. La ultima simulacion `sim_fe74d0de6702` no esta congelada: quedo en estado `failed`, sin proceso activo. El boton de detener ahora responde correctamente con exito cuando una simulacion ya esta fallida/completada/detenida.

La causa tecnica principal observada en esa corrida fue un error 400 del proveedor LLM compatible con OpenAI/Gemini:

`function_response.name: Name cannot be empty`

Ese error venia de la integracion OASIS/CAMEL cuando genera respuestas de herramientas sin nombre. Tambien habia senales de contexto excesivo: warnings de mensajes de aproximadamente 20k tokens y ventanas de historial grandes. La combinacion provoca timeouts, errores de proveedor, consumo alto y estados ambiguos.

## Flujo completo del sistema

1. Entrada de datos
   - Frontend: `Home.vue`, `Process.vue`.
   - Backend: `/api/graph/ontology/generate`.
   - Riesgo: archivos grandes, seeds ambiguos o prompt mal traducido pueden producir ontologias invalidas.

2. Construccion de grafo
   - Backend: `/api/graph/build`.
   - Servicio: `GraphBuilderService`.
   - Dependencia externa: Zep.
   - Riesgo: reconstrucciones forzadas y endpoints destructivos sin autenticacion.

3. Creacion de simulacion
   - Backend: `/api/simulation/create`.
   - Persistencia: `backend/uploads/simulations/<simulation_id>/state.json`.
   - Riesgo: si no se limpia bien runtime anterior, una nueva corrida hereda errores viejos.

4. Preparacion
   - Backend: `/api/simulation/prepare`.
   - Servicios: perfiles OASIS, configuracion temporal, posts iniciales.
   - Riesgo: prompts mezclados, defaults culturales incorrectos, JSON truncado o no parseable.

5. Ejecucion
   - Backend: `/api/simulation/start`.
   - Runner: `SimulationRunner`.
   - Subprocesos: `run_parallel_simulation.py`, `run_twitter_simulation.py`, `run_reddit_simulation.py`.
   - Persistencia runtime: `run_state.json`, `twitter/actions.jsonl`, `reddit/actions.jsonl`, `simulation.log`.
   - Riesgo: procesos muertos que quedan marcados como running, saturacion de LLM, fallos por plataforma que no se comunican al backend.

6. Monitoreo
   - Frontend: `Step3Simulation.vue`, `SimulationRunView.vue`.
   - Backend: `/run-status`, `/run-status/detail`, `/actions`, `/timeline`.
   - Riesgo: refresco agresivo de grafo y estados falsos por logs incompletos.

7. Reporte
   - Backend: `/api/report/generate`, `/api/report/chat`.
   - Servicios: `ReportAgent`, `ZepToolsService`.
   - Riesgo: gasto de herramientas, consultas largas, reporte basado en simulacion parcial sin advertir confianza.

8. Entrevistas / encuestas a agentes
   - Backend: `/api/simulation/interview/*`.
   - IPC: `SimulationIPCClient`.
   - Riesgo: si el entorno ya no esta vivo, las entrevistas deben fallar claro y no reintentar infinitamente.

9. Historial y borrado
   - Frontend: historial.
   - Backend: `DELETE /api/simulation/<id>`, `DELETE /api/graph/project/<id>`, `DELETE /api/report/<id>`.
   - Riesgo: operaciones destructivas sin autenticacion ni permisos por usuario.

## Fallos encontrados y acciones tomadas

### 1. Simulaciones que se quedan a mitad

Antes: una plataforma podia fallar dentro de `env.step()` y el proceso completo podia quedar en un estado ambiguo. La UI veia progreso parcial, pero el backend no siempre sabia si el proceso seguia vivo o habia muerto.

Accion:
- Se agrego reconciliacion de proceso en `SimulationRunner.get_run_state`.
- Si el estado dice `running/starting/paused/stopping` pero el PID real no existe, se marca como `failed`.
- Si el estado esta terminal pero el proceso todavia existe, `stop` intenta terminarlo igual.

Resultado esperado:
- Menos estados falsos.
- El boton Detener deja de responder 400 cuando la corrida ya fallo.
- La UI puede distinguir mejor entre `failed`, `completed` y `stopped`.

### 2. Error de Gemini/OASIS con herramientas sin nombre

Antes: CAMEL podia enviar `function_response.name` vacio al endpoint compatible con OpenAI de Gemini.

Accion ya aplicada previamente:
- Sanitizacion runtime en `oasis_runtime_limits.py`.
- Se agrega `name` cuando falta en mensajes de herramienta.

Resultado esperado:
- Se elimina la causa directa del error `Name cannot be empty` para corridas nuevas.

### 3. Crecimiento de contexto y gasto de tokens

Antes: las memorias de agentes podian crecer mucho durante rondas largas. Eso eleva tokens, latencia, costo y probabilidad de timeout.

Accion ya aplicada previamente:
- Ventana de mensajes y poda de memoria por variables `MIROFISH_AGENT_*`.

Accion aplicada ahora:
- La concurrencia OASIS ya no queda fija en 30 por plataforma.
- Nueva variable global: `MIROFISH_OASIS_SEMAPHORE=8`.
- Variables opcionales por plataforma: `MIROFISH_TWITTER_SEMAPHORE`, `MIROFISH_REDDIT_SEMAPHORE`.

Resultado esperado:
- Menos llamadas simultaneas.
- Menos rate limits/timeouts.
- Menos gasto explosivo cuando hay muchos agentes.

### 4. Fallos repetidos por ronda

Antes: un error en `env.step()` podia tumbar la plataforma sin registrar un evento claro para el backend.

Accion:
- Cada ronda tiene reintentos controlados con `MIROFISH_ROUND_STEP_ATTEMPTS=2`.
- Si una plataforma acumula `MIROFISH_MAX_CONSECUTIVE_ROUND_FAILURES=3`, se marca como fallida.
- Se escriben eventos `round_error` y `simulation_error` en `actions.jsonl`.

Resultado esperado:
- Los errores quedan visibles y auditables.
- No hay bucles silenciosos.
- El backend puede marcar `failed` sin depender solo del codigo de salida del proceso.

### 5. Paralelo que sigue gastando cuando una rama falla

Antes: si Twitter fallaba, Reddit podia seguir corriendo hasta completar sus rondas, aunque el resultado comercial ya estuviera comprometido.

Accion:
- El runner paralelo ahora usa comportamiento fail-fast.
- Si una plataforma falla, se cancela la plataforma pendiente.
- Se evita entrar en modo de espera de comandos si hubo fallo.

Resultado esperado:
- Menos gasto de tokens.
- Menos procesos vivos sin sentido.
- Menos riesgo de entrevistas contra un entorno inconsistente.

### 6. Stop no idempotente

Antes: al detener una simulacion ya fallida, el endpoint podia responder 400.

Accion:
- `stop_simulation` ahora acepta estados terminales y devuelve exito.
- Si quedan flags `twitter_running` o `reddit_running`, los limpia.

Resultado verificado:
- `POST /api/simulation/stop` para `sim_fe74d0de6702` devuelve 200 con mensaje: `La simulacion ya no estaba activa`.

## Validacion ejecutada

Local:
- Compilacion Python de archivos modificados.
- Revision de diff.

VPS:
- Archivos aplicados al host y al contenedor activo.
- Reinicio de contenedor `mirofish`.
- Compilacion Python dentro del contenedor.
- `GET /api/simulation/history` responde 200.
- Frontend en puerto 3000 responde 200.
- `POST /api/simulation/stop` sobre simulacion fallida responde 200.
- No hay simulaciones activas.
- Prueba de concurrencia: defaults `8/8`, override global/plataforma `5/3`.
- Disco recuperado de 97% a 85% limpiando solo cache de build Docker.

## Reglas operativas antes de vender una corrida

1. No ejecutar 55+ rondas como primera prueba de un proyecto nuevo.
2. Hacer primero una corrida smoke de 3 a 5 rondas.
3. Si pasa, ejecutar 10 a 15 rondas con el mismo seed.
4. Solo despues lanzar 30+ rondas.
5. Mantener `MIROFISH_OASIS_SEMAPHORE` entre 4 y 8 para VPS actual.
6. No activar actualizacion de memoria de grafo si el objetivo es solo validar motor.
7. No usar `force=true` desde UI automaticamente.
8. Nunca reconstruir Docker completo con menos de 20GB libres.
9. Revisar `simulation.log` y `actions.jsonl` antes de prometer conclusiones de negocio.
10. Si una simulacion queda parcial, el reporte debe decir que la confianza es parcial.

## Riesgos de seguridad pendientes

Estos puntos aun deben corregirse antes de exponerlo como producto publico:

1. No hay autenticacion real en endpoints API.
   - Impacto: cualquier persona con acceso a la URL puede crear, borrar, iniciar o detener simulaciones.
   - Prioridad: P0 antes de venta externa.

2. Endpoints destructivos expuestos.
   - `DELETE /api/simulation/<id>`
   - `DELETE /api/graph/project/<id>`
   - `DELETE /api/report/<id>`
   - Impacto: borrado de datos por error o ataque.

3. Flask dev server en contenedor.
   - Log actual: `This is a development server`.
   - Impacto: no es servidor WSGI de produccion.
   - Recomendacion: Gunicorn + reverse proxy + healthcheck real.

4. Rate limiting ausente.
   - Impacto: abuso de endpoints caros como simulacion, reporte y chat.
   - Recomendacion: limite por IP/usuario y cola de jobs.

5. Subida de archivos sin antivirus/sandbox.
   - Aunque hay extensiones permitidas y limite de 50MB, falta escaneo y aislamiento.

6. Sin separacion multiusuario.
   - Las simulaciones actuales parecen globales.
   - Para vender, cada cliente necesita ownership, permisos y trazabilidad.

7. Secretos en entorno unico.
   - LLM y Zep usan claves globales.
   - Recomendacion: rotacion, limites de gasto y monitoreo por proyecto.

## Pendientes tecnicos de mayor impacto

1. Implementar autenticacion y roles.
2. Agregar cola de ejecucion: una simulacion pesada por vez o cupos por plan.
3. Agregar checkpoint/resume real por ronda.
4. Cambiar Flask dev server por Gunicorn.
5. Crear suite de pruebas de estado: start, stop, failed, completed, delete.
6. Mostrar en UI una etiqueta de confianza cuando una simulacion termina parcial.
7. Registrar costo aproximado por corrida: agentes, rondas, acciones, tokens estimados.
8. Crear job de limpieza de Docker/logs con retencion segura.

## Recomendacion de operacion inmediata

Para la proxima prueba real:

1. Usar una simulacion nueva, no relanzar encima de `sim_fe74d0de6702`.
2. Configurar 5 rondas.
3. Mantener paralelo si el caso requiere comparacion Twitter/Reddit; si no, probar una sola plataforma.
4. Confirmar que `actions.jsonl` recibe `simulation_end`.
5. Luego subir a 15 rondas.
6. Solo si 15 rondas completan, ejecutar 30+.

Para venta:

No vender todavia como SaaS abierto. Vender como servicio gestionado: "diagnostico predictivo asistido por IA", con operador interno revisando logs, confianza y supuestos antes de entregar conclusiones al cliente.
