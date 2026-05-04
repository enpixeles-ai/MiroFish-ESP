"""
Generador inteligente de configuración de simulación
Usa el LLM para generar automáticamente parámetros de simulación detallados basándose en los requisitos de simulación, el contenido del documento y la información del grafo
Implementa automatización completa sin necesidad de configuración manual de parámetros

Adopta una estrategia de generación por pasos para evitar fallas por contenido demasiado extenso generado de una sola vez:
1. Generar configuración de tiempo
2. Generar configuración de eventos
3. Generar configuración de Agentes por lotes
4. Generar configuración de plataforma
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.simulation_config')

# Perfil horario por defecto para audiencia latina/hispana en USA o LatAm.
LATAM_US_TIMEZONE_CONFIG = {
    # Franja de madrugada (casi sin actividad)
    "dead_hours": [1, 2, 3, 4, 5],
    # Franja matutina (actividad gradual)
    "morning_hours": [6, 7, 8, 9],
    # Franja laboral
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17],
    # Pico nocturno (máxima actividad)
    "peak_hours": [18, 19, 20, 21, 22],
    # Franja nocturna tardía (actividad decreciente)
    "night_hours": [23],
    # Coeficientes de actividad
    "activity_multipliers": {
        "dead": 0.05,      # Madrugada: casi nadie activo
        "morning": 0.45,   # Mañana: actividad gradual
        "work": 0.7,       # Franja laboral: actividad media
        "peak": 1.5,       # Pico nocturno
        "night": 0.5       # Noche tardía: actividad en descenso
    }
}


@dataclass
class AgentActivityConfig:
    """Configuración de actividad de un solo Agente"""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # Configuración de nivel de actividad (0.0-1.0)
    activity_level: float = 0.5  # Nivel de actividad general
    
    # Frecuencia de publicación (publicaciones esperadas por hora)
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # Franjas horarias activas (formato 24 h, 0-23)
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # Velocidad de respuesta (demora de reacción ante eventos de tendencia, en minutos de simulación)
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # Tendencia emocional (-1.0 a 1.0, negativa a positiva)
    sentiment_bias: float = 0.0
    
    # Postura (actitud ante un tema específico)
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # Peso de influencia (determina la probabilidad de que otros Agentes vean sus publicaciones)
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """Configuración de simulación de tiempo para audiencia latina/hispana."""
    # Duración total de la simulación (en horas simuladas)
    total_simulation_hours: int = 72  # Simulación predeterminada de 72 horas (3 días)
    
    # Tiempo representado por cada ronda (en minutos simulados) — predeterminado: 60 min (1 hora), para acelerar el flujo de tiempo
    minutes_per_round: int = 60
    
    # Rango de cantidad de Agentes activados por hora
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # Horario pico nocturno habitual en redes sociales
    peak_hours: List[int] = field(default_factory=lambda: [18, 19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # Horario valle (madrugada 1-5 h, casi sin actividad)
    off_peak_hours: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05  # Madrugada: actividad extremadamente baja
    
    # Franja matutina
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8, 9])
    morning_activity_multiplier: float = 0.45
    
    # Franja laboral
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """Configuración de eventos"""
    # Evento inicial (evento disparador al comienzo de la simulación)
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # Eventos programados (disparados en momentos específicos)
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Palabras clave de temas de tendencia
    hot_topics: List[str] = field(default_factory=list)
    
    # Dirección de orientación de la opinión pública
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """Configuración específica de plataforma"""
    platform: str  # twitter or reddit
    
    # Pesos del algoritmo de recomendación
    recency_weight: float = 0.4  # Frescura temporal
    popularity_weight: float = 0.3  # Popularidad
    relevance_weight: float = 0.3  # Relevancia
    
    # Umbral de propagación viral (número de interacciones necesarias para activar la difusión)
    viral_threshold: int = 10
    
    # Intensidad del efecto cámara de eco (grado de agrupamiento de opiniones similares)
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """Configuración completa de parámetros de simulación"""
    # Información básica
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # Configuración de tiempo
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # Lista de configuraciones de Agentes
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # Configuración de eventos
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # Configuración de plataforma
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None
    
    # Configuración del LLM
    llm_model: str = ""
    llm_base_url: str = ""
    
    # Metadatos de generación
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""  # Explicación del razonamiento del LLM
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertir a diccionario"""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Convertir a cadena JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    Generador inteligente de configuración de simulación
    
    Usa el LLM para analizar los requisitos de simulación, el contenido del documento y la información de entidades del grafo,
    generando automáticamente la mejor configuración de parámetros de simulación
    
    Adopta una estrategia de generación por pasos:
    1. Generar configuración de tiempo y configuración de eventos (ligero)
    2. Generar configuración de Agentes por lotes (de 10-20 por lote)
    3. Generar configuración de plataforma
    """
    
    # Número máximo de caracteres del contexto
    MAX_CONTEXT_LENGTH = 50000
    # Número de Agentes generados por lote
    AGENTS_PER_BATCH = 15
    
    # Longitud de truncado de contexto para cada paso (en caracteres)
    TIME_CONFIG_CONTEXT_LENGTH = 10000   # Configuración de tiempo
    EVENT_CONFIG_CONTEXT_LENGTH = 8000   # Configuración de eventos
    ENTITY_SUMMARY_LENGTH = 300          # Resumen de entidad
    AGENT_SUMMARY_LENGTH = 300           # Resumen de entidad en la configuración del Agente
    ENTITIES_PER_TYPE_DISPLAY = 20       # Cantidad de entidades mostradas por tipo
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY no está configurada")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        Genera inteligentemente la configuración completa de simulación (generación por pasos)
        
        Args:
            simulation_id: ID de la simulación
            project_id: ID del proyecto
            graph_id: ID del grafo
            simulation_requirement: Descripción del requisito de simulación
            document_text: Contenido del documento original
            entities: Lista de entidades filtradas
            enable_twitter: Si se habilita Twitter
            enable_reddit: Si se habilita Reddit
            progress_callback: Función de callback de progreso (current_step, total_steps, message)
            
        Returns:
            SimulationParameters: Parámetros completos de simulación
        """
        logger.info(f"Iniciando generación inteligente de configuración de simulación: simulation_id={simulation_id}, entidades={len(entities)}")
        
        # Calcular el número total de pasos
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # Configuración de tiempo + configuración de eventos + N lotes de Agentes + configuración de plataforma
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. Construir información de contexto base
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== Paso 1: Generar configuración de tiempo ==========
        report_progress(1, "Generando configuración de tiempo...")
        num_entities = len(entities)
        time_config_result = self._generate_time_config(context, num_entities)
        time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"Configuración de tiempo: {time_config_result.get('reasoning', 'exitoso')}")
        
        # ========== Paso 2: Generar configuración de eventos ==========
        report_progress(2, "Generando configuración de eventos y temas de tendencia...")
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"Configuración de eventos: {event_config_result.get('reasoning', 'exitoso')}")
        
        # ========== Pasos 3-N: Generar configuración de Agentes por lotes ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                f"Generando configuración de Agentes ({start_idx + 1}-{end_idx}/{len(entities)})..."
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(f"Configuración de Agentes: {len(all_agent_configs)} generados exitosamente")
        
        # ========== Asignar Agente publicador para las publicaciones iniciales ==========
        logger.info("Asignando el Agente publicador adecuado para las publicaciones iniciales...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(f"Asignación de publicaciones iniciales: {assigned_count} publicaciones con publicador asignado")
        
        # ========== Último paso: Generar configuración de plataforma ==========
        report_progress(total_steps, "Generando configuración de plataforma...")
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # Construir los parámetros finales
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"Generación de configuración de simulación completada: {len(params.agent_configs)} configuraciones de Agentes")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode]
    ) -> str:
        """Construir contexto del LLM, truncado a la longitud máxima"""
        
        # Resumen de entidades
        entity_summary = self._summarize_entities(entities)
        
        # Construir contexto
        context_parts = [
            f"## Requisito de simulación\n{simulation_requirement}",
            f"\n## Información de entidades ({len(entities)} en total)\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # Se reservan 500 caracteres de margen
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(documento truncado)"
            context_parts.append(f"\n## Contenido del documento original\n{doc_text}")
        
        return "\n".join(context_parts)
    
    def _summarize_entities(self, entities: List[EntityNode]) -> str:
        """Generar resumen de entidades"""
        lines = []
        
        # Agrupar por tipo
        by_type: Dict[str, List[EntityNode]] = {}
        for e in entities:
            t = e.get_entity_type() or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)} en total)")
            # Usar la cantidad de visualización y la longitud de resumen configuradas
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary_preview = (e.summary[:summary_len] + "...") if len(e.summary) > summary_len else e.summary
                lines.append(f"- {e.name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... y {len(type_entities) - display_count} más")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Llamada al LLM con reintentos, incluye lógica de corrección de JSON"""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # Reducir temperatura en cada reintento
                    # No se establece max_tokens para permitir que el LLM responda libremente
                )
                
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                # Verificar si fue truncado
                if finish_reason == 'length':
                    logger.warning(f"Salida del LLM truncada (intento {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # Intentar parsear JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"Fallo al parsear JSON (intento {attempt+1}): {str(e)[:80]}")
                    
                    # Intentar corregir el JSON
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"Llamada al LLM fallida (intento {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("La llamada al LLM ha fallado")
    
    def _fix_truncated_json(self, content: str) -> str:
        """Corregir JSON truncado"""
        content = content.strip()
        
        # Calcular llaves/corchetes sin cerrar
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Verificar si hay cadenas sin cerrar
        if content and content[-1] not in '",}]':
            content += '"'
        
        # Cerrar corchetes y llaves
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Intentar corregir el JSON de configuración"""
        import re
        
        # Corregir el caso de truncado
        content = self._fix_truncated_json(content)
        
        # Extraer la parte JSON
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # Eliminar saltos de línea dentro de cadenas
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # Intentar eliminar todos los caracteres de control
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """Generar configuración de tiempo"""
        # Usar la longitud de truncado de contexto configurada
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # Calcular el valor máximo permitido (90% del número de agentes)
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""Basándote en los siguientes requisitos de simulación, genera la configuración de tiempo para la simulación.

{context_truncated}

## Tarea
Genera el JSON de configuración de tiempo.

### Principios básicos (solo de referencia; ajusta según el evento concreto y el grupo participante):
- El grupo de usuarios por defecto es latino/hispano en USA o LatAm; usa la zona horaria local inferida del escenario
- De 1 a 5 h (madrugada): casi sin actividad (coeficiente 0.05)
- De 6 a 9 h (mañana): actividad gradualmente creciente (coeficiente 0.45)
- De 9 a 17 h (jornada laboral): actividad media (coeficiente 0.7)
- De 18 a 22 h (noche): horario pico (coeficiente 1.5)
- Después de las 23 h: actividad decreciente (coeficiente 0.5)
- Patrón general: madrugada baja, mañana creciente, jornada laboral media, noche en pico
- **Importante**: los valores de ejemplo a continuación son solo de referencia; debes ajustar las franjas horarias según la naturaleza del evento y las características del grupo participante
  - Ejemplo: el pico para el grupo estudiantil puede ser de 21 a 23 h; los medios son activos todo el día; los organismos oficiales solo están activos en horario laboral
  - Ejemplo: un tema viral repentino puede generar discusión incluso de madrugada; off_peak_hours puede acortarse en consecuencia

### Devuelve el formato JSON (sin markdown)

Ejemplo:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [18, 19, 20, 21, 22],
    "off_peak_hours": [1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8, 9],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17],
    "reasoning": "Explicación de la configuración de tiempo para este evento"
}}

Descripción de campos:
- total_simulation_hours (int): duración total de la simulación, de 24 a 168 horas; eventos repentinos son cortos, temas sostenidos son largos
- minutes_per_round (int): duración de cada ronda, de 30 a 120 minutos; se recomienda 60 minutos
- agents_per_hour_min (int): mínimo de Agentes activados por hora (rango: 1-{max_agents_allowed})
- agents_per_hour_max (int): máximo de Agentes activados por hora (rango: 1-{max_agents_allowed})
- peak_hours (array int): franja horaria pico; ajustar según el grupo participante del evento
- off_peak_hours (array int): franja horaria valle; generalmente de madrugada
- morning_hours (array int): franja matutina
- work_hours (array int): franja laboral
- reasoning (string): breve explicación de por qué se eligió esta configuración"""

        system_prompt = "Responde razonamientos en español latino. Devuelve JSON puro. Conserva nombres de claves JSON y valores enum exactamente como se solicitan. La configuración de tiempo debe ajustarse a audiencias latinas/hispanas en USA o LatAm según el escenario."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"Fallo al generar configuración de tiempo con el LLM: {e}, usando configuración predeterminada")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """Obtener la configuración de tiempo predeterminada para audiencia latina/hispana."""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # 1 hora por ronda para acelerar el flujo de tiempo
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [18, 19, 20, 21, 22],
            "off_peak_hours": [1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8, 9],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17],
            "reasoning": "Usando configuración horaria predeterminada para audiencia latina/hispana (1 hora por ronda)"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """Parsear el resultado de configuración de tiempo y verificar que agents_per_hour no supere el total de agentes"""
        # Obtener valores originales
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # Validar y corregir: asegurarse de no superar el total de agentes
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) supera el total de Agentes ({num_entities}); corregido")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) supera el total de Agentes ({num_entities}); corregido")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # Asegurarse de que min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max; corregido a {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # Predeterminado: 1 hora por ronda
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # Madrugada: casi nadie activo
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[EntityNode]
    ) -> Dict[str, Any]:
        """Generar configuración de eventos"""
        
        # Obtener la lista de tipos de entidad disponibles para referencia del LLM
        entity_types_available = list(set(
            e.get_entity_type() or "Unknown" for e in entities
        ))
        
        # Listar nombres de entidades representativas por tipo
        type_examples = {}
        for e in entities:
            etype = e.get_entity_type() or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(e.name)
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # Usar la longitud de truncado de contexto configurada
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""Basándote en los siguientes requisitos de simulación, genera la configuración de eventos.

Requisito de simulación: {simulation_requirement}

{context_truncated}

## Tipos de entidad disponibles y ejemplos
{type_info}

## Tarea
Genera el JSON de configuración de eventos:
- Extrae palabras clave de temas de tendencia
- Describe la dirección de evolución de la opinión pública
- Diseña el contenido de las publicaciones iniciales; **cada publicación debe especificar poster_type (tipo de publicador)**

**Importante**: poster_type debe elegirse de los "tipos de entidad disponibles" de arriba, para que las publicaciones iniciales puedan asignarse al Agente adecuado para publicarlas.
Por ejemplo: los anuncios oficiales deben ser publicados por el tipo Official/University; las noticias por MediaOutlet; las opiniones estudiantiles por Student.

Devuelve el formato JSON (sin markdown):
{{
    "hot_topics": ["palabra_clave_1", "palabra_clave_2", ...],
    "narrative_direction": "<descripción de la dirección de evolución de la opinión pública>",
    "initial_posts": [
        {{"content": "contenido de la publicación", "poster_type": "tipo de entidad (debe elegirse de los tipos disponibles)"}},
        ...
    ],
    "reasoning": "<breve explicación>"
}}"""

        system_prompt = "Responde contenido narrativo en español latino. Devuelve formato JSON puro. Conserva nombres de claves JSON y poster_type exactamente como aparecen en los tipos de entidad disponibles."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"Fallo al generar configuración de eventos con el LLM: {e}, usando configuración predeterminada")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "Usando configuración predeterminada"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """Parsear el resultado de configuración de eventos"""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        Asignar el Agente publicador adecuado para las publicaciones iniciales
        
        Emparejar el agent_id más adecuado según el poster_type de cada publicación
        """
        if not event_config.initial_posts:
            return event_config
        
        # Construir índice de agentes por tipo de entidad
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # Tabla de aliases de tipo (para manejar diferentes formatos que puede generar el LLM)
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media"],
            "student": ["student", "person"],
            "professor": ["professor", "expert", "teacher"],
            "alumni": ["alumni", "person"],
            "organization": ["organization", "ngo", "company", "group"],
            "person": ["person", "student", "alumni"],
        }
        
        # Registrar el índice de agente usado por tipo para evitar repetir el mismo agente
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # Intentar encontrar un agente que coincida
            matched_agent_id = None
            
            # 1. Coincidencia directa
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 2. Coincidencia usando aliases
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            # 3. Si aún no se encontró ninguno, usar el agente con mayor influencia
            if matched_agent_id is None:
                logger.warning(f"No se encontró un Agente del tipo '{poster_type}'; se usará el Agente con mayor influencia")
                if agent_configs:
                    # Ordenar por influencia y seleccionar el de mayor influencia
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            logger.info(f"Asignación de publicación inicial: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[EntityNode],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        """Generar configuración de Agentes por lotes"""
        
        # Construir información de entidades (usando la longitud de resumen configurada)
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": e.name,
                "entity_type": e.get_entity_type() or "Unknown",
                "summary": e.summary[:summary_len] if e.summary else ""
            })
        
        prompt = f"""Basándote en la siguiente información, genera la configuración de actividad en redes sociales para cada entidad.

Requisito de simulación: {simulation_requirement}

## Lista de entidades
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## Tarea
Genera la configuración de actividad para cada entidad. Considera:
- **Horarios acordes a audiencias latinas/hispanas**: de 1 a 5 h (madrugada) casi sin actividad; de 18 a 22 h (noche) máxima actividad
- **Organismos oficiales** (University/GovernmentAgency): actividad baja (0.1-0.3), activos en horario laboral (9-17), respuesta lenta (60-240 min), influencia alta (2.5-3.0)
- **Medios de comunicación** (MediaOutlet): actividad media (0.4-0.6), activos todo el día (8-23), respuesta rápida (5-30 min), influencia alta (2.0-2.5)
- **Personas** (Student/Person/Alumni): actividad alta (0.6-0.9), principalmente activos de noche (18-23), respuesta rápida (1-15 min), influencia baja (0.8-1.2)
- **Figuras públicas/Expertos**: actividad media (0.4-0.6), influencia media-alta (1.5-2.0)

Devuelve el formato JSON (sin markdown):
{{
    "agent_configs": [
        {{
            "agent_id": <debe coincidir con la entrada>,
            "activity_level": <0.0-1.0>,
            "posts_per_hour": <frecuencia de publicaciones>,
            "comments_per_hour": <frecuencia de comentarios>,
            "active_hours": [<lista de horas activas, considerando el mercado y zona horaria del escenario>],
            "response_delay_min": <demora mínima de respuesta en minutos>,
            "response_delay_max": <demora máxima de respuesta en minutos>,
            "sentiment_bias": <-1.0 a 1.0>,
            "stance": "<supportive/opposing/neutral/observer>",
            "influence_weight": <peso de influencia>
        }},
        ...
    ]
}}"""

        system_prompt = "Responde razonamientos en español latino. Devuelve JSON puro. Conserva nombres de claves JSON y valores enum exactamente como se solicitan. La configuración debe ajustarse a los hábitos del mercado objetivo."
        
        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            logger.warning(f"Fallo al generar lote de configuraciones de Agentes con el LLM: {e}, usando generación por reglas")
            llm_configs = {}
        
        # Construir objetos AgentActivityConfig
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            # Si el LLM no generó configuración, usar generación por reglas
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=entity.uuid,
                entity_name=entity.name,
                entity_type=entity.get_entity_type() or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: EntityNode) -> Dict[str, Any]:
        """Generar configuración de un solo Agente basada en reglas para audiencia latina/hispana."""
        entity_type = (entity.get_entity_type() or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # Organismos oficiales: activos en horario laboral, baja frecuencia, alta influencia
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # Medios de comunicación: activos todo el día, frecuencia media, alta influencia
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # Expertos/Profesores: activos en jornada laboral y noche, frecuencia media
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # Estudiantes: principalmente de noche, alta frecuencia
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Mañana + noche
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # Ex alumnos: principalmente de noche
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # Descanso del mediodía + noche
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # Personas comunes: pico nocturno
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Día + noche
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    
