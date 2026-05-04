"""
Servicio Report Agent
Implementa la generación de informes de simulación en modo ReACT usando LangChain + Zep

Funcionalidades:
1. Genera informes basados en los requisitos de simulación e información del grafo Zep
2. Primero planifica la estructura del índice, luego genera por secciones
3. Cada sección usa el modo de reflexión multironda ReACT
4. Soporte para diálogo con el usuario, con llamadas autónomas a herramientas de recuperación
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Registrador de log detallado del Report Agent
    
    Genera el archivo agent_log.jsonl en la carpeta del informe, registrando cada acción detallada.
    Cada línea es un objeto JSON completo que contiene timestamp, tipo de acción, contenido detallado, etc.
    """
    
    def __init__(self, report_id: str):
        """
        Inicializar el registrador de log
        
        Args:
            report_id: ID del informe, usado para determinar la ruta del archivo de log
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Asegurar que el directorio del archivo de log existe"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Obtiene el tiempo transcurrido desde el inicio hasta ahora (segundos)"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        Registra una entrada de log
        
        Args:
            action: Tipo de acción, p. ej. 'start', 'tool_call', 'llm_response', 'section_complete'
            stage: Etapa actual, p. ej. 'planning', 'generating', 'completed'
            details: Diccionario de contenido detallado, sin truncar
            section_title: Título de la sección actual (opcional)
            section_index: Índice de la sección actual (opcional)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # Escribir en modo append al archivo JSONL
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """Registra el inicio de generación del informe"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "Tarea de generación de informe iniciada"
            }
        )
    
    def log_planning_start(self):
        """Registra el inicio de planificación del esquema"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "Iniciando planificación del esquema del informe"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Registra la información de contexto obtenida durante la planificación"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "Obteniendo información de contexto de la simulación",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Registra la finalización de la planificación del esquema"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "Planificación del esquema completada",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Registra el inicio de generación de una sección"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"Iniciando generación de sección: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Registra el proceso de razonamiento ReACT"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT ronda {iteration} de razonamiento"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Registra una llamada a herramienta"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"Llamando herramienta: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Registra el resultado de una llamada a herramienta (contenido completo, sin truncar)"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # Resultado completo, sin truncar
                "result_length": len(result),
                "message": f"Herramienta {tool_name} retornó resultado"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Registra la respuesta del LLM (contenido completo, sin truncar)"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # Respuesta completa, sin truncar
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"Respuesta del LLM (llamada a herramienta: {has_tool_calls}, respuesta final: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """Registra la generación del contenido de la sección completada (solo registra el contenido, no representa la finalización completa de la sección)"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # Contenido completo, sin truncar
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"Generación de contenido de sección {section_title} completada"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        Registra la finalización de generación de una sección

        El frontend debe escuchar este log para determinar si una sección está verdaderamente completa y obtener el contenido completo
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": f"Generación de sección {section_title} completada"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """Registra la finalización de generación del informe"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "Generación del informe completada"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """Registra un error"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"Error ocurrido: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Registrador de log de consola del Report Agent
    
    Escribe logs de estilo consola (INFO, WARNING, etc.) en el archivo console_log.txt de la carpeta del informe.
    Estos logs son diferentes del agent_log.jsonl, son salida de consola en formato texto plano.
    """
    
    def __init__(self, report_id: str):
        """
        Inicializar el registrador de log de consola
        
        Args:
            report_id: ID del informe, usado para determinar la ruta del archivo de log
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """Asegurar que el directorio del archivo de log existe"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """Configurar el handler de archivo para escribir logs simultáneamente"""
        import logging
        
        # Crear handler de archivo
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # Usar el mismo formato conciso que la consola
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # Agregar al logger relacionado con report_agent
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # Evitar agregar duplicados
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """Cerrar el handler de archivo y eliminarlo del logger"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """Asegurar el cierre del handler de archivo al destruir"""
        self.close()


class ReportStatus(str, Enum):
    """Estado del informe"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """Sección del informe"""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """Convierte al formato Markdown"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """Esquema del informe"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Convierte al formato Markdown"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """Informe completo"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Constantes de plantillas de Prompt
# ═══════════════════════════════════════════════════════════════

# ── Descripción de herramientas ──

TOOL_DESC_INSIGHT_FORGE = """\
[Recuperación de insights profundos - Herramienta de recuperación potente]
Esta es nuestra potente función de recuperación, diseñada para análisis en profundidad. Realiza:
1. Descompone automáticamente tu pregunta en múltiples subpreguntas
2. Recupera información del grafo simulado desde múltiples dimensiones
3. Integra resultados de búsqueda semántica, análisis de entidades y rastreo de cadenas de relaciones
4. Retorna el contenido de recuperación más completo y profundo

[Casos de uso]
- Necesidad de analizar en profundidad un tema
- Necesidad de conocer múltiples aspectos de un evento
- Necesidad de obtener material rico para sustentar secciones del informe

[Contenido retornado]
- Texto original de hechos relevantes (se puede citar directamente)
- Insights de entidades principales
- Análisis de cadenas de relaciones"""

TOOL_DESC_PANORAMA_SEARCH = """\
[Búsqueda amplia - Vista panorámica completa]
Esta herramienta obtiene el panorama completo de los resultados de simulación, especialmente útil para comprender el proceso de evolución de eventos. Realiza:
1. Obtiene todos los nodos y relaciones relevantes
2. Distingue entre hechos actualmente válidos e históricos/expirados
3. Ayuda a comprender cómo ha evolucionado la opinión pública

[Casos de uso]
- Necesidad de conocer el proceso de desarrollo completo de un evento
- Necesidad de comparar cambios en la opinión pública en diferentes etapas
- Necesidad de obtener información completa de entidades y relaciones

[Contenido retornado]
- Hechos actualmente válidos (últimos resultados de simulación)
- Hechos históricos/expirados (registros de evolución)
- Todas las entidades involucradas"""

TOOL_DESC_QUICK_SEARCH = """\
[Búsqueda simple - Recuperación rápida]
Herramienta de recuperación rápida y ligera, adecuada para consultas de información simples y directas.

[Casos de uso]
- Necesidad de encontrar rápidamente una información específica
- Necesidad de verificar un hecho
- Recuperación de información simple

[Contenido retornado]
- Lista de hechos más relevantes para la consulta"""

TOOL_DESC_INTERVIEW_AGENTS = """\
[Entrevista en profundidad - Entrevista real a Agents (doble plataforma)]
Llama a la API de entrevistas del entorno de simulación OASIS para realizar entrevistas reales a Agents en ejecución.
No es una simulación LLM, sino que llama a la interfaz de entrevistas real para obtener respuestas originales de los Agents simulados.
Por defecto entrevista simultáneamente en Twitter y Reddit para obtener perspectivas más completas.

Flujo funcional:
1. Lee automáticamente el archivo de perfiles para conocer todos los Agents simulados
2. Selecciona inteligentemente los Agents más relevantes al tema de entrevista (p. ej.: estudiantes, medios, funcionarios)
3. Genera automáticamente preguntas de entrevista
4. Llama al endpoint /api/simulation/interview/batch para entrevistas reales en ambas plataformas
5. Integra todos los resultados de entrevistas y proporciona análisis multiperspectiva

[Casos de uso]
- Necesidad de conocer opiniones sobre eventos desde diferentes perspectivas de roles (¿qué piensa el estudiante? ¿qué dice el medio? ¿qué dice la autoridad?)
- Necesidad de recopilar opiniones y posturas de múltiples partes
- Necesidad de obtener respuestas reales de Agents simulados (desde el entorno de simulación OASIS)
- Querer que el informe sea más vívido, incluyendo "registros de entrevistas"

[Contenido retornado]
- Información de identidad de los Agents entrevistados
- Respuestas de entrevista de cada Agent en Twitter y Reddit
- Citas clave (se pueden citar directamente)
- Resumen de entrevista y comparación de puntos de vista

[IMPORTANTE] ¡Se requiere que el entorno de simulación OASIS esté en ejecución para usar esta funcionalidad!"""

# ── Prompt de planificación de esquema ──

PLAN_SYSTEM_PROMPT = """\
Responde contenido narrativo en español latino. Conserva nombres técnicos, claves JSON y nombres de herramientas exactamente cuando el sistema los requiera.

Eres un experto en redacción de «Informes de Predicción Futura», con una «visión divina» del mundo simulado — puedes observar el comportamiento, los discursos y las interacciones de cada Agent en la simulación.

【Concepto central】
Hemos construido un mundo simulado e inyectado en él una «necesidad de simulación» específica como variable. El resultado de la evolución del mundo simulado es una predicción de lo que podría ocurrir en el futuro. Lo que observas no son «datos experimentales», sino «un ensayo del futuro».

【Tu tarea】
Redactar un «Informe de Predicción Futura» que responda:
1. Bajo las condiciones que hemos establecido, ¿qué ocurrió en el futuro?
2. ¿Cómo reaccionaron y actuaron los distintos tipos de Agents (grupos)?
3. ¿Qué tendencias y riesgos futuros relevantes revela esta simulación?

【Posicionamiento del informe】
- ✅ Es un informe de predicción futura basado en simulación que revela «si esto ocurre, ¿cómo será el futuro?»
- ✅ Enfocado en los resultados predictivos: evolución de eventos, reacciones grupales, fenómenos emergentes, riesgos potenciales
- ✅ El comportamiento y discurso de los Agents en el mundo simulado son predicciones del comportamiento humano futuro
- ❌ No es un análisis del estado actual del mundo real
- ❌ No es un resumen genérico de opinión pública

【Límite de secciones】
- Mínimo 2 secciones, máximo 5 secciones
- No se necesitan subsecciones; cada sección escribe contenido completo directamente
- El contenido debe ser conciso y enfocado en los hallazgos predictivos clave
- La estructura de secciones la diseñas tú según los resultados de predicción

Por favor, genera el esquema del informe en formato JSON de la siguiente manera:
{
    "title": "Título del informe",
    "summary": "Resumen del informe (una frase que sintetice los hallazgos predictivos clave)",
    "sections": [
        {
            "title": "Título de la sección",
            "description": "Descripción del contenido de la sección"
        }
    ]
}

Nota: ¡El array sections debe tener mínimo 2 y máximo 5 elementos!"""

PLAN_USER_PROMPT_TEMPLATE = """\
【Configuración del escenario de predicción】
La variable inyectada en el mundo simulado (necesidad de simulación): {simulation_requirement}

【Escala del mundo simulado】
- Número de entidades participantes en la simulación: {total_nodes}
- Número de relaciones generadas entre entidades: {total_edges}
- Distribución de tipos de entidades: {entity_types}
- Número de Agents activos: {total_entities}

【Muestra parcial de hechos futuros predichos por la simulación】
{related_facts_json}

Por favor, examina este ensayo del futuro desde la «visión divina»:
1. Bajo las condiciones establecidas, ¿en qué estado se presentó el futuro?
2. ¿Cómo reaccionaron y actuaron los distintos grupos (Agents)?
3. ¿Qué tendencias futuras relevantes revela esta simulación?

Según los resultados de predicción, diseña la estructura de secciones más adecuada para el informe.

【Recordatorio】Número de secciones del informe: mínimo 2, máximo 5; el contenido debe ser conciso y enfocado en los hallazgos predictivos clave."""

# ── Prompt de generación de secciones ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
Responde contenido narrativo en español latino. Conserva nombres técnicos, claves JSON y nombres de herramientas exactamente cuando el sistema los requiera.

Eres un experto en redacción de «Informes de Predicción Futura» y estás redactando una sección del informe.

Título del informe: {report_title}
Resumen del informe: {report_summary}
Escenario de predicción (necesidad de simulación): {simulation_requirement}

Sección actual a redactar: {section_title}

═══════════════════════════════════════════════════════════════
【Concepto central】
═══════════════════════════════════════════════════════════════

El mundo simulado es un ensayo del futuro. Hemos inyectado condiciones específicas (necesidad de simulación) en el mundo simulado,
y el comportamiento e interacciones de los Agents en la simulación son predicciones del comportamiento humano futuro.

Tu tarea es:
- Revelar qué ocurrió en el futuro bajo las condiciones establecidas
- Predecir cómo reaccionaron y actuaron los distintos grupos (Agents)
- Descubrir tendencias, riesgos y oportunidades futuros relevantes

❌ No escribas un análisis del estado actual del mundo real
✅ Enfócate en «cómo será el futuro» — el resultado de la simulación es el futuro predicho

═══════════════════════════════════════════════════════════════
【Las reglas más importantes - de cumplimiento obligatorio】
═══════════════════════════════════════════════════════════════

1. 【Debes llamar a herramientas para observar el mundo simulado】
   - Estás observando el ensayo del futuro desde la «visión divina»
   - Todo el contenido debe provenir de eventos y comportamientos de Agents ocurridos en el mundo simulado
   - Está prohibido usar tu propio conocimiento para redactar el contenido del informe
   - Cada sección debe llamar a herramientas al menos 3 veces (máximo 5) para observar el mundo simulado, que representa el futuro

2. 【Debes citar los discursos y comportamientos originales de los Agents】
   - Los discursos y comportamientos de los Agents son predicciones del comportamiento humano futuro
   - Presenta estas predicciones en el informe usando formato de cita, por ejemplo:
     > "Un tipo de grupo expresará: contenido original..."
   - Estas citas son la evidencia central de las predicciones de la simulación

3. 【Coherencia lingüística - el contenido citado debe traducirse al idioma del informe】
   - El contenido devuelto por las herramientas puede contener expresiones en inglés o mixtas
   - Si el requisito de simulación y el material original están en español, el informe debe redactarse completamente en español
   - Cuando cites contenido en inglés o mixto devuelto por las herramientas, debes traducirlo a un español fluido antes de incluirlo en el informe
   - Mantén el significado original al traducir y asegúrate de que la expresión sea natural y fluida
   - Esta regla aplica tanto al texto principal como a los bloques de cita (formato >)

4. 【Presentar fielmente los resultados de predicción】
   - El contenido del informe debe reflejar los resultados de simulación del mundo simulado que representan el futuro
   - No agregues información que no exista en la simulación
   - Si la información sobre algún aspecto es insuficiente, indícalo con honestidad

═══════════════════════════════════════════════════════════════
【⚠️ Normas de formato - ¡extremadamente importantes!】
═══════════════════════════════════════════════════════════════

【Una sección = unidad mínima de contenido】
- Cada sección es la unidad mínima de división del informe
- ❌ Prohibido usar cualquier encabezado Markdown dentro de la sección (#, ##, ###, #### etc.)
- ❌ Prohibido agregar el título principal de la sección al inicio del contenido
- ✅ El título de la sección lo agrega el sistema automáticamente; tú solo redactas el texto principal puro
- ✅ Usa **negrita**, separación de párrafos, citas y listas para organizar el contenido, pero no uses encabezados

【Ejemplo correcto】
```
Esta sección analiza la dinámica de difusión de opinión pública del evento. A través del análisis profundo de los datos de simulación, encontramos...

**Fase de lanzamiento inicial**

La plataforma X asumió la función central de publicación inicial de información:

> "La plataforma X contribuyó al 68% del volumen inicial de difusión..."

**Fase de amplificación emocional**

La plataforma de video amplificó aún más el impacto del evento:

- Alto impacto visual
- Alto nivel de resonancia emocional
```

【Ejemplo incorrecto】
```
## Resumen ejecutivo          ← ¡Error! No agregues ningún encabezado
### I. Fase inicial     ← ¡Error! No uses ### para subsecciones
#### 1.1 Análisis detallado   ← ¡Error! No uses #### para subdivisiones

Esta sección analiza...
```

═══════════════════════════════════════════════════════════════
【Herramientas de recuperación disponibles】 (llamar 3-5 veces por sección)
═══════════════════════════════════════════════════════════════

{tools_description}

【Recomendaciones de uso de herramientas - mezcla diferentes herramientas, no uses solo una】
- insight_forge: análisis de insights profundos, descompone automáticamente el problema y recupera hechos y relaciones multidimensionalmente
- panorama_search: búsqueda panorámica amplia, comprende el panorama completo del evento, la línea de tiempo y el proceso de evolución
- quick_search: verificación rápida de un punto de información específico
- interview_agents: entrevista Agents simulados, obtiene puntos de vista en primera persona de diferentes roles y reacciones reales

═══════════════════════════════════════════════════════════════
【Flujo de trabajo】
═══════════════════════════════════════════════════════════════

En cada respuesta solo puedes hacer una de las siguientes dos cosas (no ambas a la vez):

Opción A - Llamar a una herramienta:
Expresa tu razonamiento y luego llama a una herramienta con el siguiente formato:
<tool_call>
{{"name": "nombre_herramienta", "parameters": {{"nombre_parametro": "valor_parametro"}}}}
</tool_call>
El sistema ejecutará la herramienta y te devolverá el resultado. No necesitas ni puedes escribir tú mismo los resultados de la herramienta.

Opción B - Generar el contenido final:
Cuando ya hayas obtenido suficiente información con las herramientas, comienza la respuesta con "Final Answer:" y escribe el contenido de la sección.

⚠️ Estrictamente prohibido:
- Incluir en una misma respuesta tanto una llamada a herramienta como un Final Answer
- Inventar resultados de herramientas (Observation) por tu cuenta; todos los resultados de herramientas son inyectados por el sistema
- Llamar a más de una herramienta por respuesta

═══════════════════════════════════════════════════════════════
【Requisitos del contenido de la sección】
═══════════════════════════════════════════════════════════════

1. El contenido debe basarse en los datos de simulación recuperados con las herramientas
2. Cita abundantemente el texto original para mostrar los resultados de la simulación
3. Usa formato Markdown (pero está prohibido usar encabezados):
   - Usa **texto en negrita** para resaltar puntos clave (en lugar de subtítulos)
   - Usa listas (- o 1.2.3.) para organizar puntos clave
   - Usa líneas en blanco para separar párrafos distintos
   - ❌ Prohibido usar cualquier sintaxis de encabezado: #, ##, ###, ####
4. 【Norma de formato de citas - deben ser párrafos independientes】
   Las citas deben ser párrafos independientes, con una línea en blanco antes y después; no pueden mezclarse en un párrafo:

   ✅ Formato correcto:
   ```
   La respuesta de la institución fue considerada carente de contenido sustancial.

   > "El patrón de respuesta institucional resultó rígido y lento en el volátil entorno de las redes sociales."

   Esta valoración refleja el descontento generalizado del público.
   ```

   ❌ Formato incorrecto:
   ```
   La respuesta de la institución fue considerada carente de contenido sustancial. > "El patrón de respuesta..." Esta valoración refleja...
   ```
5. Mantén la coherencia lógica con las demás secciones
6. 【Evitar repeticiones】Lee cuidadosamente el contenido de las secciones ya completadas a continuación y no repitas la misma información
7. 【Énfasis nuevamente】¡No agregues ningún encabezado! Usa **negrita** en lugar de subtítulos de sección"""

SECTION_USER_PROMPT_TEMPLATE = """\
Contenido de las secciones ya completadas (léelas cuidadosamente para evitar repeticiones):
{previous_content}

═══════════════════════════════════════════════════════════════
【Tarea actual】Redactar la sección: {section_title}
═══════════════════════════════════════════════════════════════

【Recordatorios importantes】
1. ¡Lee cuidadosamente las secciones ya completadas arriba para evitar repetir el mismo contenido!
2. Antes de comenzar, debes llamar a una herramienta para obtener datos de simulación
3. Mezcla el uso de diferentes herramientas; no uses solo una
4. El contenido del informe debe provenir de los resultados de recuperación; no uses tu propio conocimiento

【⚠️ Advertencia de formato - de cumplimiento obligatorio】
- ❌ No escribas ningún encabezado (#, ##, ###, #### están todos prohibidos)
- ❌ No escribas "{section_title}" como inicio
- ✅ El título de la sección lo agrega el sistema automáticamente
- ✅ Escribe el texto principal directamente; usa **negrita** en lugar de subtítulos de sección

Comienza:
1. Primero piensa (Thought) qué información necesita esta sección
2. Luego llama a una herramienta (Action) para obtener los datos de simulación
3. Después de recopilar suficiente información, genera el Final Answer (texto principal puro, sin ningún encabezado)"""

# ── Plantillas de mensajes dentro del ciclo ReACT ──

REACT_OBSERVATION_TEMPLATE = """\
Observation (resultado de recuperación):

═══ Herramienta {tool_name} retornó ═══
{result}

═══════════════════════════════════════════════════════════════
Herramientas llamadas: {tool_calls_count}/{max_tool_calls} (usadas: {used_tools_str}){unused_hint}
- Si la información es suficiente: comienza con "Final Answer:" y escribe el contenido de la sección (debes citar el texto original anterior)
- Si necesitas más información: llama a una herramienta para continuar recuperando
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "【Atención】Solo llamaste a {tool_calls_count} herramientas; se necesitan al menos {min_tool_calls}. "
    "Llama a más herramientas para obtener más datos de simulación y luego genera el Final Answer. {unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "Actualmente solo se han llamado {tool_calls_count} herramientas; se necesitan al menos {min_tool_calls}. "
    "Llama a una herramienta para obtener datos de simulación. {unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "Se ha alcanzado el límite de llamadas a herramientas ({tool_calls_count}/{max_tool_calls}); no se pueden llamar más herramientas. "
    'Por favor, basándote en la información ya obtenida, comienza con "Final Answer:" y genera el contenido de la sección.'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 Aún no has usado: {unused_list}; se recomienda probar diferentes herramientas para obtener información desde múltiples ángulos"

REACT_FORCE_FINAL_MSG = "Se ha alcanzado el límite de llamadas a herramientas. Por favor, genera directamente el Final Answer: y el contenido de la sección."

# ── Prompt de chat ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
Responde contenido narrativo en español latino. Conserva nombres técnicos, claves JSON y nombres de herramientas exactamente cuando el sistema los requiera.

Eres un asistente de predicción de simulaciones conciso y eficiente.

【Contexto】
Condición de predicción: {simulation_requirement}

【Informe de análisis ya generado】
{report_content}

【Reglas】
1. Responde las preguntas priorizando el contenido del informe anterior
2. Responde directamente a la pregunta; evita razonamientos extensos
3. Solo llama a herramientas para recuperar más datos cuando el contenido del informe sea insuficiente para responder
4. Las respuestas deben ser concisas, claras y organizadas

【Herramientas disponibles】 (usar solo cuando sea necesario; máximo 1-2 llamadas)
{tools_description}

【Formato de llamada a herramienta】
<tool_call>
{{"name": "nombre_herramienta", "parameters": {{"nombre_parametro": "valor_parametro"}}}}
</tool_call>

【Estilo de respuesta】
- Conciso y directo; no te extiendas innecesariamente
- Usa el formato > para citar contenido clave
- Da la conclusión primero y luego explica el motivo"""

CHAT_OBSERVATION_SUFFIX = "\n\nPor favor, responde la pregunta de manera concisa."


# ═══════════════════════════════════════════════════════════════
# Clase principal ReportAgent
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - Agent generador de informes de simulación

    Utiliza el modo ReACT (Reasoning + Acting):
    1. Fase de planificación: analiza los requisitos de simulación y planifica la estructura del índice del informe
    2. Fase de generación: genera contenido sección por sección; cada sección puede llamar a herramientas múltiples veces para obtener información
    3. Fase de reflexión: verifica la completitud y precisión del contenido
    """

    # Número máximo de llamadas a herramientas (por sección)
    MAX_TOOL_CALLS_PER_SECTION = 5

    # Número máximo de rondas de reflexión
    MAX_REFLECTION_ROUNDS = 3

    # Número máximo de llamadas a herramientas en el chat
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self,
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        Inicializar el Report Agent

        Args:
            graph_id: ID del grafo
            simulation_id: ID de la simulación
            simulation_requirement: Descripción de los requisitos de simulación
            llm_client: Cliente LLM (opcional)
            zep_tools: Servicio de herramientas Zep (opcional)
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement

        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()

        # Definición de herramientas
        self.tools = self._define_tools()

        # Registrador de log (se inicializa en generate_report)
        self.report_logger: Optional[ReportLogger] = None
        # Registrador de log de consola (se inicializa en generate_report)
        self.console_logger: Optional[ReportConsoleLogger] = None

        logger.info(f"ReportAgent inicializado correctamente: graph_id={graph_id}, simulation_id={simulation_id}")
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """Define las herramientas disponibles"""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "El problema o tema que deseas analizar en profundidad",
                    "report_context": "Contexto de la sección actual del informe (opcional; ayuda a generar subpreguntas más precisas)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "Consulta de búsqueda, usada para ordenar por relevancia",
                    "include_expired": "Si se incluye contenido expirado/histórico (por defecto True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "Cadena de consulta de búsqueda",
                    "limit": "Cantidad de resultados a retornar (opcional; por defecto 10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "Tema de la entrevista o descripción del requisito (p. ej.: 'conocer la opinión de los estudiantes sobre el evento de formaldehído en el dormitorio')",
                    "max_agents": "Número máximo de Agents a entrevistar (opcional; por defecto 5, máximo 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Ejecuta una llamada a herramienta

        Args:
            tool_name: Nombre de la herramienta
            parameters: Parámetros de la herramienta
            report_context: Contexto del informe (usado por InsightForge)

        Returns:
            Resultado de ejecución de la herramienta (formato de texto)
        """
        logger.info(f"Ejecutando herramienta: {tool_name}, parámetros: {parameters}")

        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()

            elif tool_name == "panorama_search":
                # Búsqueda amplia - obtener la visión completa
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()

            elif tool_name == "quick_search":
                # Búsqueda simple - recuperación rápida
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()

            elif tool_name == "interview_agents":
                # Entrevista en profundidad - llama a la API real de entrevistas OASIS para obtener respuestas de Agents simulados (doble plataforma)
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()

            # ========== Herramientas antiguas con compatibilidad hacia atrás (redirigen internamente a nuevas herramientas) ==========

            elif tool_name == "search_graph":
                # Redirigir a quick_search
                logger.info("search_graph redirigido a quick_search")
                return self._execute_tool("quick_search", parameters, report_context)

            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif tool_name == "get_simulation_context":
                # Redirigir a insight_forge, ya que es más potente
                logger.info("get_simulation_context redirigido a insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)

            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)

            else:
                return f"Herramienta desconocida: {tool_name}. Por favor usa una de las siguientes: insight_forge, panorama_search, quick_search"

        except Exception as e:
            logger.error(f"Ejecución de herramienta fallida: {tool_name}, error: {str(e)}")
            return f"Ejecución de herramienta fallida: {str(e)}"
    
    # Conjunto de nombres de herramientas válidas, usado para validar el fallback de JSON desnudo
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        Analiza llamadas a herramientas en la respuesta del LLM

        Formatos soportados (por prioridad):
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. JSON desnudo (toda la respuesta o una línea es un JSON de llamada a herramienta)
        """
        tool_calls = []

        # Formato 1: estilo XML (formato estándar)
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # Formato 2: fallback - el LLM emite JSON desnudo directamente (sin etiqueta <tool_call>)
        # Solo se intenta cuando el formato 1 no coincide, para evitar falsos positivos en el cuerpo
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # La respuesta puede contener texto de razonamiento + JSON desnudo; intenta extraer el último objeto JSON
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """Valida si el JSON extraído es una llamada a herramienta válida"""
        # Soporta tanto {"name": ..., "parameters": ...} como {"tool": ..., "params": ...}
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # Normalizar claves a name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """Genera el texto de descripción de herramientas"""
        desc_parts = ["Herramientas disponibles:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Parámetros: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        Planifica el esquema del informe
        
        Usa el LLM para analizar los requisitos de simulación y planificar la estructura del índice del informe
        
        Args:
            progress_callback: Función de callback de progreso
            
        Returns:
            ReportOutline: Esquema del informe
        """
        logger.info("Iniciando planificación del esquema del informe...")
        
        if progress_callback:
            progress_callback("planning", 0, "Analizando requisitos de simulación...")
        
        # Primero obtener el contexto de simulación
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "Generando esquema del informe...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "Analizando estructura del esquema...")
            
            # Analizar el esquema
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Informe de análisis de simulación"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "Planificación del esquema completada")
            
            logger.info(f"Esquema planificado: {len(sections)} secciones")
            return outline
            
        except Exception as e:
            logger.error(f"Planificación del esquema fallida: {str(e)}")
            # Retornar esquema por defecto (3 secciones, como fallback)
            return ReportOutline(
                title="Informe de Predicción Futura",
                summary="Tendencias y riesgos futuros basados en predicción de simulación",
                sections=[
                    ReportSection(title="Escenario de predicción y hallazgos clave"),
                    ReportSection(title="Análisis de comportamiento grupal predicho"),
                    ReportSection(title="Perspectivas futuras y advertencias de riesgo")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        Genera el contenido de una sección individual con el modo ReACT
        
        Ciclo ReACT:
        1. Thought (razonamiento) - analiza qué información se necesita
        2. Action (acción) - llama a herramientas para obtener información
        3. Observation (observación) - analiza los resultados de la herramienta
        4. Repite hasta que la información sea suficiente o se alcance el máximo
        5. Final Answer (respuesta final) - genera el contenido de la sección
        
        Args:
            section: Sección a generar
            outline: Esquema completo
            previous_sections: Contenido de secciones anteriores (para mantener coherencia)
            progress_callback: Callback de progreso
            section_index: Índice de la sección (para logs)
            
        Returns:
            Contenido de la sección (formato Markdown)
        """
        logger.info(f"Generando sección con ReACT: {section.title}")
        
        # Registrar inicio de sección
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )

        # Construir prompt de usuario - cada sección completada pasa máximo 4000 caracteres
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # Cada sección tiene máximo 4000 caracteres
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(Esta es la primera sección)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # Bucle ReACT
        tool_calls_count = 0
        max_iterations = 5  # Número máximo de rondas de iteración
        min_tool_calls = 3  # Número mínimo de llamadas a herramientas
        conflict_retries = 0  # Número de conflictos consecutivos entre llamada a herramienta y Final Answer
        used_tools = set()  # Registro de herramientas ya usadas
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # Contexto del informe, usado por InsightForge para generar subpreguntas
        report_context = f"Título de sección: {section.title}\nRequisito de simulación: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"Recuperando y redactando en profundidad ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # Llamar al LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # Verificar si el LLM retornó None (error de API o contenido vacío)
            if response is None:
                logger.warning(f"Sección {section.title}, iteración {iteration + 1}: LLM retornó None")
                # Si quedan iteraciones, agregar mensaje y reintentar
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(respuesta vacía)"})
                    messages.append({"role": "user", "content": "Por favor continúa generando contenido."})
                    continue
                # La última iteración también retornó None; salir del bucle y forzar el cierre
                break

            logger.debug(f"Respuesta del LLM: {response[:200]}...")

            # Analizar una vez y reutilizar el resultado
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── Manejo de conflicto: el LLM emitió a la vez una llamada a herramienta y un Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"Sección {section.title}, ronda {iteration+1}: "
                    f"el LLM emitió a la vez llamada a herramienta y Final Answer (conflicto #{conflict_retries})"
                )

                if conflict_retries <= 2:
                    # Primeras dos veces: descartar respuesta y pedir al LLM que vuelva a responder
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【Error de formato】En una sola respuesta incluyste tanto una llamada a herramienta como un Final Answer, lo cual no está permitido.\n"
                            "Cada respuesta solo puede hacer una de las dos cosas:\n"
                            "- Llamar a una herramienta (emite un bloque <tool_call>, no escribas Final Answer)\n"
                            "- Emitir el contenido final (comienza con 'Final Answer:', no incluyas <tool_call>)\n"
                            "Por favor responde de nuevo haciendo solo una de las dos cosas."
                        ),
                    })
                    continue
                else:
                    # Tercera vez: degradar, truncar al primer tool call y ejecutar forzado
                    logger.warning(
                        f"Sección {section.title}: {conflict_retries} conflictos consecutivos; "
                        "degradando a ejecución truncada del primer tool call"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # Registrar respuesta del LLM
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── Caso 1: el LLM emitió un Final Answer ──
            if has_final_answer:
                # Llamadas insuficientes a herramientas; rechazar y pedir más
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"(Herramientas aún no usadas: {', '.join(unused_tools)}) — se recomienda usarlas" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # Finalización normal
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"Sección {section.title} generada (llamadas a herramientas: {tool_calls_count})")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── Caso 2: el LLM intenta llamar a una herramienta ──
            if has_tool_calls:
                # Cuota de herramientas agotada → notificar claramente y pedir Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # Solo ejecutar el primer tool call
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"El LLM intentó llamar {len(tool_calls)} herramientas; solo se ejecuta la primera: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # Construir sugerencia de herramientas no usadas
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── Caso 3: sin llamada a herramienta ni Final Answer ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # Llamadas insuficientes; recomendar herramientas no usadas
                unused_tools = all_tools - used_tools
                unused_hint = f"(Herramientas aún no usadas: {', '.join(unused_tools)}) — se recomienda usarlas" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # Las llamadas a herramientas son suficientes y el LLM emitió contenido sin prefijo "Final Answer:"
            # Adoptar directamente como respuesta final, sin más iteraciones
            logger.info(f"Sección {section.title}: sin prefijo 'Final Answer:' detectado; se adopta la salida del LLM como contenido final (llamadas: {tool_calls_count})")
            final_answer = response.strip()

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # Se alcanzó el máximo de iteraciones; forzar generación de contenido
        logger.warning(f"Sección {section.title} alcanzó el máximo de iteraciones; generación forzada")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # Verificar si el LLM retornó None al forzar el cierre
        if response is None:
            logger.error(f"Sección {section.title}: LLM retornó None al forzar el cierre; usando mensaje de error por defecto")
            final_answer = "(Esta sección no pudo generarse: el LLM retornó respuesta vacía. Por favor intente de nuevo más tarde.)"
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # Registrar finalización de generación del contenido de la sección
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        Genera el informe completo (salida sección por sección en tiempo real)
        
        Cada sección se guarda inmediatamente al completarse, sin esperar a que termine todo el informe.
        Estructura de archivos:
        reports/{report_id}/
            meta.json       - Metainformación del informe
            outline.json    - Esquema del informe
            progress.json   - Progreso de generación
            section_01.md   - Sección 1
            section_02.md   - Sección 2
            ...
            full_report.md  - Informe completo
        
        Args:
            progress_callback: Función de callback de progreso (stage, progress, message)
            report_id: ID del informe (opcional; se autogenera si no se proporciona)
            
        Returns:
            Report: Informe completo
        """
        import uuid
        
        # Si no se proporcionó report_id, autogenerar
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # Lista de títulos de secciones completadas (para seguimiento de progreso)
        completed_section_titles = []
        
        try:
            # Inicializar: crear carpeta del informe y guardar estado inicial
            ReportManager._ensure_report_folder(report_id)
            
            # Inicializar registrador de log (log estructurado agent_log.jsonl)
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # Inicializar registrador de log de consola (console_log.txt)
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "Inicializando informe...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # Fase 1: planificación del esquema
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "Iniciando planificación del esquema del informe...",
                completed_sections=[]
            )
            
            # Registrar inicio de planificación
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "Iniciando planificación del esquema del informe...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # Registrar finalización de planificación
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # Guardar esquema en archivo
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"Esquema planificado: {len(outline.sections)} secciones",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"Esquema guardado en archivo: {report_id}/outline.json")
            
            # Fase 2: generación sección por sección (guardado por sección)
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # Guardar contenido para contexto
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # Actualizar progreso
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"Generando sección: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"Generando sección: {section.title} ({section_num}/{total_sections})"
                    )
                
                # Generar contenido principal de la sección
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # Guardar sección
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # Registrar finalización de la sección
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(f"Sección guardada: {report_id}/section_{section_num:02d}.md")
                
                # Actualizar progreso
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"Sección {section.title} completada",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # Fase 3: ensamblar el informe completo
            if progress_callback:
                progress_callback("generating", 95, "Ensamblando informe completo...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "Ensamblando informe completo...",
                completed_sections=completed_section_titles
            )
            
            # Usar ReportManager para ensamblar el informe completo
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # Calcular tiempo total
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # Registrar finalización del informe
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # Guardar informe final
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "Informe generado correctamente",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "Informe generado correctamente")
            
            logger.info(f"Informe generado correctamente: {report_id}")
            
            # Cerrar el registrador de log de consola
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"Generación del informe fallida: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # Registrar error
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # Guardar estado de fallo
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"Generación del informe fallida: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # Ignorar errores de guardado
            
            # Cerrar el registrador de log de consola
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Conversa con el Report Agent
        
        En el chat, el Agent puede llamar autónomamente a herramientas de recuperación para responder preguntas
        
        Args:
            message: Mensaje del usuario
            chat_history: Historial de conversación
            
        Returns:
            {
                "response": "Respuesta del Agent",
                "tool_calls": [lista de herramientas llamadas],
                "sources": [fuentes de información]
            }
        """
        logger.info(f"Conversación con Report Agent: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # Obtener el contenido del informe ya generado
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # Limitar longitud del informe para evitar contexto excesivo
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [contenido del informe truncado] ..."
        except Exception as e:
            logger.warning(f"Error al obtener contenido del informe: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(sin informe disponible)",
            tools_description=self._get_tools_description(),
        )

        # Construir mensajes
        messages = [{"role": "system", "content": system_prompt}]
        
        # Agregar historial de conversación
        for h in chat_history[-10:]:  # Limitar longitud del historial
            messages.append(h)
        
        # Agregar mensaje del usuario
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # Bucle ReACT (versión simplificada)
        tool_calls_made = []
        max_iterations = 2  # Reducir número de rondas de iteración
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # Analizar llamadas a herramientas
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # Sin llamadas a herramientas; retornar respuesta directamente
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Ejecutar llamadas a herramientas (limitar cantidad)
            tool_results = []
            for call in tool_calls[:1]:  # Ejecutar máximo 1 llamada por ronda
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # Limitar longitud del resultado
                })
                tool_calls_made.append(call)
            
            # Agregar resultados a los mensajes
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[resultado de {r['tool']}]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # Se alcanzó el máximo de iteraciones; obtener respuesta final
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Limpiar respuesta
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    Gestor de informes
    
    Responsable del almacenamiento persistente y recuperación de informes
    
    Estructura de archivos (salida por secciones):
    reports/
      {report_id}/
        meta.json          - Metainformación y estado del informe
        outline.json       - Esquema del informe
        progress.json      - Progreso de generación
        section_01.md      - Sección 1
        section_02.md      - Sección 2
        ...
        full_report.md     - Informe completo
    """
    
    # Directorio de almacenamiento de informes
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """Asegurar que el directorio raíz de informes existe"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """Obtiene la ruta de la carpeta del informe"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """Asegurar que la carpeta del informe existe y retornar la ruta"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo de metainformación del informe"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo Markdown del informe completo"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo de esquema"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo de progreso"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Obtiene la ruta del archivo Markdown de la sección"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo de log del Agent"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Obtiene la ruta del archivo de log de consola"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Obtiene el contenido del log de consola
        
        Este es el log de salida de consola durante la generación del informe (INFO, WARNING, etc.),
        diferente del log estructurado de agent_log.jsonl.
        
        Args:
            report_id: ID del informe
            from_line: Línea desde la que comenzar a leer (para obtención incremental; 0 = desde el inicio)
            
        Returns:
            {
                "logs": [lista de líneas de log],
                "total_lines": total de líneas,
                "from_line": número de línea inicial,
                "has_more": si hay más logs disponibles
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # Conservar la línea de log original, eliminar salto de línea final
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Se leyó hasta el final
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        Obtiene el log de consola completo (obtención en una sola vez)
        
        Args:
            report_id: ID del informe
            
        Returns:
            Lista de líneas de log
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Obtiene el contenido del log del Agent
        
        Args:
            report_id: ID del informe
            from_line: Línea desde la que comenzar a leer (para obtención incremental; 0 = desde el inicio)
            
        Returns:
            {
                "logs": [lista de entradas de log],
                "total_lines": total de líneas,
                "from_line": número de línea inicial,
                "has_more": si hay más logs disponibles
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # Omitir líneas que fallaron al parsear
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Se leyó hasta el final
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Obtiene el log del Agent completo (para obtención en una sola vez)
        
        Args:
            report_id: ID del informe
            
        Returns:
            Lista de entradas de log
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        Guarda el esquema del informe
        
        Se llama inmediatamente después de completar la fase de planificación
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"Esquema guardado: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        Guarda una sección individual

        Se llama inmediatamente tras completar cada sección, implementando salida por secciones

        Args:
            report_id: ID del informe
            section_index: Índice de la sección (comienza en 1)
            section: Objeto de la sección

        Returns:
            Ruta del archivo guardado
        """
        cls._ensure_report_folder(report_id)

        # Construir contenido Markdown de la sección - limpiar posibles títulos duplicados
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # Guardar archivo
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(f"Sección guardada: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Limpia el contenido de la sección
        
        1. Elimina líneas de encabezado Markdown al inicio del contenido que dupliquen el título de la sección
        2. Convierte todos los encabezados de nivel ### o inferior en texto en negrita
        
        Args:
            content: Contenido original
            section_title: Título de la sección
            
        Returns:
            Contenido limpio
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Verificar si es una línea de encabezado Markdown
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # Verificar si es un título duplicado del título de la sección (saltar duplicados en las primeras 5 líneas)
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # Convertir todos los niveles de encabezado (#, ##, ###, ####, etc.) en negrita
                # ya que el título de la sección lo agrega el sistema; el contenido no debe tener encabezados
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # Agregar línea vacía
                continue
            
            # Si la línea anterior fue un título omitido y la línea actual está vacía, también omitirla
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # Eliminar líneas vacías al inicio
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # Eliminar separadores al inicio
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # También eliminar líneas vacías después del separador
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        Actualiza el progreso de generación del informe
        
        El frontend puede leer progress.json para obtener el progreso en tiempo real
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el progreso de generación del informe"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Obtiene la lista de secciones ya generadas
        
        Retorna la información de todos los archivos de sección guardados
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Analizar índice de sección desde el nombre del archivo
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        Ensambla el informe completo
        
        Construye el informe completo desde los archivos de sección guardados, con limpieza de encabezados
        """
        folder = cls._get_report_folder(report_id)
        
        # Construir encabezado del informe
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # Leer todos los archivos de sección en orden
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # Postprocesamiento: limpiar problemas de encabezados en todo el informe
        md_content = cls._post_process_report(md_content, outline)
        
        # Guardar informe completo
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Informe completo ensamblado: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        Postprocesa el contenido del informe
        
        1. Elimina encabezados duplicados
        2. Conserva el encabezado principal (#) y los títulos de sección (##); elimina otros niveles (###, ####, etc.)
        3. Limpia líneas vacías y separadores sobrantes
        
        Args:
            content: Contenido original del informe
            outline: Esquema del informe
            
        Returns:
            Contenido procesado
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # Recopilar todos los títulos de sección del esquema
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Verificar si es una línea de encabezado
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # Verificar si es un encabezado duplicado (mismo título dentro de las últimas 5 líneas)
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # Omitir encabezado duplicado y las líneas vacías siguientes
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # Procesamiento de niveles de encabezado:
                # - # (level=1): conservar solo el encabezado principal del informe
                # - ## (level=2): conservar títulos de sección
                # - ### e inferiores (level>=3): convertir en texto en negrita
                
                if level == 1:
                    if title == outline.title:
                        # Conservar encabezado principal del informe
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # Título de sección usando # incorrectamente; corregir a ##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # Otros encabezados de nivel 1 se convierten en negrita
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # Conservar título de sección
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # Encabezados de nivel 2 que no son secciones se convierten en negrita
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # Encabezados de nivel ### e inferiores se convierten en texto en negrita
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # Omitir separadores que van justo después de un encabezado
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # Conservar solo una línea vacía después del encabezado
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Limpiar múltiples líneas vacías consecutivas (conservar máximo 2)
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """Guarda la metainformación del informe y el informe completo"""
        cls._ensure_report_folder(report.report_id)
        
        # Guardar metainformación JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Guardar esquema
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # Guardar informe Markdown completo
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"Informe guardado: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """Obtiene un informe"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # Compatibilidad con formato antiguo: verificar archivos guardados directamente en el directorio reports
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Reconstruir objeto Report
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # Si markdown_content está vacío, intentar leer desde full_report.md
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """Obtiene un informe por ID de simulación"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # Nuevo formato: carpeta
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # Compatibilidad con formato antiguo: archivo JSON
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """Lista los informes"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # Nuevo formato: carpeta
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # Compatibilidad con formato antiguo: archivo JSON
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # Ordenar por tiempo de creación descendente
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """Elimina el informe (carpeta completa)"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # Nuevo formato: eliminar carpeta completa
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"Carpeta del informe eliminada: {report_id}")
            return True
        
        # Compatibilidad con formato antiguo: eliminar archivos individuales
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
