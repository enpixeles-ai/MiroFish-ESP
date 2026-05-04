# Endurecimiento MiroFish ESP

Fecha: 2026-05-04

Este documento resume las correcciones aplicadas para estabilizar MiroFish ESP como motor interno de simulaciones y reportes predictivos.

## Problemas corregidos

- La pantalla de simulacion ya no reinicia corridas automaticamente al abrir o recargar la vista.
- Una corrida nueva limpia el estado runtime anterior, incluido `error`, contadores y marcas de ejecucion.
- El historial permite eliminar simulaciones viejas o fallidas, con proteccion para no borrar una corrida activa por accidente.
- La ontologia ya no traduce contratos tecnicos que deben conservarse en ingles (`PascalCase`, `SCREAMING_SNAKE_CASE`, `snake_case`).
- Los prompts y defaults temporales dejaron de mezclar China/Pekin con comportamiento LatAm/Hispanic USA.
- El grafo ya no se refresca agresivamente antes de confirmar que la simulacion esta activa.
- Los agentes OASIS/CAMEL ahora tienen ventana y poda de memoria para evitar bucles por contexto de cientos de miles de tokens en rondas finales.
- La encuesta consulta agentes de la plataforma correcta y cuenta solo respuestas reales recibidas.
- La API valida parametros de entrevistas, limita tamanos de batch y controla timeouts.
- Flask queda en modo produccion por defecto, con `SECRET_KEY` obligatorio fuera de debug.
- CORS es configurable mediante `CORS_ORIGINS`; no queda abierto globalmente por defecto.
- El frontend usa el host actual para construir la URL de API, evitando dependencias de `localhost` en VPS.

## Validacion ejecutada

Desde la raiz del repo:

```bash
python3 -m compileall -q backend/app backend/scripts
cd frontend && npm run build
cd frontend && npm audit --omit=dev
npm audit --omit=dev
git diff --check
```

Resultado esperado:

- Compilacion Python sin errores.
- Build frontend exitoso.
- Auditorias npm con `0 vulnerabilities`.
- Sin errores de espacios o formato en el diff.

## Variables requeridas en produccion

Definir en `.env` antes de reconstruir/desplegar:

```bash
FLASK_DEBUG=false
SECRET_KEY=<valor-largo-aleatorio>
CORS_ORIGINS=http://76.13.159.252:3000,https://<dominio-produccion>
MIROFISH_AGENT_MESSAGE_WINDOW_SIZE=8
MIROFISH_AGENT_MEMORY_RECORDS=24
MIROFISH_AGENT_TOKEN_LIMIT=64000
MIROFISH_AGENT_STEP_TIMEOUT_SECONDS=180
MIROFISH_OASIS_SEMAPHORE=8
MIROFISH_ROUND_STEP_ATTEMPTS=2
MIROFISH_MAX_CONSECUTIVE_ROUND_FAILURES=3
```

No usar `SECRET_KEY=dev-secret-key` ni `FLASK_DEBUG=true` en VPS publica.

## Despliegue seguro en VPS

Antes de reiniciar o reconstruir el contenedor:

1. Confirmar que no haya simulaciones activas.
2. Hacer respaldo de `/home/pixel/MiroFish/backend/uploads`.
3. Verificar espacio disponible en disco y limpiar imagenes Docker antiguas si hace falta.
4. Aplicar cambios del repo.
5. Reconstruir y levantar el contenedor.
6. Crear una simulacion corta de prueba y confirmar que historial, encuesta, grafo e informe funcionan.

Si hay una simulacion activa, no reiniciar el contenedor: se puede subir el codigo al repo, pero el despliegue debe esperar.
