"""
Rutas de API de reportes
Provee interfaces para generación, obtención y conversación de reportes de simulación
"""

import json
import os
import traceback
import threading
from datetime import datetime
from flask import request, jsonify, send_file

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.simulation_manager import SimulationManager
from ..models.project import ProjectManager
from ..models.task import TaskManager, TaskStatus
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.report')

FUTURE_ANALYSIS_MAX_REPORT_CHARS = int(os.environ.get("FUTURE_ANALYSIS_MAX_REPORT_CHARS", "42000"))
FUTURE_ANALYSIS_MAX_TOKENS = int(os.environ.get("FUTURE_ANALYSIS_MAX_TOKENS", "2400"))


def _get_future_analysis_paths(report_id: str):
    folder = ReportManager._ensure_report_folder(report_id)
    return (
        os.path.join(folder, "profesor_futuro_analysis.md"),
        os.path.join(folder, "profesor_futuro_analysis.json"),
    )


def _fallback_future_analysis(report) -> str:
    title = report.outline.title if report.outline else "Informe de predicción"
    summary = report.outline.summary if report.outline else ""
    return (
        f"# Análisis Profesor Futuro — {title}\n\n"
        f"Reporte ID: `{report.report_id}`\n\n"
        f"Simulación ID: `{report.simulation_id}`\n\n"
        "## Lectura ejecutiva\n\n"
        f"{summary or 'El reporte está disponible, pero no se pudo generar un análisis enriquecido automático.'}\n\n"
        "## Preguntas base para análisis profundo\n\n"
        "- ¿Qué decisión de negocio se quiere tomar con esta predicción?\n"
        "- ¿Qué patrones se repiten entre la simulación, el grafo y el informe?\n"
        "- ¿Qué señales tienen impacto económico inmediato?\n"
        "- ¿Qué incertidumbres deben validarse antes de ejecutar?\n\n"
        "## Prompt sugerido para otro LLM\n\n"
        "Analiza el reporte completo como futurista estratégico, investigador de mercado y operador de negocio. "
        "Extrae patrones, oportunidades, riesgos, señales débiles, hipótesis de demanda, métricas a observar y próximos experimentos. "
        "Separa hechos del reporte, inferencias y recomendaciones.\n\n"
        "Confianza: baja, porque se usó un resumen fallback sin razonamiento LLM enriquecido.\n"
    )


def _strip_markdown_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _generate_future_analysis(report, force: bool = False):
    md_path, json_path = _get_future_analysis_paths(report.report_id)

    if not force and os.path.exists(md_path):
        with open(md_path, 'r', encoding='utf-8') as f:
            cached_markdown = _strip_markdown_fence(f.read())
        cached_payload = {}
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                cached_payload = json.load(f)
            cached_payload["analysis_markdown"] = cached_markdown
        return cached_markdown, cached_payload, True

    report_markdown = report.markdown_content or ""
    if len(report_markdown) > FUTURE_ANALYSIS_MAX_REPORT_CHARS:
        report_markdown = report_markdown[:FUTURE_ANALYSIS_MAX_REPORT_CHARS] + "\n\n[Contenido truncado para análisis automático]"

    outline = report.outline.to_dict() if report.outline else None
    prompt = (
        "Eres Profesor Futuro, un analista estratégico de predicción para negocios. "
        "Tu trabajo es leer un informe generado por MiroFish y convertirlo en un paquete descargable "
        "para análisis ejecutivo, proyección y uso posterior con otro LLM.\n\n"
        "Reglas:\n"
        "- Escribe en español profesional y accionable.\n"
        "- Separa evidencia del reporte, inferencias y recomendaciones.\n"
        "- No inventes datos externos.\n"
        "- Si una conclusión depende de poca evidencia, marca confianza baja.\n"
        "- Optimiza para que Manuel pueda usar el archivo como entrada de otro LLM.\n\n"
        "Entrega en Markdown con estas secciones exactas:\n"
        "1. Lectura ejecutiva\n"
        "2. Pregunta original y preguntas base que responde el reporte\n"
        "3. Patrones principales detectados\n"
        "4. Predicciones y escenarios probables\n"
        "5. Señales que conviene monitorear\n"
        "6. Oportunidades comerciales inmediatas\n"
        "7. Riesgos, sesgos e incertidumbres\n"
        "8. Próximas preguntas para MiroFish\n"
        "9. Prompt recomendado para análisis profundo en otro LLM\n"
        "10. Confianza general\n\n"
        f"METADATOS:\n"
        f"report_id: {report.report_id}\n"
        f"simulation_id: {report.simulation_id}\n"
        f"graph_id: {report.graph_id}\n"
        f"created_at: {report.created_at}\n"
        f"completed_at: {report.completed_at}\n"
        f"simulation_requirement:\n{report.simulation_requirement}\n\n"
        f"OUTLINE:\n{json.dumps(outline, ensure_ascii=False, indent=2)}\n\n"
        f"REPORTE COMPLETO:\n{report_markdown}\n"
    )

    try:
        analysis_markdown = LLMClient().chat(
            messages=[
                {
                    "role": "system",
                    "content": "Eres Profesor Futuro. Eres riguroso, práctico y enfocado en predicción de negocio."
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=FUTURE_ANALYSIS_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning(f"Profesor Futuro falló para report={report.report_id}: {e}")
        analysis_markdown = _fallback_future_analysis(report)
    analysis_markdown = _strip_markdown_fence(analysis_markdown)

    payload = {
        "report_id": report.report_id,
        "simulation_id": report.simulation_id,
        "graph_id": report.graph_id,
        "generated_at": datetime.now().isoformat(),
        "analysis_markdown": analysis_markdown,
        "report": report.to_dict(),
    }

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(analysis_markdown)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return analysis_markdown, payload, False


# ============== Interfaz de generación de reportes ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    Generar reporte de análisis de simulación (tarea asíncrona)
    
    Esta es una operación de larga duración, la interfaz retorna task_id inmediatamente,
    use GET /api/report/generate/status para consultar el progreso
    
    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",    // Obligatorio, ID de simulación
            "force_regenerate": false        // Opcional, forzar regeneración
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "Tarea de generación de reporte iniciada"
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
        
        force_regenerate = data.get('force_regenerate', False)
        
        # Obtener información de simulación
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        # Verificar si ya existe un reporte
        if not force_regenerate:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "message": "El reporte ya existe",
                        "already_generated": True
                    }
                })
        
        # Obtener información del proyecto
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"El proyecto no existe: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Falta el ID del grafo, asegúrese de que el grafo haya sido construido"
            }), 400
        
        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "Falta la descripción del requisito de simulación"
            }), 400
        
        # Generar report_id de antemano para devolverlo inmediatamente al frontend
        import uuid
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        
        # Crear tarea asíncrona
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="report_generate",
            metadata={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "report_id": report_id
            }
        )
        
        # Definir tarea en segundo plano
        def run_generate():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="Inicializando Report Agent..."
                )
                
                # Crear Report Agent
                agent = ReportAgent(
                    graph_id=graph_id,
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement
                )
                
                # Callback de progreso
                def progress_callback(stage, progress, message):
                    task_manager.update_task(
                        task_id,
                        progress=progress,
                        message=f"[{stage}] {message}"
                    )
                
                # Generar reporte (pasando el report_id pregenerado)
                report = agent.generate_report(
                    progress_callback=progress_callback,
                    report_id=report_id
                )
                
                # Guardar reporte
                ReportManager.save_report(report)
                
                if report.status == ReportStatus.COMPLETED:
                    task_manager.complete_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "completed"
                        }
                    )
                else:
                    task_manager.fail_task(task_id, report.error or "Error al generar el reporte")
                
            except Exception as e:
                logger.error(f"Error al generar el reporte: {str(e)}")
                task_manager.fail_task(task_id, str(e))
        
        # Iniciar hilo en segundo plano
        thread = threading.Thread(target=run_generate, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": "Tarea de generación de reporte iniciada, consulte el progreso en /api/report/generate/status",
                "already_generated": False
            }
        })
        
    except Exception as e:
        logger.error(f"Error al iniciar la tarea de generación de reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['POST'])
def get_generate_status():
    """
    Consultar progreso de la tarea de generación de reporte
    
    Solicitud (JSON):
        {
            "task_id": "task_xxxx",         // Opcional, task_id retornado por generate
            "simulation_id": "sim_xxxx"     // Opcional, ID de simulación
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # Si se proporcionó simulation_id, verificar primero si ya hay un reporte completado
        if simulation_id:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "progress": 100,
                        "message": "El reporte ya fue generado",
                        "already_completed": True
                    }
                })
        
        if not task_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione task_id o simulation_id"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                "success": False,
                "error": f"La tarea no existe: {task_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": task.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error al consultar el estado de la tarea: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Interfaz de obtención de reportes ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    Obtener detalles del reporte
    
    Retorna:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"El reporte no existe: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    Obtener reporte según el ID de simulación
    
    Retorna:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"Esta simulación no tiene reporte aún: {simulation_id}",
                "has_report": False
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    Listar todos los reportes
    
    Parámetros de query:
        simulation_id: Filtrar por ID de simulación (opcional)
        limit: Límite de cantidad retornada (por defecto 50)
    
    Retorna:
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)
        
        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })
        
    except Exception as e:
        logger.error(f"Error al listar reportes: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    Descargar reporte (formato Markdown o TXT)

    Parámetros de query:
        format: 'txt' para descargar como archivo de texto plano (por defecto: 'md')

    Retorna archivo Markdown o TXT
    """
    try:
        report = ReportManager.get_report(report_id)

        if not report:
            return jsonify({
                "success": False,
                "error": f"El reporte no existe: {report_id}"
            }), 404

        fmt = request.args.get('format', 'md').lower()
        is_txt = fmt == 'txt'
        ext = 'txt' if is_txt else 'md'
        mimetype = 'text/plain'

        md_path = ReportManager._get_report_markdown_path(report_id)

        if not os.path.exists(md_path):
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{ext}', delete=False) as f:
                f.write(report.markdown_content)
                temp_path = f.name

            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f"{report_id}.{ext}",
                mimetype=mimetype
            )

        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.{ext}",
            mimetype=mimetype
        )
        
    except Exception as e:
        logger.error(f"Error al descargar el reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/future-analysis', methods=['POST'])
def generate_future_analysis(report_id: str):
    """
    Generar análisis descargable tipo Profesor Futuro para un reporte existente.
    """
    try:
        data = request.get_json() or {}
        force = bool(data.get("force", False))

        report = ReportManager.get_report(report_id)
        if not report:
            return jsonify({
                "success": False,
                "error": f"El reporte no existe: {report_id}"
            }), 404

        analysis_markdown, payload, cached = _generate_future_analysis(report, force=force)

        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "simulation_id": report.simulation_id,
                "cached": cached,
                "analysis_markdown": analysis_markdown,
                "payload": payload,
            }
        })

    except Exception as e:
        logger.error(f"Error al generar análisis Profesor Futuro: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """Eliminar reporte"""
    try:
        success = ReportManager.delete_report(report_id)
        
        if not success:
            return jsonify({
                "success": False,
                "error": f"El reporte no existe: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "message": f"Reporte eliminado: {report_id}"
        })
        
    except Exception as e:
        logger.error(f"Error al eliminar el reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de conversación con Report Agent ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    Conversar con Report Agent
    
    Report Agent puede llamar autónomamente herramientas de recuperación durante la conversación para responder preguntas
    
    Solicitud (JSON):
        {
            "simulation_id": "sim_xxxx",        // Obligatorio, ID de simulación
            "message": "Por favor explique la tendencia de opinión pública",    // Obligatorio, mensaje del usuario
            "chat_history": [                   // Opcional, historial de conversación
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }
    
    Retorna:
        {
            "success": true,
            "data": {
                "response": "Respuesta del Agent...",
                "tool_calls": [lista de herramientas llamadas],
                "sources": [fuentes de información]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione simulation_id"
            }), 400
        
        if not message:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione message"
            }), 400
        
        # Obtener información de simulación y proyecto
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"La simulación no existe: {simulation_id}"
            }), 404
        
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"El proyecto no existe: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Falta el ID del grafo"
            }), 400
        
        simulation_requirement = project.simulation_requirement or ""
        
        # Crear Agent e iniciar conversación
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement
        )
        
        result = agent.chat(message=message, chat_history=chat_history)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Error en la conversación: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de progreso y secciones del reporte ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    Obtener progreso de generación del reporte (en tiempo real)
    
    Retorna:
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "Generando sección: Hallazgos clave",
                "current_section": "Hallazgos clave",
                "completed_sections": ["Resumen ejecutivo", "Contexto de simulación"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)
        
        if not progress:
            return jsonify({
                "success": False,
                "error": f"El reporte no existe o la información de progreso no está disponible: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": progress
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el progreso del reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    Obtener lista de secciones ya generadas (salida por secciones)
    
    El frontend puede hacer polling de esta interfaz para obtener contenido de secciones ya generadas, sin esperar a que todo el reporte esté completo
    
    Retorna:
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## Resumen ejecutivo\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)
        
        # Obtener estado del reporte
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener la lista de secciones: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    Obtener contenido de una sección individual
    
    Retorna:
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## Resumen ejecutivo\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)
        
        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": f"La sección no existe: section_{section_index:02d}.md"
            }), 404
        
        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el contenido de la sección: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de verificación de estado del reporte ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    Verificar si la simulación tiene reporte y el estado del reporte
    
    Usado por el frontend para determinar si se desbloquea la funcionalidad de Interview
    
    Retorna:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None
        
        # Solo desbloquear interview cuando el reporte esté completo
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })
        
    except Exception as e:
        logger.error(f"Error al verificar el estado del reporte: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de log del Agent ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    Obtener el log detallado de ejecución del Report Agent
    
    Obtener en tiempo real cada paso de la generación del reporte, incluyendo:
    - Inicio del reporte, inicio/finalización de la planificación
    - Inicio, llamadas a herramientas, respuesta del LLM y finalización de cada sección
    - Completado o fallo del reporte
    
    Parámetros de query:
        from_line: Desde qué línea comenzar a leer (opcional, por defecto 0, para obtención incremental)
    
    Retorna:
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "Resumen ejecutivo",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el log del Agent: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    Obtener el log completo del Agent (obtención única de todo)
    
    Retorna:
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el log del Agent: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de log de consola ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    Obtener el log de salida de consola del Report Agent
    
    Obtener en tiempo real la salida de consola durante la generación del reporte (INFO, WARNING, etc.),
    esto es diferente del log JSON estructurado retornado por la interfaz agent-log,
    es un log de estilo consola en formato de texto plano.
    
    Parámetros de query:
        from_line: Desde qué línea comenzar a leer (opcional, por defecto 0, para obtención incremental)
    
    Retorna:
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: Búsqueda completada: se encontraron 15 hechos relevantes",
                    "[19:46:14] INFO: Búsqueda en grafo: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_console_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el log de consola: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    Obtener el log completo de consola (obtención única de todo)
    
    Retorna:
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener el log de consola: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interfaz de llamada a herramientas (para depuración) ==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    Interfaz de herramienta de búsqueda en grafo (para depuración)
    
    Solicitud (JSON):
        {
            "graph_id": "mirofish_xxxx",
            "query": "consulta de búsqueda",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)
        
        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": "Por favor proporcione graph_id y query"
            }), 400
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"Error en la búsqueda del grafo: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    Interfaz de herramienta de estadísticas del grafo (para depuración)
    
    Solicitud (JSON):
        {
            "graph_id": "mirofish_xxxx"
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
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.get_graph_statistics(graph_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Error al obtener estadísticas del grafo: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
