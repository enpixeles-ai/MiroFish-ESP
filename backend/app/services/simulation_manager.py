"""
Gestor de simulación OASIS
Gestiona simulaciones paralelas de doble plataforma Twitter y Reddit
Usa scripts predefinidos + generación inteligente de parámetros de configuración por LLM
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """Estado de simulación"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # Simulación detenida manualmente
    COMPLETED = "completed"  # Simulación completada naturalmente
    FAILED = "failed"


class PlatformType(str, Enum):
    """Tipo de plataforma"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """Estado de simulación"""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # Estado de activación de plataforma
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # Estado
    status: SimulationStatus = SimulationStatus.CREATED
    
    # Datos de la fase de preparación
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    
    # Información de generación de configuración
    config_generated: bool = False
    config_reasoning: str = ""
    
    # Datos en tiempo de ejecución
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # Marcas de tiempo
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Información de error
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Diccionario de estado completo (uso interno)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """Diccionario de estado simplificado (para retorno de API)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    Gestor de simulaciones

    Funcionalidades principales:
    1. Leer y filtrar entidades del grafo Zep
    2. Generar perfiles de agente OASIS
    3. Generar inteligentemente parámetros de configuración de simulación con LLM
    4. Preparar todos los archivos requeridos por los scripts predefinidos
    """
    
    # Directorio de almacenamiento de datos de simulación
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # Asegurar que el directorio existe
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # Caché del estado de simulación en memoria
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """Obtener directorio de datos de simulación"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """Guardar estado de simulación en archivo"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """Cargar estado de simulación desde archivo"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        Crear nueva simulación
        
        Args:
            project_id: ID del proyecto
            graph_id: ID del grafo Zep
            enable_twitter: si habilitar simulación de Twitter
            enable_reddit: si habilitar simulación de Reddit
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"Creando simulación: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3
    ) -> SimulationState:
        """
        Preparar el entorno de simulación (completamente automatizado)

        Pasos:
        1. Leer y filtrar entidades del grafo Zep
        2. Generar perfil de agente OASIS para cada entidad (mejora opcional por LLM, soporta paralelismo)
        3. Generar inteligentemente parámetros de configuración de simulación con LLM (tiempo, actividad, frecuencia de publicación, etc.)
        4. Guardar archivos de configuración y de perfil
        5. Copiar scripts predefinidos al directorio de simulación
        
        Args:
            simulation_id: ID de simulación
            simulation_requirement: descripción de requisitos de simulación (para generación de configuración por LLM)
            document_text: contenido del documento original (para que el LLM entienda el contexto)
            defined_entity_types: tipos de entidad predefinidos (opcional)
            use_llm_for_profiles: si usar LLM para generar perfiles detallados
            progress_callback: función callback de progreso (stage, progress, message)
            parallel_profile_count: número de perfiles a generar en paralelo, predeterminado 3
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"La simulación no existe: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== Fase 1: Leer y filtrar entidades ==========
            if progress_callback:
                progress_callback("reading", 0, "Conectando al grafo Zep...")
            
            reader = ZepEntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "Leyendo datos de nodos...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )
            
            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"Completado, {filtered.filtered_count} entidades en total",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "No se encontraron entidades que coincidan, verifica que el grafo esté correctamente construido"
                self._save_simulation_state(state)
                return state
            
            # ========== Fase 2: Generar perfil de agente ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "Iniciando generación...",
                    current=0,
                    total=total_entities
                )
            
            # Pasar graph_id para habilitar recuperación de Zep y obtener contexto más rico
            generator = OasisProfileGenerator(graph_id=state.graph_id)
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # Configurar ruta de archivo para guardado en tiempo real (priorizar formato JSON de Reddit)
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # Pasar graph_id para habilitar recuperación de Zep
                parallel_count=parallel_profile_count,  # Número de perfiles a generar en paralelo
                realtime_output_path=realtime_output_path,  # Ruta de guardado en tiempo real
                output_platform=realtime_platform  # Formato de salida
            )
            
            state.profiles_count = len(profiles)
            
            # Guardar archivos de perfil (nota: Twitter usa formato CSV, Reddit usa formato JSON)
            # Reddit ya se guardó en tiempo real durante la generación, aquí se guarda otra vez para garantizar integridad
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "Guardando archivos de perfil...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter usa formato CSV, es un requisito de OASIS
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"Completado, {len(profiles)} perfiles en total",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== Fase 3: Generación inteligente de configuración de simulación por LLM ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0, 
                    "Analizando requisitos de simulación...",
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30, 
                    "Llamando a LLM para generar configuración...",
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70, 
                    "Guardando archivos de configuración...",
                    current=2,
                    total=3
                )
            
            # Guardar archivos de configuración
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100, 
                    "Generación de configuración completada",
                    current=3,
                    total=3
                )
            
            # Nota: los scripts de ejecución se mantienen en el directorio backend/scripts/, ya no se copian al directorio de simulación
            # Al iniciar la simulación, simulation_runner ejecutará los scripts desde el directorio scripts/
            
            # Actualizar estado
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"Preparación de simulación completada: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"Error en preparación de simulación: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """Obtener estado de simulación"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """Listar todas las simulaciones"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # Omitir archivos ocultos (ej. .DS_Store) y archivos que no son directorios
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        simulations.sort(key=lambda state: state.created_at or state.updated_at or "", reverse=True)
        return simulations

    def delete_simulation(self, simulation_id: str) -> Dict[str, Any]:
        """Eliminar los archivos persistidos de una simulación."""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        if not os.path.isdir(sim_dir):
            raise ValueError(f"La simulación no existe: {simulation_id}")

        state = self._load_simulation_state(simulation_id)
        shutil.rmtree(sim_dir)
        self._simulations.pop(simulation_id, None)

        return {
            "simulation_id": simulation_id,
            "project_id": state.project_id if state else "",
            "deleted": True,
        }
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """Obtener perfil de agente de la simulación"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"La simulación no existe: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")
        
        if not os.path.exists(profile_path):
            return []
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Obtener configuración de simulación"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """Obtener instrucciones de ejecución"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. Activar entorno conda: conda activate MiroFish\n"
                f"2. Ejecutar simulación (scripts en {scripts_dir}):\n"
                f"   - Solo Twitter: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - Solo Reddit: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - Doble plataforma en paralelo: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
