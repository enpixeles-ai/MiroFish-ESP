"""
Servicio de actualización de memoria del grafo Zep
Actualiza dinámicamente las actividades de los agentes de simulación en el grafo Zep
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Registro de actividad del agente"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        Convertir actividad a descripción de texto que puede enviarse a Zep

        Usa formato de lenguaje natural para que Zep pueda extraer entidades y relaciones
        No agrega prefijos relacionados con la simulación para evitar confundir la actualización del grafo
        """
        # Generar diferentes descripciones según el tipo de acción
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # Retornar directamente en formato "nombre_agente: descripción_actividad" sin prefijo de simulación
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"publicó una publicación: «{content}»"
        return "publicó una publicación"
    
    def _describe_like_post(self) -> str:
        """Me gusta en publicación - incluye texto original y autor"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"le dio me gusta a la publicación de {post_author}: «{post_content}»"
        elif post_content:
            return f"le dio me gusta a una publicación: «{post_content}»"
        elif post_author:
            return f"le dio me gusta a una publicación de {post_author}"
        return "le dio me gusta a una publicación"
    
    def _describe_dislike_post(self) -> str:
        """No me gusta en publicación - incluye texto original y autor"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"no le gustó la publicación de {post_author}: «{post_content}»"
        elif post_content:
            return f"no le gustó una publicación: «{post_content}»"
        elif post_author:
            return f"no le gustó una publicación de {post_author}"
        return "no le gustó una publicación"
    
    def _describe_repost(self) -> str:
        """Repostear - incluye contenido original y autor"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"reposteó la publicación de {original_author}: «{original_content}»"
        elif original_content:
            return f"reposteó una publicación: «{original_content}»"
        elif original_author:
            return f"reposteó una publicación de {original_author}"
        return "reposteó una publicación"
    
    def _describe_quote_post(self) -> str:
        """Citar publicación - incluye contenido original, autor y comentario de cita"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"citó la publicación de {original_author} «{original_content}»"
        elif original_content:
            base = f"citó una publicación «{original_content}»"
        elif original_author:
            base = f"citó una publicación de {original_author}"
        else:
            base = "citó una publicación"
        
        if quote_content:
            base += f", y comentó: «{quote_content}»"
        return base
    
    def _describe_follow(self) -> str:
        """Seguir usuario - incluye nombre del usuario seguido"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"siguió al usuario «{target_user_name}»"
        return "siguió a un usuario"
    
    def _describe_create_comment(self) -> str:
        """Publicar comentario - incluye contenido del comentario e información de la publicación"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"comentó en la publicación de {post_author} «{post_content}»: «{content}»"
            elif post_content:
                return f"comentó en la publicación «{post_content}»: «{content}»"
            elif post_author:
                return f"comentó en una publicación de {post_author}: «{content}»"
            return f"comentó: «{content}»"
        return "publicó un comentario"
    
    def _describe_like_comment(self) -> str:
        """Me gusta en comentario - incluye contenido del comentario y autor"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"le dio me gusta al comentario de {comment_author}: «{comment_content}»"
        elif comment_content:
            return f"le dio me gusta a un comentario: «{comment_content}»"
        elif comment_author:
            return f"le dio me gusta a un comentario de {comment_author}"
        return "le dio me gusta a un comentario"
    
    def _describe_dislike_comment(self) -> str:
        """No me gusta en comentario - incluye contenido del comentario y autor"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"no le gustó el comentario de {comment_author}: «{comment_content}»"
        elif comment_content:
            return f"no le gustó un comentario: «{comment_content}»"
        elif comment_author:
            return f"no le gustó un comentario de {comment_author}"
        return "no le gustó un comentario"
    
    def _describe_search(self) -> str:
        """Buscar publicaciones - incluye palabras clave de búsqueda"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"buscó «{query}»" if query else "realizó una búsqueda"
    
    def _describe_search_user(self) -> str:
        """Buscar usuarios - incluye palabras clave de búsqueda"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"buscó al usuario «{query}»" if query else "buscó usuarios"
    
    def _describe_mute(self) -> str:
        """Bloquear usuario - incluye nombre del usuario bloqueado"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"bloqueó al usuario «{target_user_name}»"
        return "bloqueó a un usuario"
    
    def _describe_generic(self) -> str:
        # Para tipos de acción desconocidos, generar descripción genérica
        return f"ejecutó la operación {self.action_type}"


class ZepGraphMemoryUpdater:
    """
    Actualizador de memoria del grafo Zep

    Monitorea los archivos de log de acciones de la simulación y actualiza en tiempo real
    las actividades de los agentes en el grafo Zep.
    Agrupa por plataforma y envía en lote a Zep tras acumular BATCH_SIZE actividades.

    Todas las acciones significativas se actualizan en Zep, action_args incluye contexto completo:
    - Texto original de publicaciones con me gusta/no me gusta
    - Texto original de publicaciones reposteadas/citadas
    - Nombre de usuarios seguidos/bloqueados
    - Texto original de comentarios con me gusta/no me gusta
    """
    
    # Tamaño del lote de envío (cuántas actividades acumular por plataforma antes de enviar)
    BATCH_SIZE = int(os.environ.get("ZEP_GRAPH_BATCH_SIZE", "20"))
    
    # Mapeo de nombres de plataforma (para visualización en consola)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'Mundo 1',
        'reddit': 'Mundo 2',
    }
    
    # Intervalo de envío (segundos) para evitar solicitudes demasiado rápidas
    SEND_INTERVAL = float(os.environ.get("ZEP_GRAPH_SEND_INTERVAL", "1.0"))
    
    # Configuración de reintentos
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # segundos
    MAX_EPISODE_CHARS = int(os.environ.get("ZEP_GRAPH_MAX_EPISODE_CHARS", "16000"))
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        Inicializar actualizador
        
        Args:
            graph_id: ID del grafo Zep
            api_key: Zep API Key (opcional, se lee de la configuración por defecto)
        """
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY
        
        if not self.api_key:
            raise ValueError("ZEP_API_KEY no configurado")
        
        self.client = Zep(api_key=self.api_key)
        
        # Cola de actividades
        self._activity_queue: Queue = Queue()
        
        # Buffer de actividades agrupado por plataforma (cada plataforma acumula hasta BATCH_SIZE antes de enviar en lote)
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # Indicadores de control
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # Estadísticas
        self._total_activities = 0  # Total de actividades agregadas a la cola
        self._total_sent = 0        # Lotes enviados exitosamente a Zep
        self._total_items_sent = 0  # Actividades enviadas exitosamente a Zep
        self._failed_count = 0      # Lotes con envío fallido
        self._skipped_count = 0     # Actividades filtradas y omitidas (DO_NOTHING)
        
        logger.info(f"ZepGraphMemoryUpdater inicializado: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """Obtener nombre de visualización de la plataforma"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Iniciar hilo de trabajo en segundo plano"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater iniciado: graph_id={self.graph_id}")
    
    def stop(self):
        """Detener hilo de trabajo en segundo plano"""
        self._running = False
        
        # Enviar actividades restantes
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater detenido: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        Agregar una actividad de agente a la cola

        Todas las acciones significativas se agregan a la cola, incluyendo:
        - CREATE_POST (publicar)
        - CREATE_COMMENT (comentar)
        - QUOTE_POST (citar publicación)
        - SEARCH_POSTS (buscar publicaciones)
        - SEARCH_USER (buscar usuario)
        - LIKE_POST/DISLIKE_POST (me gusta/no me gusta en publicación)
        - REPOST (repostear)
        - FOLLOW (seguir)
        - MUTE (bloquear)
        - LIKE_COMMENT/DISLIKE_COMMENT (me gusta/no me gusta en comentario)

        action_args incluirá contexto completo (ej. texto original de publicación, nombre de usuario, etc.).
        
        Args:
            activity: registro de actividad del agente
        """
        # Omitir actividades de tipo DO_NOTHING
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"Actividad agregada a la cola Zep: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        Agregar actividad desde datos de diccionario
        
        Args:
            data: datos de diccionario analizados desde actions.jsonl
            platform: nombre de la plataforma (twitter/reddit)
        """
        # Omitir entradas de tipo evento
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """Bucle de trabajo en segundo plano - enviar actividades a Zep en lote por plataforma"""
        while self._running or not self._activity_queue.empty():
            try:
                # Intentar obtener actividad de la cola (tiempo de espera 1 segundo)
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # Agregar actividad al buffer de la plataforma correspondiente
                    platform = activity.platform.lower()
                    batch = None
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # Verificar si la plataforma alcanzó el tamaño del lote
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]

                    if batch:
                        self._send_batch_activities(batch, platform)
                        time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"Excepción en el bucle de trabajo: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        Enviar actividades en lote al grafo Zep (combinadas en un texto)
        
        Args:
            activities: lista de actividades del agente
            platform: nombre de la plataforma
        """
        if not activities:
            return
        
        # Combinar múltiples actividades en un texto, separadas por salto de línea
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        if len(combined_text) > self.MAX_EPISODE_CHARS:
            combined_text = combined_text[:self.MAX_EPISODE_CHARS] + "\n...[truncated]"
        
        # Enviar con reintentos
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"Lote de {len(activities)} actividades de {display_name} enviado exitosamente al grafo {self.graph_id}")
                logger.debug(f"Vista previa del lote: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Error al enviar lote a Zep (intento {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"Error al enviar lote a Zep, tras {self.MAX_RETRIES} reintentos: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """Enviar actividades restantes en la cola y el buffer"""
        # Primero procesar las actividades restantes en la cola, agregarlas al buffer
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # Luego enviar las actividades restantes en el buffer de cada plataforma (incluso si no llegan a BATCH_SIZE)
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"Enviando {len(buffer)} actividades restantes de la plataforma {display_name}")
                    self._send_batch_activities(buffer, platform)
            # Limpiar todos los buffers
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtener estadísticas"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # Total de actividades agregadas a la cola
            "batches_sent": self._total_sent,            # Lotes enviados exitosamente
            "items_sent": self._total_items_sent,        # Actividades enviadas exitosamente
            "failed_count": self._failed_count,          # Lotes con envío fallido
            "skipped_count": self._skipped_count,        # Actividades filtradas y omitidas (DO_NOTHING)
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # Tamaño del buffer por plataforma
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    Gestor de actualizadores de memoria del grafo Zep para múltiples simulaciones

    Cada simulación puede tener su propia instancia de actualizador
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        Crear actualizador de memoria del grafo para una simulación
        
        Args:
            simulation_id: ID de simulación
            graph_id: ID del grafo Zep
            
        Returns:
            Instancia de ZepGraphMemoryUpdater
        """
        with cls._lock:
            # Si ya existe, detener el anterior primero
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"Creando actualizador de memoria del grafo: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """Obtener el actualizador de una simulación"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Detener y eliminar el actualizador de una simulación"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"Actualizador de memoria del grafo detenido: simulation_id={simulation_id}")
    
    # Indicador para evitar llamadas duplicadas a stop_all
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Detener todos los actualizadores"""
        # Evitar llamadas duplicadas
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"Error al detener actualizador: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("Todos los actualizadores de memoria del grafo detenidos")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Obtener estadísticas de todos los actualizadores"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
