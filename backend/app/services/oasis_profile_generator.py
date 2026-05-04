"""
Generador de OASIS Agent Profile
Convierte entidades del grafo Zep al formato de Agent Profile requerido por la plataforma de simulación OASIS

Mejoras optimizadas:
1. Llama a la función de búsqueda de Zep para enriquecer la información de nodos
2. Optimiza los prompts para generar perfiles de personaje muy detallados
3. Distingue entre entidades individuales y entidades de grupos abstractos
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """Estructura de datos OASIS Agent Profile"""
    # Campos comunes
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # Campos opcionales - estilo Reddit
    karma: int = 1000
    
    # Campos opcionales - estilo Twitter
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # Información adicional del personaje
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # Información de entidad fuente
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Convertir al formato de plataforma Reddit"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # La biblioteca OASIS requiere que el nombre del campo sea username (sin guión bajo)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # Agregar información adicional del personaje (si existe)
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Convertir al formato de plataforma Twitter"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # La biblioteca OASIS requiere que el nombre del campo sea username (sin guión bajo)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # Agregar información adicional del personaje
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertir al formato de diccionario completo"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    Generador de OASIS Profile
    
    Convierte entidades del grafo Zep al Agent Profile requerido para la simulación OASIS
    
    Características optimizadas:
    1. Llama a la función de búsqueda del grafo Zep para obtener contexto más rico
    2. Genera perfiles de personaje muy detallados (incluye información básica, experiencia profesional, rasgos de personalidad, comportamiento en redes sociales, etc.)
    3. Distingue entre entidades individuales y entidades de grupos abstractos
    """
    
    # Lista de tipos MBTI
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Países por defecto para escenarios latinos/hispanos en USA o LatAm
    COUNTRIES = [
        "Estados Unidos", "México", "Colombia", "Venezuela", "República Dominicana",
        "Puerto Rico", "Cuba", "Perú", "Ecuador", "Argentina", "Chile"
    ]
    
    # Entidades de tipo individual (necesitan generar un personaje concreto)
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # Entidades de tipo grupo/institución (necesitan generar un personaje representativo del grupo)
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
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
        
        # Cliente Zep para recuperar contexto enriquecido
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Error al inicializar el cliente Zep: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        Genera un OASIS Agent Profile a partir de una entidad Zep
        
        Args:
            entity: Nodo de entidad Zep
            user_id: ID de usuario (para OASIS)
            use_llm: Si se usa LLM para generar un perfil detallado
            
        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"
        
        # Información básica
        name = entity.name
        user_name = self._generate_username(name)
        
        # Construir información de contexto
        context = self._build_entity_context(entity)
        
        if use_llm:
            # Usar LLM para generar un perfil detallado
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # Usar reglas para generar un perfil básico
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """Generar nombre de usuario"""
        # Eliminar caracteres especiales, convertir a minúsculas
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # Agregar sufijo aleatorio para evitar duplicados
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Usa la función de búsqueda híbrida del grafo Zep para obtener información enriquecida relacionada con la entidad
        
        Zep no tiene una interfaz de búsqueda híbrida incorporada; hay que buscar edges y nodes por separado y combinar los resultados.
        Se usan solicitudes paralelas para buscar simultáneamente y mejorar la eficiencia.
        
        Args:
            entity: Objeto nodo de entidad
            
        Returns:
            Diccionario que contiene facts, node_summaries y context
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # Se necesita graph_id para poder hacer búsquedas
        if not self.graph_id:
            logger.debug(f"Omitiendo búsqueda Zep: graph_id no está configurado")
            return results
        
        comprehensive_query = f"Toda la información, actividades, eventos, relaciones y contexto sobre {entity_name}"
        
        def search_edges():
            """Buscar aristas (hechos/relaciones) - con mecanismo de reintentos"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Fallo en búsqueda de aristas Zep intento {attempt + 1}: {str(e)[:80]}, reintentando...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Búsqueda de aristas Zep fallida tras {max_retries} intentos: {e}")
            return None
        
        def search_nodes():
            """Buscar nodos (resúmenes de entidades) - con mecanismo de reintentos"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Fallo en búsqueda de nodos Zep intento {attempt + 1}: {str(e)[:80]}, reintentando...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Búsqueda de nodos Zep fallida tras {max_retries} intentos: {e}")
            return None
        
        try:
            # Ejecutar búsquedas de edges y nodes en paralelo
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)
                
                # Obtener resultados
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)
            
            # Procesar resultados de búsqueda de aristas
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)
            
            # Procesar resultados de búsqueda de nodos
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"Entidad relacionada: {node.name}")
            results["node_summaries"] = list(all_summaries)
            
            # Construir contexto integral
            context_parts = []
            if results["facts"]:
                context_parts.append("Información de hechos:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("Entidades relacionadas:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)
            
            logger.info(f"Búsqueda híbrida Zep completada: {entity_name}, obtenidos {len(results['facts'])} hechos, {len(results['node_summaries'])} nodos relacionados")
            
        except concurrent.futures.TimeoutError:
            logger.warning(f"Tiempo de espera agotado en búsqueda Zep ({entity_name})")
        except Exception as e:
            logger.warning(f"Fallo en búsqueda Zep ({entity_name}): {e}")
        
        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        Construye la información de contexto completa de la entidad
        
        Incluye:
        1. Información de aristas de la propia entidad (hechos)
        2. Información detallada de nodos asociados
        3. Información enriquecida recuperada por búsqueda híbrida de Zep
        """
        context_parts = []
        
        # 1. Agregar información de atributos de la entidad
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Atributos de la entidad\n" + "\n".join(attrs))
        
        # 2. Agregar información de aristas relacionadas (hechos/relaciones)
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # sin límite de cantidad
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (entidad relacionada)")
                    else:
                        relationships.append(f"- (entidad relacionada) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### Hechos y relaciones relacionados\n" + "\n".join(relationships))
        
        # 3. Agregar información detallada de nodos asociados
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # sin límite de cantidad
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # Filtrar etiquetas predeterminadas
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### Información de entidades asociadas\n" + "\n".join(related_info))
        
        # 4. Usar búsqueda híbrida de Zep para obtener información más enriquecida
        zep_results = self._search_zep_for_entity(entity)
        
        if zep_results.get("facts"):
            # Deduplicar: excluir hechos ya existentes
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Información de hechos recuperada por Zep\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if zep_results.get("node_summaries"):
            context_parts.append("### Nodos relacionados recuperados por Zep\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """Determina si es una entidad de tipo individual"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """Determina si es una entidad de tipo grupo/institución"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        Usa LLM para generar un perfil de personaje muy detallado
        
        Distingue según el tipo de entidad:
        - Entidad individual: genera una configuración de personaje concreta
        - Entidad de grupo/institución: genera una configuración de cuenta representativa
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # Intentar generar múltiples veces hasta tener éxito o alcanzar el máximo de reintentos
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # Reducir temperatura en cada reintento
                    # No establecer max_tokens, dejar que el LLM responda libremente
                )
                
                content = response.choices[0].message.content
                
                # Verificar si fue truncado (finish_reason no es 'stop')
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"Salida LLM truncada (intento {attempt+1}), intentando reparar...")
                    content = self._fix_truncated_json(content)
                
                # Intentar parsear JSON
                try:
                    result = json.loads(content)
                    
                    # Validar campos requeridos
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} es un {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"Error al parsear JSON (intento {attempt+1}): {str(je)[:80]}")
                    
                    # Intentar reparar JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"Fallo en llamada LLM (intento {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # Retroceso exponencial
        
        logger.warning(f"Fallo al generar perfil con LLM ({max_attempts} intentos): {last_error}, usando generación por reglas")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repara JSON truncado (la salida fue truncada por el límite de max_tokens)"""
        import re
        
        # Si el JSON fue truncado, intentar cerrarlo
        content = content.strip()
        
        # Calcular corchetes/llaves no cerrados
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Verificar si hay cadenas no cerradas
        # Verificación simple: si después del último signo de comillas no hay coma ni corchete de cierre, puede que la cadena esté truncada
        if content and content[-1] not in '",}]':
            # Intentar cerrar la cadena
            content += '"'
        
        # Cerrar corchetes/llaves
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Intentar reparar JSON dañado"""
        import re
        
        # 1. Primero intentar reparar el caso truncado
        content = self._fix_truncated_json(content)
        
        # 2. Intentar extraer la parte JSON
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. Manejar problema de saltos de línea en cadenas
            # Encontrar todos los valores de cadena y reemplazar los saltos de línea en ellos
            def fix_string_newlines(match):
                s = match.group(0)
                # Reemplazar saltos de línea reales dentro de cadenas por espacios
                s = s.replace('\n', ' ').replace('\r', ' ')
                # Reemplazar espacios adicionales
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # Hacer coincidir valores de cadena JSON
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. Intentar parsear
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. Si sigue fallando, intentar una reparación más agresiva
                try:
                    # Eliminar todos los caracteres de control
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # Reemplazar todos los espacios en blanco consecutivos
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. Intentar extraer información parcial del contenido
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # puede estar truncado
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} es un {entity_type}.")
        
        # Si se extrajo contenido significativo, marcar como reparado
        if bio_match or persona_match:
            logger.info(f"Se extrajo información parcial del JSON dañado")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. Fallo total, retornar estructura básica
        logger.warning(f"Fallo al reparar JSON, retornando estructura básica")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} es un {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Obtener el prompt del sistema"""
        base_prompt = "Responde contenido descriptivo en español latino. Devuelve JSON válido. Conserva claves JSON y valores enum exactamente como se solicitan, por ejemplo gender debe ser male/female/other. Genera perfiles detallados y realistas para simulaciones de opinión pública, reproduciendo al máximo la situación real existente; los valores de cadena no pueden contener saltos de línea sin escapar."
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Construye el prompt detallado de personaje para una entidad individual"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "Ninguno"
        context_str = context[:3000] if context else "Sin contexto adicional"
        
        return f"""Genera un perfil de usuario de redes sociales detallado para la entidad, reproduciendo al máximo la situación real existente.

Nombre de entidad: {entity_name}
Tipo de entidad: {entity_type}
Resumen de entidad: {entity_summary}
Atributos de entidad: {attrs_str}

Información de contexto:
{context_str}

Genera JSON con los siguientes campos:

1. bio: Descripción del perfil en redes sociales, 200 palabras
2. persona: Descripción detallada del personaje (texto plano de 2000 palabras), debe incluir:
   - Información básica (edad, profesión, formación académica, ubicación)
   - Historial del personaje (experiencias importantes, relación con los eventos, relaciones sociales)
   - Rasgos de personalidad (tipo MBTI, carácter central, forma de expresar emociones)
   - Comportamiento en redes sociales (frecuencia de publicación, preferencias de contenido, estilo de interacción, características del lenguaje)
   - Posición y opiniones (actitud hacia los temas, contenido que puede provocar enojo/emoción)
   - Características únicas (muletillas, experiencias especiales, aficiones personales)
   - Memoria personal (parte importante del personaje; describe la relación de este individuo con los eventos, así como las acciones y reacciones ya realizadas por este individuo en los eventos)
3. age: Número de edad (debe ser entero)
4. gender: Género, debe ser en inglés: "male" o "female"
5. mbti: Tipo MBTI (ej. INTJ, ENFP, etc.)
6. country: País o región relevante al escenario (ej. "Estados Unidos", "México", "Venezuela")
7. profession: Profesión
8. interested_topics: Array de temas de interés

Importante:
- Todos los valores de campo deben ser cadenas o números, sin saltos de línea
- persona debe ser una descripción textual coherente
- Usa español (excepto el campo gender que debe ser en inglés male/female)
- El contenido debe ser coherente con la información de la entidad
- age debe ser un entero válido, gender debe ser "male" o "female"
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Construye el prompt detallado de personaje para una entidad de grupo/institución"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "Ninguno"
        context_str = context[:3000] if context else "Sin contexto adicional"
        
        return f"""Genera una configuración de cuenta de redes sociales detallada para la entidad de institución/grupo, reproduciendo al máximo la situación real existente.

Nombre de entidad: {entity_name}
Tipo de entidad: {entity_type}
Resumen de entidad: {entity_summary}
Atributos de entidad: {attrs_str}

Información de contexto:
{context_str}

Genera JSON con los siguientes campos:

1. bio: Descripción de la cuenta oficial, 200 palabras, profesional y apropiada
2. persona: Descripción detallada de la configuración de la cuenta (texto plano de 2000 palabras), debe incluir:
   - Información básica de la institución (nombre oficial, naturaleza institucional, antecedentes de fundación, funciones principales)
   - Posicionamiento de la cuenta (tipo de cuenta, público objetivo, función central)
   - Estilo de comunicación (características del lenguaje, expresiones habituales, temas prohibidos)
   - Características del contenido publicado (tipo de contenido, frecuencia de publicación, horarios de mayor actividad)
   - Postura e actitud (posición oficial sobre temas centrales, manera de manejar controversias)
   - Notas especiales (perfil del grupo que representa, hábitos de gestión)
   - Memoria institucional (parte importante del personaje institucional; describe la relación de esta institución con los eventos, así como las acciones y reacciones ya realizadas por esta institución en los eventos)
3. age: Fijar en 30 (edad virtual de la cuenta institucional)
4. gender: Fijar en "other" (las cuentas institucionales usan other para indicar que no son personales)
5. mbti: Tipo MBTI, para describir el estilo de la cuenta, ej. ISTJ representa riguroso y conservador
6. country: País o región relevante al escenario (ej. "Estados Unidos", "México", "Venezuela")
7. profession: Descripción de la función institucional
8. interested_topics: Array de áreas de interés

Importante:
- Todos los valores de campo deben ser cadenas o números, no se permiten valores null
- persona debe ser una descripción textual coherente, sin saltos de línea
- Usa español (excepto el campo gender que debe ser en inglés "other")
- age debe ser el entero 30, gender debe ser la cadena "other"
- Las comunicaciones de cuentas institucionales deben ser coherentes con su identidad"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Genera un perfil básico usando reglas"""
        
        # Generar personajes diferentes según el tipo de entidad
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # Edad virtual de la institución
                "gender": "other",  # Las instituciones usan other
                "mbti": "ISTJ",  # Estilo institucional: riguroso y conservador
                "country": random.choice(self.COUNTRIES),
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # Edad virtual de la institución
                "gender": "other",  # Las instituciones usan other
                "mbti": "ISTJ",  # Estilo institucional: riguroso y conservador
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # Personaje predeterminado
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Establece el ID del grafo para búsqueda en Zep"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        Genera Agent Profiles en lote a partir de entidades (soporta generación en paralelo)
        
        Args:
            entities: Lista de entidades
            use_llm: Si se usa LLM para generar un perfil detallado
            progress_callback: Función de callback de progreso (current, total, message)
            graph_id: ID del grafo, para obtener contexto más enriquecido mediante búsqueda en Zep
            parallel_count: Cantidad de generaciones en paralelo, por defecto 5
            realtime_output_path: Ruta del archivo de escritura en tiempo real (si se proporciona, se escribe por cada perfil generado)
            output_platform: Formato de plataforma de salida ("reddit" o "twitter")
            
        Returns:
            Lista de Agent Profiles
        """
        import concurrent.futures
        from threading import Lock
        
        # Establecer graph_id para búsqueda en Zep
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # Lista preasignada para mantener el orden
        completed_count = [0]  # Usar lista para poder modificarla en clausuras
        lock = Lock()
        
        # Función auxiliar para escritura en tiempo real al archivo
        def save_profiles_realtime():
            """Guardar en tiempo real los profiles ya generados en el archivo"""
            if not realtime_output_path:
                return
            
            with lock:
                # Filtrar los profiles ya generados
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Formato JSON de Reddit
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Formato CSV de Twitter
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"Fallo al guardar profiles en tiempo real: {e}")
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """Función de trabajo para generar un solo perfil"""
            entity_type = entity.get_entity_type() or "Entity"
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # Mostrar el personaje generado en tiempo real en consola y logs
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"Fallo al generar personaje para la entidad {entity.name}: {str(e)}")
                # Crear un perfil básico
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"Iniciando generación en paralelo de {total} personajes de Agent (paralelos: {parallel_count})...")
        print(f"\n{'='*60}")
        print(f"Iniciando generación de personajes de Agent - total {total} entidades, paralelos: {parallel_count}")
        print(f"{'='*60}\n")
        
        # Usar pool de hilos para ejecución en paralelo
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Enviar todas las tareas
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # Recopilar resultados
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Escritura en tiempo real al archivo
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"Completado {current}/{total}: {entity.name} ({entity_type})"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} usa perfil de respaldo: {error}")
                    else:
                        logger.info(f"[{current}/{total}] Perfil generado con éxito: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"Excepción al procesar entidad {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # Escritura en tiempo real al archivo (incluso si es perfil de respaldo)
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"¡Generación de personajes completada! Total generados: {len([p for p in profiles if p])} Agents")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Mostrar en tiempo real el personaje generado en consola (contenido completo, sin truncar)"""
        separator = "-" * 70
        
        # Construir contenido de salida completo (sin truncar)
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'Ninguno'
        
        output_lines = [
            f"\n{separator}",
            f"[Generado] {entity_name} ({entity_type})",
            f"{separator}",
            f"Nombre de usuario: {profile.user_name}",
            f"",
            f"[Descripción]",
            f"{profile.bio}",
            f"",
            f"[Personaje detallado]",
            f"{profile.persona}",
            f"",
            f"[Atributos básicos]",
            f"Edad: {profile.age} | Género: {profile.gender} | MBTI: {profile.mbti}",
            f"Profesión: {profile.profession} | País: {profile.country}",
            f"Temas de interés: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Solo mostrar en consola (evitar duplicados, logger ya no muestra el contenido completo)
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Guarda el Profile en un archivo (elige el formato correcto según la plataforma)
        
        Requisitos de formato de la plataforma OASIS:
        - Twitter: Formato CSV
        - Reddit: Formato JSON
        
        Args:
            profiles: Lista de Profiles
            file_path: Ruta del archivo
            platform: Tipo de plataforma ("reddit" o "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Guarda Twitter Profile en formato CSV (cumpliendo requisitos oficiales de OASIS)
        
        Campos CSV requeridos por OASIS Twitter:
        - user_id: ID de usuario (empieza desde 0 según el orden del CSV)
        - name: Nombre real del usuario
        - username: Nombre de usuario en el sistema
        - user_char: Descripción detallada del personaje (inyectado en el prompt del sistema LLM, guía el comportamiento del Agent)
        - description: Descripción pública breve (mostrada en la página de perfil del usuario)
        
        Diferencia entre user_char y description:
        - user_char: Uso interno, prompt del sistema LLM, determina cómo piensa y actúa el Agent
        - description: Mostrado externamente, descripción visible para otros usuarios
        """
        import csv
        
        # Asegurar que la extensión del archivo sea .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Escribir encabezado requerido por OASIS
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # Escribir filas de datos
            for idx, profile in enumerate(profiles):
                # user_char: Personaje completo (bio + persona), para el prompt del sistema LLM
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # Manejar saltos de línea (reemplazar por espacios en CSV)
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: Descripción breve, para mostrar externamente
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: ID secuencial empezando desde 0
                    profile.name,           # name: Nombre real
                    profile.user_name,      # username: Nombre de usuario
                    user_char,              # user_char: Personaje completo (uso interno LLM)
                    description             # description: Descripción breve (mostrado externamente)
                ]
                writer.writerow(row)
        
        logger.info(f"Guardados {len(profiles)} Twitter Profiles en {file_path} (formato CSV de OASIS)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        Normaliza el campo gender al formato en inglés requerido por OASIS
        
        OASIS requiere: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # Mapeo desde valores de entrada (chino y español) a valores requeridos por OASIS
        gender_map = {
            "男": "male",       # masculino en chino
            "女": "female",     # femenino en chino
            "机构": "other",    # institución en chino
            "其他": "other",    # otro en chino
            # Aliases en español
            "masculino": "male",
            "femenino": "female",
            "institución": "other",
            "otro": "other",
            # Ya tiene valores en inglés
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Guarda Reddit Profile en formato JSON
        
        Usa el mismo formato que to_reddit_format(), para asegurar que OASIS pueda leerlo correctamente.
        Debe incluir el campo user_id, ¡es la clave para la coincidencia de OASIS agent_graph.get_agent()!
        
        Campos requeridos:
        - user_id: ID de usuario (entero, para hacer coincidir el poster_agent_id en initial_posts)
        - username: Nombre de usuario
        - name: Nombre para mostrar
        - bio: Descripción
        - persona: Personaje detallado
        - age: Edad (entero)
        - gender: "male", "female" o "other"
        - mbti: Tipo MBTI
        - country: País
        """
        data = []
        for idx, profile in enumerate(profiles):
            # Usar el mismo formato que to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # Clave: debe incluir user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # Campos requeridos por OASIS - asegurar que todos tengan valores predeterminados
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "Estados Unidos",
            }
            
            # Campos opcionales
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Guardados {len(profiles)} Reddit Profiles en {file_path} (formato JSON, incluye campo user_id)")
    
    # Mantener el nombre del método antiguo como alias para compatibilidad con versiones anteriores
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[Obsoleto] Usar el método save_profiles()"""
        logger.warning("save_profiles_to_json está obsoleto, usar el método save_profiles")
        self.save_profiles(profiles, file_path, platform)
