"""
Rutas API relacionadas con la simulación
Paso 2: Lectura y filtrado de entidades Zep, preparación y ejecución de la simulación OASIS (automatización completa)
"""

import os
import traceback
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Prefijo de optimización del prompt de entrevista
# Agregar este prefijo evita que el Agent llame a herramientas, responde directamente con texto
INTERVIEW_PROMPT_PREFIX = "Considerando tu personaje, todos tus recuerdos y acciones pasadas, respóndeme directamente con texto sin llamar a ninguna herramienta:"
MAX_INTERVIEW_BATCH_SIZE = int(os.environ.get("MAX_INTERVIEW_BATCH_SIZE", "60"))
MAX_INTERVIEW_PROMPT_CHARS = int(os.environ.get("MAX_INTERVIEW_PROMPT_CHARS", "4000"))
MAX_INTERVIEW_TIMEOUT_SECONDS = int(os.environ.get("MAX_INTERVIEW_TIMEOUT_SECONDS", "600"))


def optimize_interview_prompt(prompt: str) -> str:
    """
    Optimiza la pregunta de entrevista, agrega prefijo para evitar que el Agent llame a herramientas
    
    Args:
        prompt: Pregunta original
        
    Returns:
        Pregunta optimizada
    """
    if not prompt:
        return prompt
    # Evitar agregar el prefijo repetidamente
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


def reset_simulation_runtime_state(state):
    """Limpiar campos de ejecución antes de una corrida nueva."""
    state.error = None
    state.current_round = 0
    state.twitter_status = "not_started"
    state.reddit_status = "not_started"


def mark_simulation_running(state):
    """Actualizar estado persistente al iniciar una corrida."""
    state.status = SimulationStatus.RUNNING
    state.error = None
    state.current_round = 0
    state.twitter_status = "running" if state.enable_twitter else "disabled"
    state.reddit_status = "running" if state.enable_reddit else "disabled"


# ============== Interfaces de lectura de entidades ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    Obtiene todas las entidades del grafo (filtradas)
    
    Solo retorna nodos que cumplen los tipos de entidad predefinidos (nodos cuyas Labels no son solo Entity)
    
    Parámetros de consulta:
        entity_types: Lista de tipos de entidad separados por coma (opcional, para filtrar aún más)
        enrich: Si se obtiene información de aristas relacionadas (por defecto true)
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY no está configurada"
            }), 500
        
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        logger.info(f"Obteniendo entidades del grafo: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")
        
        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener entidades del grafo: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """Obtiene información detallada de una sola entidad"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY no está configurada"
            }), 500
        
        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": f"La entidad no existe: {entity_uuid}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener detalles de la entidad: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """Obtiene todas las entidades del tipo especificado"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY no está configurada"
            }), 500
        
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener entidades: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de gestión de simulaciones ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    Crea una nueva simulación
    
    Nota: parámetros como max_rounds son generados de forma inteligente por LLM, no es necesario configurarlos manualmente
    
    Solicitud (JSON):
        {
            "project_id": "proj_xxxx",      // Requerido
            "graph_id": "mirofish_xxxx",    // Opcional, si no se proporciona se obtiene del proyecto
            "enable_twitter": true,          // Opcional, por defecto true
            "enable_reddit": true            // Opcional, por defecto true
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "mirofish_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione project_id"
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"El proyecto no existe: {project_id}"
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "El proyecto aún no tiene grafo construido, llame primero a /api/graph/build"
            }), 400
        
        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Fallo al crear la simulación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    Verifica si la simulación ya está preparada
    
    Condiciones de verificación:
    1. state.json existe y el status es "ready"
    2. Los archivos necesarios existen: reddit_profiles.json, twitter_profiles.csv, simulation_config.json
    
    Nota: los scripts de ejecución (run_*.py) se conservan en el directorio backend/scripts/, ya no se copian al directorio de la simulación
    
    Args:
        simulation_id: ID de la simulación
        
    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # Verificar si el directorio existe
    if not os.path.exists(simulation_dir):
        return False, {"reason": "El directorio de la simulación no existe"}
    
    # Lista de archivos necesarios (sin incluir scripts, los scripts están en backend/scripts/)
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # Verificar si los archivos existen
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "Faltan archivos necesarios",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # Verificar el estado en state.json
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # Log detallado
        logger.debug(f"Detectando estado de preparación de la simulación: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # Si config_generated=True y los archivos existen, se considera preparada
        # Los siguientes estados indican que la preparación está completa:
        # - ready: Preparación completa, se puede ejecutar
        # - preparing: si config_generated=True indica que está completo
        # - running: En ejecución, lo que indica que la preparación ya estaba completa
        # - completed: Ejecución completada, la preparación ya estaba completa
        # - stopped: Detenido, la preparación ya estaba completa
        # - failed: Ejecución fallida (pero la preparación estaba completa)
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # Obtener estadísticas de archivos
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # Si el estado es preparing pero los archivos están completos, actualizar automáticamente el estado a ready
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"Actualizando automáticamente el estado de la simulación: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"Fallo al actualizar estado automáticamente: {e}")
            
            logger.info(f"Resultado de detección de la simulación {simulation_id}: preparación completa (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"Resultado de detección de la simulación {simulation_id}: preparación no completa (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"Estado no está en la lista de preparados o config_generated es false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"Fallo al leer el archivo de estado: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    Prepara el entorno de simulación (tarea asíncrona, LLM genera todos los parámetros de forma inteligente)
    
    Esta es una operación costosa en tiempo, la interfaz retorna task_id de inmediato,
    usar GET /api/simulation/prepare/status para consultar el progreso
    
    Características:
    - Detecta automáticamente trabajos de preparación completados, evita generación duplicada
    - Si ya está preparado, retorna directamente el resultado existente
    - Soporta regeneración forzada (force_regenerate=true)
    
    Pasos:
    1. Verificar si ya existe trabajo de preparación completado
    2. Leer y filtrar entidades del grafo Zep
    3. Generar OASIS Agent Profile para cada entidad (con mecanismo de reintentos)
    4. LLM genera la configuración de la simulación de forma inteligente (con mecanismo de reintentos)
    5. Guardar archivos de configuración y scripts preestablecidos
    
    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",                   // Requerido, ID de simulación
            "entity_types": ["Student", "PublicFigure"],  // Opcional, especificar tipos de entidad
            "use_llm_for_profiles": true,                 // Opcional, si usar LLM para generar perfiles
            "parallel_profile_count": 5,                  // Opcional, cantidad de perfiles generados en paralelo, por defecto 5
            "force_regenerate": false                     // Opcional, regeneración forzada, por defecto false
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // Se retorna cuando es una nueva tarea
                "status": "preparing|ready",
                "message": "Tarea de preparación iniciada|Ya existe trabajo de preparación completado",
                "already_prepared": true|false    // Si ya está preparado
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        # Verificar si se fuerza la regeneración
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"Procesando solicitud /prepare: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # Verificar si ya está preparado (evitar generación duplicada)
        if not force_regenerate:
            logger.debug(f"Verificando si la simulación {simulation_id} ya está preparada...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"Resultado de verificación: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"La simulación {simulation_id} ya está preparada, omitiendo generación duplicada")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": "Ya existe trabajo de preparación completado, no es necesario regenerar",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"La simulación {simulation_id} no está preparada, iniciando tarea de preparación")
        
        # Obtener información necesaria del proyecto
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"El proyecto no existe: {state.project_id}"
            }), 404
        
        # Obtener requisitos de la simulación
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "El proyecto carece de descripción de requisitos de simulación (simulation_requirement)"
            }), 400
        
        # Obtener texto del documento
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # ========== Obtener el número de entidades de forma sincrónica (antes de iniciar la tarea en segundo plano) ==========
        # Así el frontend puede obtener inmediatamente el total esperado de Agents después de llamar a prepare
        try:
            logger.info(f"Obteniendo número de entidades de forma sincrónica: graph_id={state.graph_id}")
            reader = ZepEntityReader()
            # Leer entidades rápidamente (no se necesita información de aristas, solo contar)
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False  # No obtener información de aristas, para mayor velocidad
            )
            # Guardar el número de entidades en el estado (para que el frontend lo obtenga de inmediato)
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
            logger.info(f"Número esperado de entidades: {filtered_preview.filtered_count}, tipos: {filtered_preview.entity_types}")
        except Exception as e:
            logger.warning(f"Fallo al obtener número de entidades de forma sincrónica (se reintentará en la tarea en segundo plano): {e}")
            # El fallo no afecta el flujo posterior, la tarea en segundo plano lo volverá a obtener
        
        # Crear tarea asíncrona
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # Actualizar estado de la simulación (incluye el número de entidades obtenido previamente)
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)
        
        # Definir tarea en segundo plano
        def run_prepare():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="Iniciando preparación del entorno de simulación..."
                )
                
                # Preparar la simulación (con callback de progreso)
                # Almacenar detalles de progreso de cada etapa
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # Calcular progreso total
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # Construir información de progreso detallada
                    stage_names = {
                        "reading": "Leyendo entidades del grafo",
                        "generating_profiles": "Generando perfiles de Agent",
                        "generating_config": "Generando configuración de simulación",
                        "copying_scripts": "Preparando scripts de simulación"
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # Actualizar detalles de la etapa
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # Construir información de progreso detallada
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # Construir mensaje conciso
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )
                
                # Tarea completada
                task_manager.complete_task(
                    task_id,
                    result=result_state.to_simple_dict()
                )
                
            except Exception as e:
                logger.error(f"Fallo al preparar la simulación: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # Actualizar el estado de la simulación a fallido
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # Iniciar hilo en segundo plano
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": "Tarea de preparación iniciada, consulte el progreso mediante /api/simulation/prepare/status",
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # Total esperado de Agents
                "entity_types": state.entity_types  # Lista de tipos de entidad
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Fallo al iniciar tarea de preparación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    Consulta el progreso de la tarea de preparación
    
    Soporta dos métodos de consulta:
    1. Consultar el progreso de la tarea en curso mediante task_id
    2. Verificar si ya existe trabajo de preparación completado mediante simulation_id
    
    Solicitud (JSON):
        {
            "task_id": "task_xxxx",          // Opcional, task_id retornado por prepare
            "simulation_id": "sim_xxxx"      // Opcional, ID de simulación (para verificar preparación completada)
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // Si ya existe preparación completada
                "prepare_info": {...}            // Información detallada cuando ya está preparado
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # Si se proporcionó simulation_id, verificar primero si ya está preparado
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": "Ya existe trabajo de preparación completado",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
        
        # Si no hay task_id, retornar error
        if not task_id:
            if simulation_id:
                # Hay simulation_id pero no está preparado
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": "La preparación aún no ha comenzado, llame a /api/simulation/prepare para iniciar",
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": "Por favor proporcione task_id o simulation_id"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # La tarea no existe, pero si hay simulation_id, verificar si ya está preparado
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": "Tarea completada (el trabajo de preparación ya existe)",
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": f"La tarea no existe: {task_id}"
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"Fallo al consultar el estado de la tarea: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """Obtiene el estado de la simulación"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        result = state.to_dict()
        
        # Si la simulación está lista, agregar instrucciones de ejecución
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener el estado de la simulación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['DELETE'])
def delete_simulation(simulation_id: str):
    """Eliminar una simulación histórica que no esté corriendo activamente."""
    try:
        force = request.args.get('force', 'false').lower() == 'true'
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404

        run_state = SimulationRunner.get_run_state(simulation_id)
        is_running = bool(
            run_state and run_state.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]
        )

        if is_running and not force:
            return jsonify({
                "success": False,
                "error": "La simulación está en ejecución. Detenla antes de eliminarla."
            }), 409

        if is_running and force:
            try:
                SimulationRunner.stop_simulation(simulation_id)
            except Exception as e:
                logger.warning(f"No se pudo detener la simulación antes de eliminar: {simulation_id}, error={e}")

        result = manager.delete_simulation(simulation_id)
        SimulationRunner._run_states.pop(simulation_id, None)
        SimulationRunner._processes.pop(simulation_id, None)
        SimulationRunner._action_queues.pop(simulation_id, None)
        SimulationRunner._monitor_threads.pop(simulation_id, None)

        return jsonify({
            "success": True,
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:
        logger.error(f"Fallo al eliminar la simulación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    Lista todas las simulaciones
    
    Parámetros de consulta:
        project_id: Filtrar por ID de proyecto (opcional)
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.error(f"Fallo al listar las simulaciones: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
    Obtiene el report_id más reciente correspondiente a la simulación
    
    Recorre el directorio de reports, encuentra el report que coincide con simulation_id,
    si hay varios retorna el más reciente (ordenado por created_at)
    
    Args:
        simulation_id: ID de la simulación
        
    Returns:
        report_id o None
    """
    import json
    from datetime import datetime
    
    # Ruta del directorio de reports: backend/uploads/reports
    # __file__ es app/api/simulation.py, necesita subir dos niveles hasta backend/
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None
    
    matching_reports = []
    
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue
        
        if not matching_reports:
            return None
        
        # Ordenar por tiempo de creación descendente, retornar el más reciente
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
        
    except Exception as e:
        logger.warning(f"Fallo al buscar el report de la simulación {simulation_id}: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
    Obtiene la lista de simulaciones históricas (con detalles del proyecto)
    
    Usada para mostrar proyectos históricos en la página de inicio, retorna lista de simulaciones con información enriquecida como nombre y descripción del proyecto
    
    Parámetros de consulta:
        limit: Límite de cantidad a retornar (por defecto 20)
    
    Retorna:
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "project_id": "proj_xxxx",
                    "project_name": "Análisis de opinión de la Universidad de Wuhan",
                    "simulation_requirement": "Si la Universidad de Wuhan publica...",
                    "status": "completed",
                    "entities_count": 68,
                    "profiles_count": 68,
                    "entity_types": ["Student", "Professor", ...],
                    "created_at": "2024-12-10",
                    "updated_at": "2024-12-10",
                    "total_rounds": 120,
                    "current_round": 120,
                    "report_id": "report_xxxx",
                    "version": "v1.0.2"
                },
                ...
            ],
            "count": 7
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        
        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]
        
        # Enriquecer datos de la simulación, leyendo solo desde archivos de Simulation
        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()
            
            # Obtener información de configuración de la simulación (leer simulation_requirement de simulation_config.json)
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # Rondas recomendadas (valor de respaldo)
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 / 
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0
            
            # Obtener estado de ejecución (leer las rondas reales configuradas por el usuario de run_state.json)
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # Usar total_rounds configurado por el usuario, si no hay usar rondas recomendadas
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds
            
            # Obtener la lista de archivos del proyecto asociado (máximo 3)
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "Archivo desconocido")} 
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []
            
            # Obtener el report_id asociado (buscar el report más reciente de esta simulación)
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            
            # Agregar número de versión
            sim_dict["version"] = "v1.0.2"
            
            # Formatear fecha
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""
            
            enriched_simulations.append(sim_dict)
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener historial de simulaciones: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    Obtiene el Agent Profile de la simulación
    
    Parámetros de consulta:
        platform: Tipo de plataforma (reddit/twitter, por defecto reddit)
    """
    try:
        platform = request.args.get('platform', 'reddit')
        
        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Fallo al obtener Profile: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    Obtiene en tiempo real el Agent Profile de la simulación (para ver el progreso en tiempo real durante la generación)
    
    Diferencia con la interfaz /profiles:
    - Lee el archivo directamente, sin pasar por SimulationManager
    - Adecuada para visualización en tiempo real durante la generación
    - Retorna metadatos adicionales (ej. tiempo de modificación del archivo, si está generando, etc.)
    
    Parámetros de consulta:
        platform: Tipo de plataforma (reddit/twitter, por defecto reddit)
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // Total esperado (si está disponible)
                "is_generating": true,  // Si está generando
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform', 'reddit')
        
        # Obtener directorio de la simulación
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        # Determinar la ruta del archivo
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # Verificar si los archivos existen
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # Obtener tiempo de modificación del archivo
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Fallo al leer el archivo de profiles (puede estar escribiéndose): {e}")
                profiles = []
        
        # Verificar si está generando (determinado mediante state.json)
        is_generating = False
        total_expected = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener Profile en tiempo real: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    Obtiene en tiempo real la configuración de la simulación (para ver el progreso en tiempo real durante la generación)
    
    Diferencia con la interfaz /config:
    - Lee el archivo directamente, sin pasar por SimulationManager
    - Adecuada para visualización en tiempo real durante la generación
    - Retorna metadatos adicionales (ej. tiempo de modificación del archivo, si está generando, etc.)
    - Puede retornar información parcial incluso si la configuración aún no está generada completamente
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  // Si está generando
                "generation_stage": "generating_config",  // Etapa de generación actual
                "config": {...}  // Contenido de la configuración (si existe)
            }
        }
    """
    import json
    from datetime import datetime
    
    try:
        # Obtener directorio de la simulación
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        # Ruta del archivo de configuración
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # Verificar si los archivos existen
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # Obtener tiempo de modificación del archivo
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Fallo al leer el archivo de configuración (puede estar escribiéndose): {e}")
                config = None
        
        # Verificar si está generando (determinado mediante state.json)
        is_generating = False
        generation_stage = None
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)
                    
                    # Determinar la etapa actual
                    if is_generating:
                        if state_data.get("profiles_generated", False):
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
            except Exception:
                pass
        
        # Construir datos de respuesta
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "generation_stage": generation_stage,
            "config_generated": config_generated,
            "config": config
        }
        
        # Si la configuración existe, extraer algunas estadísticas clave
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener configuración en tiempo real: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    Obtiene la configuración de la simulación (configuración completa generada de forma inteligente por LLM)
    
    Retorna que incluye:
        - time_config: Configuración de tiempo (duración de la simulación, rondas, horarios de pico/valle)
        - agent_configs: Configuración de actividad de cada Agent (nivel de actividad, frecuencia de publicación, postura, etc.)
        - event_config: Configuración de eventos (publicaciones iniciales, temas de tendencia)
        - platform_configs: Configuración de plataformas
        - generation_reasoning: Explicación del razonamiento de configuración del LLM
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": f"La configuración de la simulación no existe, llame primero a la interfaz /prepare"
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener la configuración: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """Descarga el archivo de configuración de la simulación"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": "El archivo de configuración no existe, llame primero a la interfaz /prepare"
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.error(f"Fallo al descargar la configuración: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
    Descarga el archivo de script de ejecución de la simulación (script general, ubicado en backend/scripts/)
    
    Valores posibles para script_name:
        - run_twitter_simulation.py
        - run_reddit_simulation.py
        - run_parallel_simulation.py
        - action_logger.py
    """
    try:
        # Los scripts están en el directorio backend/scripts/
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        # Validar el nombre del script
        allowed_scripts = [
            "run_twitter_simulation.py",
            "run_reddit_simulation.py", 
            "run_parallel_simulation.py",
            "action_logger.py"
        ]
        
        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": f"Script desconocido: {script_name}, opciones: {allowed_scripts}"
            }), 400
        
        script_path = os.path.join(scripts_dir, script_name)
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": f"El archivo de script no existe: {script_name}"
            }), 404
        
        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )
        
    except Exception as e:
        logger.error(f"Fallo al descargar el script: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de generación de Profile (uso independiente) ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    Genera directamente OASIS Agent Profile desde el grafo (sin crear simulación)
    
    Solicitud (JSON):
        {
            "graph_id": "mirofish_xxxx",     // Requerido
            "entity_types": ["Student"],      // Opcional
            "use_llm": true,                  // Opcional
            "platform": "reddit"              // Opcional
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione graph_id"
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )
        
        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": "No se encontraron entidades que cumplan los criterios"
            }), 400
        
        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al generar Profile: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de control de ejecución de la simulación ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    Inicia la ejecución de la simulación

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",          // Requerido, ID de simulación
            "platform": "parallel",                // Opcional: twitter / reddit / parallel (por defecto)
            "max_rounds": 100,                     // Opcional: máximo de rondas de simulación, para truncar simulaciones demasiado largas
            "enable_graph_memory_update": false,   // Opcional: si actualizar dinámicamente las actividades de los Agents en la memoria del grafo Zep
            "force": false                         // Opcional: forzar reinicio (detendrá la simulación en ejecución y limpiará logs)
        }

    Acerca del parámetro force:
        - Al activarse, si la simulación está en ejecución o completada, se detiene y se limpian los logs de ejecución
        - El contenido limpiado incluye: run_state.json, actions.jsonl, simulation.log, etc.
        - No limpia los archivos de configuración (simulation_config.json) ni los archivos de profile
        - Adecuado para escenarios donde se necesita volver a ejecutar la simulación

    Acerca de enable_graph_memory_update:
        - Al activarse, todas las actividades de los Agents en la simulación (publicar, comentar, dar me gusta, etc.) se actualizan en tiempo real al grafo Zep
        - Esto permite que el grafo "recuerde" el proceso de simulación, para análisis posterior o diálogos de IA
        - Requiere que el proyecto asociado a la simulación tenga un graph_id válido
        - Usa un mecanismo de actualización por lotes para reducir el número de llamadas a la API

    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // Si se habilitó la actualización de memoria del grafo
                "force_restarted": true               // Si fue un reinicio forzado
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')  # Opcional: máximo de rondas de simulación
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)  # Opcional: si habilitar actualización de memoria del grafo
        force = data.get('force', False)  # Opcional: forzar reinicio

        # Validar el parámetro max_rounds
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": "max_rounds debe ser un entero positivo"
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "max_rounds debe ser un entero válido"
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": f"Tipo de plataforma inválido: {platform}, opciones: twitter/reddit/parallel"
            }), 400

        # Verificar si la simulación está lista
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404

        force_restarted = False
        
        # Manejo inteligente del estado: si la preparación está completa, permitir reinicio
        if state.status != SimulationStatus.READY:
            # Verificar si la preparación está completa
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                # La preparación está completa, verificar si hay procesos en ejecución
                if state.status == SimulationStatus.RUNNING:
                    # Verificar si el proceso de la simulación realmente está ejecutándose
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        # El proceso realmente está en ejecución
                        if force:
                            # Modo forzado: detener la simulación en ejecución
                            logger.info(f"Modo forzado: deteniendo la simulación en ejecución {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"Advertencia al detener la simulación: {str(e)}")
                        else:
                            return jsonify({
                                "success": False,
                                "error": f"La simulación está en ejecución, llame primero a la interfaz /stop para detenerla, o use force=true para forzar el reinicio"
                            }), 400

                # Si es modo forzado, limpiar los logs de ejecución
                if force:
                    logger.info(f"Modo forzado: limpiando logs de la simulación {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"Advertencia al limpiar logs: {cleanup_result.get('errors')}")
                    force_restarted = True

                # El proceso no existe o ya terminó, restablecer el estado a ready
                logger.info(f"La preparación de la simulación {simulation_id} está completa, restableciendo estado a ready (estado anterior: {state.status.value})")
                reset_simulation_runtime_state(state)
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # La preparación no está completa
                return jsonify({
                    "success": False,
                    "error": f"La simulación no está lista, estado actual: {state.status.value}, llame primero a la interfaz /prepare"
                }), 400
        
        # Obtener el ID del grafo (para actualización de memoria del grafo)
        graph_id = None
        if enable_graph_memory_update:
            # Obtener graph_id del estado de la simulación o del proyecto
            graph_id = state.graph_id
            if not graph_id:
                # Intentar obtener del proyecto
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id
            
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": "Habilitar la actualización de memoria del grafo requiere un graph_id válido, asegúrese de que el proyecto tiene grafo construido"
                }), 400
            
            logger.info(f"Habilitando actualización de memoria del grafo: simulation_id={simulation_id}, graph_id={graph_id}")
        
        # Iniciar la simulación
        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=platform,
            max_rounds=max_rounds,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )
        
        # Actualizar estado de la simulación
        mark_simulation_running(state)
        manager._save_simulation_state(state)
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Fallo al iniciar la simulación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    Detiene la simulación
    
    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx"  // Requerido, ID de simulación
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # Actualizar estado de la simulación
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            if run_state.runner_status.value == "failed":
                state.status = SimulationStatus.FAILED
                state.twitter_status = "completed" if run_state.twitter_completed else "failed"
                state.reddit_status = "completed" if run_state.reddit_completed else "failed"
                state.error = run_state.error or state.error
            elif run_state.runner_status.value == "completed":
                state.status = SimulationStatus.COMPLETED
                state.twitter_status = "completed"
                state.reddit_status = "completed"
            else:
                state.status = SimulationStatus.STOPPED
                state.twitter_status = "stopped"
                state.reddit_status = "stopped"
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict(),
            "message": "La simulación ya no estaba activa" if run_state.runner_status.value in ["failed", "completed", "stopped"] else "Simulación detenida"
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Fallo al detener la simulación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de monitoreo de estado en tiempo real ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    Obtiene el estado de ejecución en tiempo real de la simulación (para sondeo del frontend)
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener el estado de ejecución: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    Obtiene el estado detallado de ejecución de la simulación (incluye todas las acciones)
    
    Usada para mostrar actividad en tiempo real en el frontend
    
    Parámetros de consulta:
        platform: Filtrar plataforma (twitter/reddit, opcional)
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # Todas las acciones de la plataforma Twitter
                "reddit_actions": [...]    # Todas las acciones de la plataforma Reddit
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # Obtener la lista completa de acciones
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # Obtener acciones por plataforma
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # Obtener las acciones de la ronda actual (recent_actions solo muestra la última ronda)
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # Obtener información básica del estado
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions solo muestra el contenido de ambas plataformas de la última ronda actual
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener estado detallado: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    Obtiene el historial de acciones de los Agents en la simulación
    
    Parámetros de consulta:
        limit: Cantidad a retornar (por defecto 100)
        offset: Desplazamiento (por defecto 0)
        platform: Filtrar plataforma (twitter/reddit)
        agent_id: Filtrar por ID de Agent
        round_num: Filtrar por número de ronda
    
    Retorna:
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener historial de acciones: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    Obtiene la línea de tiempo de la simulación (resumida por rondas)
    
    Usada para mostrar barra de progreso y vista de línea de tiempo en el frontend
    
    Parámetros de consulta:
        start_round: Ronda inicial (por defecto 0)
        end_round: Ronda final (por defecto todas)
    
    Retorna información resumida de cada ronda
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener la línea de tiempo: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    Obtiene información estadística de cada Agent
    
    Usada para mostrar el ranking de actividad de Agents, distribución de acciones, etc. en el frontend
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener estadísticas de Agents: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de consulta de base de datos ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    Obtiene los posts de la simulación
    
    Parámetros de consulta:
        platform: Tipo de plataforma (twitter/reddit)
        limit: Cantidad a retornar (por defecto 50)
        offset: Desplazamiento
    
    Retorna lista de posts (leída desde la base de datos SQLite)
    """
    try:
        platform = request.args.get('platform', 'reddit')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": "La base de datos no existe, la simulación puede no haber sido ejecutada aún"
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            posts = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener posts: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    Obtiene los comentarios de la simulación (solo Reddit)
    
    Parámetros de consulta:
        post_id: Filtrar por ID de post (opcional)
        limit: Cantidad a retornar
        offset: Desplazamiento
    """
    try:
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, "reddit_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            comments = [dict(row) for row in cursor.fetchall()]
            
        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"Fallo al obtener comentarios: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaces de entrevistas (Interview) ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    Entrevista a un Agent individual

    Nota: esta función requiere que el entorno de simulación esté en ejecución (después de completar el ciclo de simulación entra en modo de espera de comandos)

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",       // Requerido, ID de simulación
            "agent_id": 0,                     // Requerido, Agent ID
            "prompt": "¿Qué opinas sobre esto?",  // Requerido, pregunta de entrevista
            "platform": "twitter",             // Opcional, especificar plataforma (twitter/reddit)
                                               // Si no se especifica: en simulación de doble plataforma se entrevistan ambas plataformas simultáneamente
            "timeout": 60                      // Opcional, tiempo de espera (segundos), por defecto 60
        }

    Retorna (sin especificar platform, modo doble plataforma):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "¿Qué opinas sobre esto?",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    Retorna (especificando platform):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "¿Qué opinas sobre esto?",
                "result": {
                    "agent_id": 0,
                    "response": "Creo que...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Opcional: twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione agent_id"
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione prompt (pregunta de entrevista)"
            }), 400
        
        # Validar el parámetro platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "El parámetro platform solo puede ser 'twitter' o 'reddit'"
            }), 400
        
        # Verificar el estado del entorno
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "El entorno de simulación no está en ejecución o está cerrado. Asegúrese de que la simulación haya completado y esté en modo de espera de comandos."
            }), 400
        
        # Optimizar prompt, agregar prefijo para evitar que el Agent llame a herramientas
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"Tiempo de espera agotado esperando la respuesta de entrevista: {str(e)}"
        }), 504
        
    except Exception as e:
        logger.error(f"Fallo en la entrevista: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    Entrevista a múltiples Agents en lote

    Nota: esta función requiere que el entorno de simulación esté en ejecución

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",       // Requerido, ID de simulación
            "interviews": [                    // Requerido, lista de entrevistas
                {
                    "agent_id": 0,
                    "prompt": "¿Qué opinas sobre A?",
                    "platform": "twitter"      // Opcional, especificar la plataforma de entrevista para ese Agent
                },
                {
                    "agent_id": 1,
                    "prompt": "¿Qué opinas sobre B?"  // Si no se especifica platform se usa el valor por defecto
                }
            ],
            "platform": "reddit",              // Opcional, plataforma por defecto (sobreescrita por el platform de cada ítem)
                                               // Si no se especifica: en simulación de doble plataforma cada Agent es entrevistado en ambas plataformas simultáneamente
            "timeout": 120                     // Opcional, tiempo de espera (segundos), por defecto 120
        }

    Retorna:
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # Opcional: twitter/reddit/None
        try:
            timeout = int(data.get('timeout', 300))
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "timeout debe ser un número entero"
            }), 400
        timeout = max(5, min(timeout, MAX_INTERVIEW_TIMEOUT_SECONDS))

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": "Por favor proporcione interviews (lista de entrevistas)"
            }), 400

        if len(interviews) > MAX_INTERVIEW_BATCH_SIZE:
            return jsonify({
                "success": False,
                "error": f"El lote de entrevistas no puede superar {MAX_INTERVIEW_BATCH_SIZE} destinatarios"
            }), 400

        # Validar el parámetro platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "El parámetro platform solo puede ser 'twitter' o 'reddit'"
            }), 400

        sanitized_interviews = []

        # Validar cada ítem de entrevista
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"El ítem {i+1} de la lista de entrevistas carece de agent_id"
                }), 400
            try:
                agent_id = int(interview.get('agent_id'))
            except (TypeError, ValueError):
                return jsonify({
                    "success": False,
                    "error": f"agent_id del ítem {i+1} debe ser un entero"
                }), 400
            if agent_id < 0:
                return jsonify({
                    "success": False,
                    "error": f"agent_id del ítem {i+1} no puede ser negativo"
                }), 400

            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"El ítem {i+1} de la lista de entrevistas carece de prompt"
                }), 400
            prompt = str(interview.get('prompt', '')).strip()
            if not prompt:
                return jsonify({
                    "success": False,
                    "error": f"prompt del ítem {i+1} no puede estar vacío"
                }), 400
            if len(prompt) > MAX_INTERVIEW_PROMPT_CHARS:
                return jsonify({
                    "success": False,
                    "error": f"prompt del ítem {i+1} supera {MAX_INTERVIEW_PROMPT_CHARS} caracteres"
                }), 400

            # Validar el platform de cada ítem (si existe)
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": f"El platform del ítem {i+1} de la lista de entrevistas solo puede ser 'twitter' o 'reddit'"
                }), 400
            sanitized = {
                "agent_id": agent_id,
                "prompt": prompt
            }
            if item_platform:
                sanitized["platform"] = item_platform
            sanitized_interviews.append(sanitized)

        # Verificar el estado del entorno
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "El entorno de simulación no está en ejecución o está cerrado. Asegúrese de que la simulación haya completado y esté en modo de espera de comandos."
            }), 400

        # Optimizar el prompt de cada ítem de entrevista, agregar prefijo para evitar que el Agent llame a herramientas
        optimized_interviews = []
        for interview in sanitized_interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"Tiempo de espera agotado esperando la respuesta de entrevista en lote: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"Fallo en la entrevista en lote: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    Entrevista global - entrevista a todos los Agents con la misma pregunta

    Nota: esta función requiere que el entorno de simulación esté en ejecución

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",            // Requerido, ID de simulación
            "prompt": "¿Qué opinas en general sobre este asunto?",  // Requerido, pregunta de entrevista (todos los Agents usan la misma pregunta)
            "platform": "reddit",                   // Opcional, especificar plataforma (twitter/reddit)
                                                    // Si no se especifica: en simulación de doble plataforma cada Agent es entrevistado en ambas plataformas simultáneamente
            "timeout": 180                          // Opcional, tiempo de espera (segundos), por defecto 180
        }

    Retorna:
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Opcional: twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione prompt (pregunta de entrevista)"
            }), 400

        # Validar el parámetro platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "El parámetro platform solo puede ser 'twitter' o 'reddit'"
            }), 400

        # Verificar el estado del entorno
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "El entorno de simulación no está en ejecución o está cerrado. Asegúrese de que la simulación haya completado y esté en modo de espera de comandos."
            }), 400

        # Optimizar prompt, agregar prefijo para evitar que el Agent llame a herramientas
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"Tiempo de espera agotado esperando la respuesta de entrevista global: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"Fallo en la entrevista global: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    Obtiene el historial de entrevistas (Interview)

    Lee todos los registros de Interview desde la base de datos de la simulación

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",  // Requerido, ID de simulación
            "platform": "reddit",          // Opcional, tipo de plataforma (reddit/twitter)
                                           // Si no se especifica retorna todo el historial de ambas plataformas
            "agent_id": 0,                 // Opcional, obtener solo el historial de entrevistas de este Agent
            "limit": 100                   // Opcional, cantidad a retornar, por defecto 100
        }

    Retorna:
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "Creo que...",
                        "prompt": "¿Qué opinas sobre esto?",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # Si no se especifica retorna el historial de ambas plataformas
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"Fallo al obtener historial de entrevistas: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    Obtiene el estado del entorno de simulación

    Verifica si el entorno de simulación está activo (puede recibir comandos de Interview)

    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx"  // Requerido, ID de simulación
        }

    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "El entorno está en ejecución, puede recibir comandos de Interview"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # Obtener información de estado más detallada
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = "El entorno está en ejecución, puede recibir comandos de Interview"
        else:
            message = "El entorno no está en ejecución o está cerrado"

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"Fallo al obtener el estado del entorno: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    Cierra el entorno de simulación
    
    Envía el comando de cierre del entorno a la simulación para que salga elegantemente del modo de espera de comandos.
    
    Nota: esto es diferente de la interfaz /stop; /stop fuerza la terminación del proceso,
    mientras que esta interfaz permite que la simulación cierre el entorno de forma elegante y salga.
    
    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",  // Requerido, ID de simulación
            "timeout": 30                  // Opcional, tiempo de espera (segundos), por defecto 30
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "message": "Comando de cierre del entorno enviado",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 90)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # Actualizar estado de la simulación
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            if result.get("success", False):
                state.status = SimulationStatus.COMPLETED
                state.error = None
                state.twitter_status = "completed"
                state.reddit_status = "completed"
            else:
                state.error = result.get("error") or "No se pudo cerrar el entorno de simulación"
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Fallo al cerrar el entorno: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
