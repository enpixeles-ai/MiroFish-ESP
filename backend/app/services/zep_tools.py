"""
Servicio de herramientas Zep
Encapsula búsqueda en grafo, lectura de nodos, consulta de aristas y otras herramientas para uso del Report Agent

Herramientas de recuperación principales (optimizadas):
1. InsightForge (recuperación de insights profundos) - La recuperación híbrida más potente, genera subpreguntas automáticamente y realiza búsqueda multidimensional
2. PanoramaSearch (búsqueda amplia) - Obtiene la visión completa, incluido contenido expirado
3. QuickSearch (búsqueda simple) - Recuperación rápida
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """Resultado de búsqueda"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """Convierte al formato de texto para comprensión del LLM"""
        text_parts = [f"Consulta de búsqueda: {self.query}", f"Se encontraron {self.total_count} registros relevantes"]
        
        if self.facts:
            text_parts.append("\n### Hechos relevantes:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """Información del nodo"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """Convierte al formato de texto"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "tipo desconocido")
        return f"Entidad: {self.name} (tipo: {entity_type})\nResumen: {self.summary}"


@dataclass
class EdgeInfo:
    """Información de arista"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # Información temporal
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """Convierte al formato de texto"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Relación: {source} --[{self.name}]--> {target}\nHecho: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "desconocido"
            invalid_at = self.invalid_at or "hasta hoy"
            base_text += f"\nVigencia: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (expirado: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """Si ya ha expirado"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """Si ya ha caducado"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    Resultado de recuperación de insights profundos (InsightForge)
    Contiene resultados de recuperación de múltiples subpreguntas y análisis integral
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # Resultados de recuperación por dimensión
    semantic_facts: List[str] = field(default_factory=list)  # Resultados de búsqueda semántica
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # Insights de entidades
    relationship_chains: List[str] = field(default_factory=list)  # Cadenas de relaciones
    
    # Información estadística
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """Convierte al formato de texto detallado para comprensión del LLM"""
        text_parts = [
            f"## Análisis profundo de predicción futura",
            f"Pregunta de análisis: {self.query}",
            f"Escenario de predicción: {self.simulation_requirement}",
            f"\n### Estadísticas de datos de predicción",
            f"- Hechos de predicción relevantes: {self.total_facts}",
            f"- Entidades involucradas: {self.total_entities}",
            f"- Cadenas de relaciones: {self.total_relationships}"
        ]
        
        # Subpreguntas
        if self.sub_queries:
            text_parts.append(f"\n### Subpreguntas analizadas")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # Resultados de búsqueda semántica
        if self.semantic_facts:
            text_parts.append(f"\n### [HECHOS CLAVE] (cite estos textos originales en el informe)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Insights de entidades
        if self.entity_insights:
            text_parts.append(f"\n### [ENTIDADES PRINCIPALES]")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', 'desconocido')}** ({entity.get('type', 'entidad')})")
                if entity.get('summary'):
                    text_parts.append(f"  Resumen: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  Hechos relacionados: {len(entity.get('related_facts', []))}")
        
        # Cadenas de relaciones
        if self.relationship_chains:
            text_parts.append(f"\n### [CADENAS DE RELACIONES]")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    Resultado de búsqueda amplia (Panorama)
    Contiene toda la información relevante, incluido contenido expirado
    """
    query: str
    
    # Todos los nodos
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # Todas las aristas (incluidas las expiradas)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # Hechos actualmente válidos
    active_facts: List[str] = field(default_factory=list)
    # Hechos expirados/caducados (registro histórico)
    historical_facts: List[str] = field(default_factory=list)
    
    # Estadísticas
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """Convierte al formato de texto (versión completa, sin truncar)"""
        text_parts = [
            f"## Resultados de búsqueda amplia (vista panorámica del futuro)",
            f"Consulta: {self.query}",
            f"\n### Información estadística",
            f"- Total de nodos: {self.total_nodes}",
            f"- Total de aristas: {self.total_edges}",
            f"- Hechos actualmente válidos: {self.active_count}",
            f"- Hechos históricos/expirados: {self.historical_count}"
        ]
        
        # Hechos actualmente válidos (salida completa, sin truncar)
        if self.active_facts:
            text_parts.append(f"\n### [HECHOS ACTUALMENTE VÁLIDOS] (texto original del resultado de simulación)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Hechos históricos/expirados (salida completa, sin truncar)
        if self.historical_facts:
            text_parts.append(f"\n### [HECHOS HISTÓRICOS/EXPIRADOS] (registro del proceso de evolución)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Entidades clave (salida completa, sin truncar)
        if self.all_nodes:
            text_parts.append(f"\n### [ENTIDADES INVOLUCRADAS]")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "entidad")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """Resultado de entrevista de un solo Agent"""
    agent_name: str
    agent_role: str  # Tipo de rol (p. ej.: estudiante, docente, medios, etc.)
    agent_bio: str  # Biografía
    question: str  # Pregunta de entrevista
    response: str  # Respuesta a la entrevista
    key_quotes: List[str] = field(default_factory=list)  # Citas clave
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # Mostrar el agent_bio completo, sin truncar
        text += f"_Biografía: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**Citas clave:**\n"
            for quote in self.key_quotes:
                # Limpiar varios tipos de comillas
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # Eliminar puntuación al inicio
                while clean_quote and clean_quote[0] in '，,；;：:、。！？\n\r\t ':
                    clean_quote = clean_quote[1:]
                # Filtrar contenido basura que incluya números de pregunta (问题1-9) — se mantiene el regex como está
                skip = False
                for d in '123456789':
                    if f'\u95ee\u9898{d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # Truncar contenido demasiado largo (por punto, no truncado duro)
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('\u3002', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    Resultado de entrevista (Interview)
    Contiene respuestas de entrevista de múltiples Agents simulados
    """
    interview_topic: str  # Tema de la entrevista
    interview_questions: List[str]  # Lista de preguntas de entrevista
    
    # Agents seleccionados para la entrevista
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # Respuestas de entrevista de cada Agent
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # Razón para seleccionar los Agents
    selection_reasoning: str = ""
    # Resumen de entrevista integrado
    summary: str = ""
    
    # Estadísticas
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """Convierte al formato de texto detallado para comprensión del LLM y citación en el informe"""
        text_parts = [
            "## Informe de entrevista en profundidad",
            f"**Tema de entrevista:** {self.interview_topic}",
            f"**Agentes entrevistados:** {self.interviewed_count} / {self.total_agents} Agents simulados",
            "\n### Razón de selección de entrevistados",
            self.selection_reasoning or "(selección automática)",
            "\n---",
            "\n### Registro de entrevistas",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### Entrevista #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("(sin registro de entrevistas)\n\n---")

        text_parts.append("\n### Resumen de entrevista y puntos de vista principales")
        text_parts.append(self.summary or "(sin resumen)")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Servicio de herramientas Zep
    
    [Herramientas de recuperación principales - optimizadas]
    1. insight_forge - recuperación de insights profundos (la más potente, genera subpreguntas automáticas, búsqueda multidimensional)
    2. panorama_search - búsqueda amplia (obtiene la visión completa, incluido contenido expirado)
    3. quick_search - búsqueda simple (recuperación rápida)
    4. interview_agents - entrevista profunda (entrevista Agents simulados, obtiene perspectivas múltiples)
    
    [Herramientas básicas]
    - search_graph - búsqueda semántica en grafo
    - get_all_nodes - obtiene todos los nodos del grafo
    - get_all_edges - obtiene todas las aristas del grafo (con información temporal)
    - get_node_detail - obtiene información detallada de un nodo
    - get_node_edges - obtiene las aristas relacionadas con un nodo
    - get_entities_by_type - obtiene entidades por tipo
    - get_entity_summary - obtiene el resumen de relaciones de una entidad
    """
    
    # Configuración de reintentos
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY no configurado")
        
        self.client = Zep(api_key=self.api_key)
        # Cliente LLM para que InsightForge genere subpreguntas
        self._llm_client = llm_client
        logger.info("ZepToolsService inicializado correctamente")
    
    @property
    def llm(self) -> LLMClient:
        """Inicialización diferida del cliente LLM"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """Llamada a API con mecanismo de reintento"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} intento {attempt + 1} fallido: {str(e)[:100]}, "
                        f"reintentando en {delay:.1f} segundos..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name} falló después de {max_retries} intentos: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Búsqueda semántica en grafo
        
        Utiliza búsqueda híbrida (semántica + BM25) para buscar información relevante en el grafo.
        Si la Search API de Zep Cloud no está disponible, se degrada a coincidencia local de palabras clave.
        
        Args:
            graph_id: ID del grafo (Standalone Graph)
            query: Consulta de búsqueda
            limit: Cantidad de resultados a retornar
            scope: Alcance de búsqueda, "edges" o "nodes"
            
        Returns:
            SearchResult: Resultado de búsqueda
        """
        logger.info(f"Búsqueda en grafo: graph_id={graph_id}, query={query[:50]}...")
        
        # Intentar usar la Search API de Zep Cloud
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"búsqueda en grafo (graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # Parsear resultados de búsqueda de aristas
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # Parsear resultados de búsqueda de nodos
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # El resumen del nodo también cuenta como hecho
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Búsqueda completada: se encontraron {len(facts)} hechos relevantes")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search API falló, se degrada a búsqueda local: {str(e)}")
            # Degradar: usar búsqueda local por coincidencia de palabras clave
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Búsqueda local por coincidencia de palabras clave (como degradación de la Zep Search API)
        
        Obtiene todas las aristas/nodos y luego realiza coincidencia de palabras clave localmente
        
        Args:
            graph_id: ID del grafo
            query: Consulta de búsqueda
            limit: Cantidad de resultados a retornar
            scope: Alcance de búsqueda
            
        Returns:
            SearchResult: Resultado de búsqueda
        """
        logger.info(f"Usando búsqueda local: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # Extraer palabras clave de la consulta (tokenización simple)
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """Calcula la puntuación de coincidencia entre el texto y la consulta"""
            if not text:
                return 0
            text_lower = text.lower()
            # Coincidencia exacta con la consulta
            if query_lower in text_lower:
                return 100
            # Coincidencia de palabras clave
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # Obtener todas las aristas y hacer coincidencia
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # Ordenar por puntuación
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # Obtener todos los nodos y hacer coincidencia
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Búsqueda local completada: se encontraron {len(facts)} hechos relevantes")
            
        except Exception as e:
            logger.error(f"Búsqueda local fallida: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        Obtiene todos los nodos del grafo (con paginación)

        Args:
            graph_id: ID del grafo

        Returns:
            Lista de nodos
        """
        logger.info(f"Obteniendo todos los nodos del grafo {graph_id}...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"Se obtuvieron {len(result)} nodos")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        Obtiene todas las aristas del grafo (con paginación, incluye información temporal)

        Args:
            graph_id: ID del grafo
            include_temporal: Si incluir información temporal (predeterminado True)

        Returns:
            Lista de aristas (incluye created_at, valid_at, invalid_at, expired_at)
        """
        logger.info(f"Obteniendo todas las aristas del grafo {graph_id}...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # Agregar información temporal
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"Se obtuvieron {len(result)} aristas")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        Obtiene información detallada de un nodo
        
        Args:
            node_uuid: UUID del nodo
            
        Returns:
            Información del nodo o None
        """
        logger.info(f"Obteniendo detalles del nodo: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"obtener detalle de nodo (uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"Error al obtener detalles del nodo: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        Obtiene todas las aristas relacionadas con un nodo
        
        Obtiene todas las aristas del grafo y luego filtra las que están relacionadas con el nodo especificado
        
        Args:
            graph_id: ID del grafo
            node_uuid: UUID del nodo
            
        Returns:
            Lista de aristas
        """
        logger.info(f"Obteniendo aristas relacionadas con el nodo {node_uuid[:8]}...")
        
        try:
            # Obtener todas las aristas del grafo y luego filtrar
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # Verificar si la arista está relacionada con el nodo especificado (como origen o destino)
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"Se encontraron {len(result)} aristas relacionadas con el nodo")
            return result
            
        except Exception as e:
            logger.warning(f"Error al obtener aristas del nodo: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        Obtiene entidades por tipo
        
        Args:
            graph_id: ID del grafo
            entity_type: Tipo de entidad (p. ej. Student, PublicFigure, etc.)
            
        Returns:
            Lista de entidades del tipo especificado
        """
        logger.info(f"Obteniendo entidades de tipo {entity_type}...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # Verificar si labels contiene el tipo especificado
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"Se encontraron {len(filtered)} entidades de tipo {entity_type}")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        Obtiene el resumen de relaciones de la entidad especificada
        
        Busca toda la información relacionada con esta entidad y genera un resumen
        
        Args:
            graph_id: ID del grafo
            entity_name: Nombre de la entidad
            
        Returns:
            Información de resumen de la entidad
        """
        logger.info(f"Obteniendo resumen de relaciones de la entidad {entity_name}...")
        
        # Primero buscar información relacionada con esta entidad
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # Intentar encontrar esta entidad entre todos los nodos
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # Pasar el parámetro graph_id
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        Obtiene estadísticas del grafo
        
        Args:
            graph_id: ID del grafo
            
        Returns:
            Información estadística
        """
        logger.info(f"Obteniendo estadísticas del grafo {graph_id}...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # Distribución de tipos de entidades
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # Distribución de tipos de relaciones
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        Obtiene información de contexto relacionada con la simulación
        
        Busca de forma integral toda la información relacionada con los requisitos de la simulación
        
        Args:
            graph_id: ID del grafo
            simulation_requirement: Descripción de los requisitos de simulación
            limit: Límite de cantidad de información por categoría
            
        Returns:
            Información de contexto de la simulación
        """
        logger.info(f"Obteniendo contexto de simulación: {simulation_requirement[:50]}...")
        
        # Buscar información relacionada con los requisitos de simulación
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # Obtener estadísticas del grafo
        stats = self.get_graph_statistics(graph_id)
        
        # Obtener todos los nodos de entidades
        all_nodes = self.get_all_nodes(graph_id)
        
        # Filtrar entidades con tipo real (no nodos Entity puros)
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # Limitar la cantidad
            "total_entities": len(entities)
        }
    
    # ========== Herramientas de recuperación principales (optimizadas) ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        [InsightForge - Recuperación de insights profundos]
        
        La función de recuperación híbrida más potente, descompone automáticamente el problema y recupera desde múltiples dimensiones:
        1. Usa LLM para descomponer el problema en múltiples subpreguntas
        2. Realiza búsqueda semántica para cada subpregunta
        3. Extrae entidades relevantes y obtiene su información detallada
        4. Rastrea cadenas de relaciones
        5. Integra todos los resultados y genera insights profundos
        
        Args:
            graph_id: ID del grafo
            query: Pregunta del usuario
            simulation_requirement: Descripción de los requisitos de simulación
            report_context: Contexto del informe (opcional, para generación de subpreguntas más precisas)
            max_sub_queries: Número máximo de subpreguntas
            
        Returns:
            InsightForgeResult: Resultado de recuperación de insights profundos
        """
        logger.info(f"InsightForge recuperación de insights profundos: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Paso 1: Usar LLM para generar subpreguntas
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"Se generaron {len(sub_queries)} subpreguntas")
        
        # Paso 2: Realizar búsqueda semántica para cada subpregunta
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # También buscar la pregunta original
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Paso 3: Extraer UUIDs de entidades relevantes de las aristas, obtener solo la información de estas entidades (no todos los nodos)
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # Obtener detalles de todas las entidades relevantes (sin límite de cantidad, salida completa)
        entity_insights = []
        node_map = {}  # Para construcción posterior de cadenas de relaciones
        
        for uuid in list(entity_uuids):  # Procesar todas las entidades, sin truncar
            if not uuid:
                continue
            try:
                # Obtener información de cada nodo relacionado individualmente
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "entidad")
                    
                    # Obtener todos los hechos relacionados con esta entidad (sin truncar)
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts  # Salida completa, sin truncar
                    })
            except Exception as e:
                logger.debug(f"Error al obtener nodo {uuid}: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Paso 4: Construir todas las cadenas de relaciones (sin límite de cantidad)
        relationship_chains = []
        for edge_data in all_edges:  # Procesar todas las aristas, sin truncar
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge completado: {result.total_facts} hechos, {result.total_entities} entidades, {result.total_relationships} relaciones")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        Usa LLM para generar subpreguntas
        
        Descompone un problema complejo en múltiples subpreguntas que se pueden recuperar de forma independiente
        """
        system_prompt = """Responde contenido narrativo en español latino. Conserva claves JSON y nombres técnicos exactamente cuando el sistema los requiera.

Eres un experto profesional en análisis de preguntas. Tu tarea es descomponer una pregunta compleja en múltiples subpreguntas que se puedan observar de forma independiente en el mundo simulado.

Requisitos:
1. Cada subpregunta debe ser suficientemente específica para encontrar comportamientos o eventos de Agents relevantes en el mundo simulado
2. Las subpreguntas deben cubrir diferentes dimensiones de la pregunta original (p. ej.: quién, qué, por qué, cómo, cuándo, dónde)
3. Las subpreguntas deben estar relacionadas con el escenario de simulación
4. Retornar en formato JSON: {"sub_queries": ["subpregunta1", "subpregunta2", ...]}"""

        user_prompt = f"""Contexto de requisitos de simulación:
{simulation_requirement}

{f"Contexto del informe: {report_context[:500]}" if report_context else ""}

Por favor descompone la siguiente pregunta en {max_queries} subpreguntas:
{query}

Retorna la lista de subpreguntas en formato JSON."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # Asegurarse de que sea una lista de strings
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"Error al generar subpreguntas: {str(e)}, usando subpreguntas predeterminadas")
            # Degradar: retornar variantes basadas en la pregunta original
            return [
                query,
                f"principales participantes en {query}",
                f"causas y efectos de {query}",
                f"proceso de desarrollo de {query}"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        [PanoramaSearch - Búsqueda amplia]
        
        Obtiene una vista completa, incluido todo el contenido relevante e información histórica/expirada:
        1. Obtiene todos los nodos relevantes
        2. Obtiene todas las aristas (incluidas las expiradas/caducadas)
        3. Clasifica y organiza hechos actualmente válidos e históricos
        
        Esta herramienta es adecuada para escenarios donde se necesita conocer el panorama completo de un evento o rastrear su proceso de evolución.
        
        Args:
            graph_id: ID del grafo
            query: Consulta de búsqueda (para ordenamiento por relevancia)
            include_expired: Si incluir contenido expirado (predeterminado True)
            limit: Límite de cantidad de resultados
            
        Returns:
            PanoramaResult: Resultado de búsqueda amplia
        """
        logger.info(f"PanoramaSearch búsqueda amplia: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # Obtener todos los nodos
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # Obtener todas las aristas (con información temporal)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # Clasificar hechos
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # Agregar nombres de entidades a los hechos
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # Determinar si está expirado/caducado
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # Hecho histórico/expirado, agregar marca temporal
                valid_at = edge.valid_at or "desconocido"
                invalid_at = edge.invalid_at or edge.expired_at or "desconocido"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # Hecho actualmente válido
                active_facts.append(edge.fact)
        
        # Ordenar por relevancia basado en la consulta
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # Ordenar y limitar cantidad
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch completado: {result.active_count} válidos, {result.historical_count} históricos")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        [QuickSearch - Búsqueda simple]
        
        Herramienta de recuperación rápida y ligera:
        1. Llama directamente a la búsqueda semántica de Zep
        2. Retorna los resultados más relevantes
        3. Adecuada para necesidades de recuperación simples y directas
        
        Args:
            graph_id: ID del grafo
            query: Consulta de búsqueda
            limit: Cantidad de resultados a retornar
            
        Returns:
            SearchResult: Resultado de búsqueda
        """
        logger.info(f"QuickSearch búsqueda simple: {query[:50]}...")
        
        # Llamar directamente al método search_graph existente
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch completado: {result.total_count} resultados")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        [InterviewAgents - Entrevista en profundidad]
        
        Llama a la API de entrevistas real de OASIS para entrevistar a Agents que están ejecutándose en la simulación:
        1. Lee automáticamente el archivo de perfiles para conocer todos los Agents simulados
        2. Usa LLM para analizar los requisitos de entrevista e inteligentemente selecciona los Agents más relevantes
        3. Usa LLM para generar preguntas de entrevista
        4. Llama al endpoint /api/simulation/interview/batch para entrevistas reales (simultáneamente en ambas plataformas)
        5. Integra todos los resultados de entrevistas y genera un informe
        
        [IMPORTANTE] Esta funcionalidad requiere que el entorno de simulación esté en ejecución (entorno OASIS no cerrado)
        
        [Casos de uso]
        - Necesidad de conocer opiniones sobre eventos desde diferentes perspectivas de roles
        - Necesidad de recopilar opiniones y puntos de vista de múltiples partes
        - Necesidad de obtener respuestas reales de Agents simulados (no simulaciones LLM)
        
        Args:
            simulation_id: ID de simulación (para localizar el archivo de perfiles y llamar a la API de entrevistas)
            interview_requirement: Descripción de los requisitos de entrevista (no estructurado, p. ej. "conocer la opinión de estudiantes sobre el evento")
            simulation_requirement: Contexto de requisitos de simulación (opcional)
            max_agents: Número máximo de Agents a entrevistar
            custom_questions: Preguntas de entrevista personalizadas (opcional, se generan automáticamente si no se proporcionan)
            
        Returns:
            InterviewResult: Resultado de entrevista
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents entrevista en profundidad (API real): {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Paso 1: Leer el archivo de perfiles
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"No se encontró el archivo de perfiles para la simulación {simulation_id}")
            result.summary = "No se encontró el archivo de perfiles de Agents disponibles para entrevistar"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"Se cargaron {len(profiles)} perfiles de Agents")
        
        # Paso 2: Usar LLM para seleccionar los Agents a entrevistar (retorna lista de agent_id)
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"Se seleccionaron {len(selected_agents)} Agents para entrevistar: {selected_indices}")
        
        # Paso 3: Generar preguntas de entrevista (si no se proporcionaron)
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"Se generaron {len(result.interview_questions)} preguntas de entrevista")
        
        # Combinar las preguntas en un solo prompt de entrevista
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # Agregar prefijo optimizado para restringir el formato de respuesta del Agent
        INTERVIEW_PROMPT_PREFIX = (
            "Estás siendo entrevistado. Por favor responde las siguientes preguntas directamente en texto plano, "
            "basándote en tu perfil de personaje, todos tus recuerdos y acciones pasadas.\n"
            "Requisitos de respuesta:\n"
            "1. Responde directamente en lenguaje natural, no llames ninguna herramienta\n"
            "2. No retornes formato JSON ni formato de llamada a herramienta\n"
            "3. No uses títulos Markdown (como #, ##, ###)\n"
            "4. Responde las preguntas una por una según su número, cada respuesta comenzando con «Pregunta X:» (X es el número de pregunta)\n"
            "5. Separa las respuestas de cada pregunta con una línea en blanco\n"
            "6. Las respuestas deben tener contenido sustancial, al menos 2-3 oraciones por pregunta\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Paso 4: Llamar a la API de entrevistas real (sin especificar platform, por defecto entrevista en ambas plataformas simultáneamente)
        try:
            # Construir la lista de entrevistas por lotes (sin especificar platform, entrevista en ambas plataformas)
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # Usar el prompt optimizado
                    # Sin especificar platform, la API entrevistará en twitter y reddit simultáneamente
                })
            
            logger.info(f"Llamando a la API de entrevistas por lotes (doble plataforma): {len(interviews_request)} Agents")
            
            # Llamar al método de entrevistas por lotes de SimulationRunner (sin pasar platform, doble plataforma)
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # Sin especificar platform, entrevista en ambas plataformas
                timeout=180.0   # La doble plataforma requiere mayor tiempo de espera
            )
            
            logger.info(f"Respuesta de la API de entrevistas: {api_result.get('interviews_count', 0)} resultados, success={api_result.get('success')}")
            
            # Verificar si la llamada a la API fue exitosa
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "error desconocido")
                logger.warning(f"La API de entrevistas retornó fallo: {error_msg}")
                result.summary = f"Error al llamar a la API de entrevistas: {error_msg}. Por favor verifique el estado del entorno de simulación OASIS."
                return result
            
            # Paso 5: Parsear los resultados de la API y construir objetos AgentInterview
            # Formato de retorno en modo doble plataforma: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "desconocido")
                agent_bio = agent.get("bio", "")
                
                # Obtener los resultados de entrevista de este Agent en ambas plataformas
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # Limpiar posibles envoltorios JSON de llamadas a herramientas
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # Siempre mostrar marcas de doble plataforma
                twitter_text = twitter_response if twitter_response else "(no se obtuvo respuesta en esta plataforma)"
                reddit_text = reddit_response if reddit_response else "(no se obtuvo respuesta en esta plataforma)"
                response_text = f"[Respuesta en Twitter]\n{twitter_text}\n\n[Respuesta en Reddit]\n{reddit_text}"

                # Extraer citas clave (de las respuestas en ambas plataformas)
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # Limpiar el texto de respuesta: eliminar marcas, numeración, Markdown, etc.
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'问题\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'【[^】]+】', '', clean_text)

                # Estrategia 1 (principal): extraer oraciones completas con contenido sustancial
                sentences = re.split(r'[。！？]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W，,；;：:、]+', s.strip())
                    and not s.strip().startswith(('{', '问题'))  # mantener regex: coincide con salida LLM en chino
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "。" for s in meaningful[:3]]

                # Estrategia 2 (complementaria): texto largo dentro de comillas chinas correctamente emparejadas「」
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[，,；;：:、]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # Ampliar el límite de longitud del bio
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # Entorno de simulación no en ejecución
            logger.warning(f"Error al llamar a la API de entrevistas (¿entorno no en ejecución?): {e}")
            result.summary = f"Entrevista fallida: {str(e)}. El entorno de simulación puede estar cerrado, asegúrese de que el entorno OASIS esté en ejecución."
            return result
        except Exception as e:
            logger.error(f"Excepción al llamar a la API de entrevistas: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"Ocurrió un error durante la entrevista: {str(e)}"
            return result
        
        # Paso 6: Generar resumen de entrevista
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents completado: se entrevistaron {result.interviewed_count} Agents (doble plataforma)")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Limpia el envoltorio JSON de llamadas a herramientas en respuestas de Agents y extrae el contenido real"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """Carga el archivo de perfiles de Agents de la simulación"""
        import os
        import csv
        
        # Construir la ruta del archivo de perfiles
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # Primero intentar leer el formato JSON de Reddit
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"Se cargaron {len(profiles)} perfiles desde reddit_profiles.json")
                return profiles
            except Exception as e:
                logger.warning(f"Error al leer reddit_profiles.json: {e}")
        
        # Intentar leer el formato CSV de Twitter
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # Convertir formato CSV al formato unificado
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "desconocido"
                        })
                logger.info(f"Se cargaron {len(profiles)} perfiles desde twitter_profiles.csv")
                return profiles
            except Exception as e:
                logger.warning(f"Error al leer twitter_profiles.csv: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        Usa LLM para seleccionar los Agents a entrevistar
        
        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: Lista completa de información de Agents seleccionados
                - selected_indices: Lista de índices de Agents seleccionados (para llamada a API)
                - reasoning: Razón de la selección
        """
        
        # Construir lista de resúmenes de Agents
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "desconocido"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """Responde contenido narrativo en español latino. Conserva claves JSON y nombres técnicos exactamente cuando el sistema los requiera.

Eres un experto en planificación de entrevistas. Tu tarea es seleccionar los objetos más adecuados para entrevistar de la lista de Agents simulados, según los requisitos de la entrevista.

Criterios de selección:
1. La identidad/profesión del Agent es relevante para el tema de la entrevista
2. El Agent puede tener perspectivas únicas o valiosas
3. Seleccionar perspectivas diversas (p. ej.: partido a favor, en contra, neutral, expertos, etc.)
4. Dar prioridad a los roles directamente relacionados con el evento

Retornar en formato JSON:
{
    "selected_indices": [lista de índices de Agents seleccionados],
    "reasoning": "explicación de la razón de selección"
}"""

        user_prompt = f"""Requisitos de entrevista:
{interview_requirement}

Contexto de simulación:
{simulation_requirement if simulation_requirement else "no proporcionado"}

Lista de Agents disponibles (total {len(agent_summaries)}):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

Por favor seleccione hasta {max_agents} Agents más adecuados para entrevistar y explique la razón de selección."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "selección automática basada en relevancia")
            
            # Obtener la información completa de los Agents seleccionados
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"Error al seleccionar Agents con LLM, usando selección predeterminada: {e}")
            # Degradar: seleccionar los primeros N
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "usando estrategia de selección predeterminada"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """Usa LLM para generar preguntas de entrevista"""
        
        agent_roles = [a.get("profession", "desconocido") for a in selected_agents]
        
        system_prompt = """Responde contenido narrativo en español latino. Conserva claves JSON y nombres técnicos exactamente cuando el sistema los requiera.

Eres un periodista/entrevistador profesional. Genera 3-5 preguntas de entrevista profunda según los requisitos de la entrevista.

Requisitos de las preguntas:
1. Preguntas abiertas que fomenten respuestas detalladas
2. Pueden tener diferentes respuestas para diferentes roles
3. Cubrir múltiples dimensiones: hechos, opiniones, sentimientos
4. Lenguaje natural, como una entrevista real
5. Cada pregunta de máximo 50 caracteres, concisa y clara
6. Preguntar directamente, sin incluir explicaciones de contexto ni prefijos

Retornar en formato JSON: {"questions": ["pregunta1", "pregunta2", ...]}"""

        user_prompt = f"""Requisitos de entrevista: {interview_requirement}

Contexto de simulación: {simulation_requirement if simulation_requirement else "no proporcionado"}

Roles de los entrevistados: {', '.join(agent_roles)}

Por favor genere 3-5 preguntas de entrevista."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"¿Cuál es su opinión sobre {interview_requirement}?"])
            
        except Exception as e:
            logger.warning(f"Error al generar preguntas de entrevista: {e}")
            return [
                f"¿Cuál es su punto de vista sobre {interview_requirement}?",
                "¿Cómo le afecta este asunto a usted o al grupo que representa?",
                "¿Cómo cree que se debería resolver o mejorar este problema?"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """Genera el resumen de entrevista"""
        
        if not interviews:
            return "No se completó ninguna entrevista"
        
        # Recopilar todo el contenido de entrevistas
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"【{interview.agent_name}（{interview.agent_role}）】\n{interview.response[:500]}")
        
        system_prompt = """Responde contenido narrativo en español latino. Conserva claves JSON y nombres técnicos exactamente cuando el sistema los requiera.

Eres un editor de noticias profesional. Por favor genera un resumen de entrevista basado en las respuestas de múltiples entrevistados.

Requisitos del resumen:
1. Extraer los puntos de vista principales de cada parte
2. Señalar consensos y divergencias en las opiniones
3. Destacar citas valiosas
4. Objetivo y neutral, sin favorecer a ninguna parte
5. Máximo 1000 palabras

Restricciones de formato (deben cumplirse):
- Usar párrafos de texto plano, separar las diferentes partes con líneas en blanco
- No usar títulos Markdown (como #, ##, ###)
- No usar líneas divisorias (como ---, ***)
- Al citar las palabras originales del entrevistado usar comillas «»
- Se puede usar **negrita** para marcar palabras clave, pero no usar otra sintaxis Markdown"""

        user_prompt = f"""Tema de entrevista: {interview_requirement}

Contenido de la entrevista:
{"".join(interview_texts)}

Por favor genere el resumen de entrevista."""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"Error al generar resumen de entrevista: {e}")
            # Degradar: concatenación simple
            return f"Se entrevistaron {len(interviews)} personas, incluyendo: " + ", ".join([i.agent_name for i in interviews])
