<template>
  <div 
    class="history-database"
    :class="{ 'no-projects': projects.length === 0 && !loading }"
    ref="historyContainer"
  >
    <!-- Decoración de fondo: cuadrícula técnica (solo cuando hay proyectos) -->
    <div v-if="projects.length > 0 || loading" class="tech-grid-bg">
      <div class="grid-pattern"></div>
      <div class="gradient-overlay"></div>
    </div>

    <!-- Área de título -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">Historial de simulaciones</span>
      <div class="section-line"></div>
    </div>

    <!-- Contenedor de tarjetas (solo cuando hay proyectos) -->
    <div v-if="projects.length > 0" class="cards-container" :class="{ expanded: isExpanded }" :style="containerStyle">
      <div 
        v-for="(project, index) in projects" 
        :key="project.simulation_id"
        class="project-card"
        :class="{ expanded: isExpanded, hovering: hoveringCard === index }"
        :style="getCardStyle(index)"
        @mouseenter="hoveringCard = index"
        @mouseleave="hoveringCard = null"
        @click="navigateToProject(project)"
      >
        <!-- Encabezado de tarjeta: simulation_id y estado de disponibilidad de función -->
        <div class="card-header">
          <span class="card-id">{{ formatSimulationId(project.simulation_id) }}</span>
          <div class="card-status-icons">
            <span 
              class="status-icon" 
              :class="{ available: project.project_id, unavailable: !project.project_id }"
              title="Construcción del grafo"
            >◇</span>
            <span 
              class="status-icon available" 
              title="Configuración del entorno"
            >◈</span>
            <span 
              class="status-icon" 
              :class="{ available: project.report_id, unavailable: !project.report_id }"
              title="Informe de análisis"
            >◆</span>
          </div>
        </div>

        <!-- Área de lista de archivos -->
        <div class="card-files-wrapper">
          <!-- Decoración de esquina - estilo visor -->
          <div class="corner-mark top-left-only"></div>
          
          <!-- Lista de archivos -->
          <div class="files-list" v-if="project.files && project.files.length > 0">
            <div 
              v-for="(file, fileIndex) in project.files.slice(0, 3)" 
              :key="fileIndex"
              class="file-item"
            >
              <span class="file-tag" :class="getFileType(file.filename)">{{ getFileTypeLabel(file.filename) }}</span>
              <span class="file-name">{{ truncateFilename(file.filename, 20) }}</span>
            </div>
            <!-- Si hay más archivos, mostrar aviso -->
            <div v-if="project.files.length > 3" class="files-more">
              +{{ project.files.length - 3 }} archivos más
            </div>
          </div>
          <!-- Placeholder cuando no hay archivos -->
          <div class="files-empty" v-else>
            <span class="empty-file-icon">◇</span>
            <span class="empty-file-text">Sin archivos</span>
          </div>
        </div>

        <!-- Título de tarjeta (usando los primeros 20 caracteres del requisito de simulación) -->
        <h3 class="card-title">{{ getSimulationTitle(project.simulation_requirement) }}</h3>

        <!-- Descripción de tarjeta (mostrar requisito de simulación completo) -->
        <p class="card-desc">{{ truncateText(project.simulation_requirement, 55) }}</p>

        <!-- Pie de tarjeta -->
        <div class="card-footer">
          <div class="card-datetime">
            <span class="card-date">{{ formatDate(project.created_at) }}</span>
            <span class="card-time">{{ formatTime(project.created_at) }}</span>
          </div>
          <span class="card-progress" :class="getProgressClass(project)">
            <span class="status-dot">●</span> {{ formatRounds(project) }}
          </span>
        </div>
        
        <!-- Línea decorativa inferior (se expande al hover) -->
        <div class="card-bottom-line"></div>
      </div>
    </div>

    <!-- Estado de carga -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">Cargando...</span>
    </div>

    <!-- Ventana emergente de detalles de historial -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="selectedProject" class="modal-overlay" @click.self="closeModal">
          <div class="modal-content">
            <!-- Encabezado de ventana emergente -->
            <div class="modal-header">
              <div class="modal-title-section">
                <span class="modal-id">{{ formatSimulationId(selectedProject.simulation_id) }}</span>
                <span class="modal-progress" :class="getProgressClass(selectedProject)">
                  <span class="status-dot">●</span> {{ formatRounds(selectedProject) }}
                </span>
                <span class="modal-create-time">{{ formatDate(selectedProject.created_at) }} {{ formatTime(selectedProject.created_at) }}</span>
              </div>
              <button class="modal-close" @click="closeModal">×</button>
            </div>

            <!-- Contenido de ventana emergente -->
            <div class="modal-body">
              <!-- Requisito de simulación -->
              <div class="modal-section">
                <div class="modal-label">Requisito de simulación</div>
                <div class="modal-requirement">{{ selectedProject.simulation_requirement || 'Sin datos' }}</div>
              </div>

              <!-- Lista de archivos -->
              <div class="modal-section">
                <div class="modal-label">Archivos relacionados</div>
                <div class="modal-files" v-if="selectedProject.files && selectedProject.files.length > 0">
                  <div v-for="(file, index) in selectedProject.files" :key="index" class="modal-file-item">
                    <span class="file-tag" :class="getFileType(file.filename)">{{ getFileTypeLabel(file.filename) }}</span>
                    <span class="modal-file-name">{{ file.filename }}</span>
                  </div>
                </div>
                <div class="modal-empty" v-else>Sin archivos relacionados</div>
              </div>
            </div>

            <!-- Separador de reproducción de simulación -->
            <div class="modal-divider">
              <span class="divider-line"></span>
              <span class="divider-text">Reproducción de simulación</span>
              <span class="divider-line"></span>
            </div>

            <!-- Botones de navegación -->
            <div class="modal-actions">
              <button 
                class="modal-btn btn-project" 
                @click="goToProject"
                :disabled="!selectedProject.project_id"
              >
                <span class="btn-step">Step1</span>
                <span class="btn-icon">◇</span>
                <span class="btn-text">Construcción del grafo</span>
              </button>
              <button 
                class="modal-btn btn-simulation" 
                @click="goToSimulation"
              >
                <span class="btn-step">Step2</span>
                <span class="btn-icon">◈</span>
                <span class="btn-text">Configuración del entorno</span>
              </button>
              <button 
                class="modal-btn btn-report" 
                @click="goToReport"
                :disabled="!selectedProject.report_id"
              >
                <span class="btn-step">Step4</span>
                <span class="btn-icon">◆</span>
                <span class="btn-text">Informe de análisis</span>
              </button>
            </div>
            <!-- Aviso de no reproducible -->
            <div class="modal-playback-hint">
              <span class="hint-text">El Paso 3 «Iniciar simulación» y el Paso 5 «Interacción profunda» deben iniciarse en ejecución activa, no soportan reproducción histórica</span>
            </div>

            <div class="modal-maintenance">
              <button
                class="modal-delete-btn"
                @click="deleteSelectedProject"
                :disabled="!canDeleteSelected || deletingId === selectedProject.simulation_id"
                :title="canDeleteSelected ? 'Eliminar simulación' : 'Detén la simulación antes de eliminarla'"
              >
                <span class="btn-icon">×</span>
                <span>{{ deletingId === selectedProject.simulation_id ? 'Eliminando...' : 'Eliminar simulación' }}</span>
              </button>
              <span v-if="deleteError" class="modal-delete-error">{{ deleteError }}</span>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { deleteSimulation, getSimulationHistory } from '../api/simulation'

const router = useRouter()
const route = useRoute()

// Estado
const projects = ref([])
const loading = ref(true)
const isExpanded = ref(false)
const hoveringCard = ref(null)
const historyContainer = ref(null)
const selectedProject = ref(null)  // Proyecto actualmente seleccionado (para ventana emergente)
const deletingId = ref(null)
const deleteError = ref('')
let observer = null
let isAnimating = false  // Bloqueo de animación para evitar parpadeo
let expandDebounceTimer = null  // Temporizador de debounce
let pendingState = null  // Registrar el estado objetivo pendiente

// Configuración de diseño de tarjetas - ajustar a proporción más ancha
const CARDS_PER_ROW = 4
const CARD_WIDTH = 280  
const CARD_HEIGHT = 280 
const CARD_GAP = 24

// Calcular dinámicamente el estilo de altura del contenedor
const containerStyle = computed(() => {
  if (!isExpanded.value) {
    // Estado colapsado: altura fija
    return { minHeight: '420px' }
  }
  
  // Estado expandido: calcular altura dinámicamente según cantidad de tarjetas
  const total = projects.value.length
  if (total === 0) {
    return { minHeight: '280px' }
  }
  
  const rows = Math.ceil(total / CARDS_PER_ROW)
  // Calcular altura real necesaria: filas * alto de tarjeta + (filas-1) * espaciado + pequeño margen inferior
  const expandedHeight = rows * CARD_HEIGHT + (rows - 1) * CARD_GAP + 10
  
  return { minHeight: `${expandedHeight}px` }
})

const canDeleteSelected = computed(() => {
  if (!selectedProject.value) return false
  return !['running', 'starting'].includes(getNormalizedStatus(selectedProject.value))
})

// Obtener estilo de tarjeta
const getCardStyle = (index) => {
  const total = projects.value.length
  
  if (isExpanded.value) {
    // Estado expandido: diseño de cuadrícula
    const transition = 'transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1), box-shadow 0.3s ease, border-color 0.3s ease'

    const col = index % CARDS_PER_ROW
    const row = Math.floor(index / CARDS_PER_ROW)
    
    // Calcular cantidad de tarjetas por fila para centrar cada fila
    const currentRowStart = row * CARDS_PER_ROW
    const currentRowCards = Math.min(CARDS_PER_ROW, total - currentRowStart)
    
    const rowWidth = currentRowCards * CARD_WIDTH + (currentRowCards - 1) * CARD_GAP
    
    const startX = -(rowWidth / 2) + (CARD_WIDTH / 2)
    const colInRow = index % CARDS_PER_ROW
    const x = startX + colInRow * (CARD_WIDTH + CARD_GAP)
    
    // Expandir hacia abajo, aumentar separación con el título
    const y = 20 + row * (CARD_HEIGHT + CARD_GAP)

    return {
      transform: `translate(${x}px, ${y}px) rotate(0deg) scale(1)`,
      zIndex: 100 + index,
      opacity: 1,
      transition: transition
    }
  } else {
    // Estado colapsado: apilamiento en abanico
    const transition = 'transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1), box-shadow 0.3s ease, border-color 0.3s ease'

    const centerIndex = (total - 1) / 2
    const offset = index - centerIndex
    
    const x = offset * 35
    // Ajustar posición inicial, cerca del título pero con espaciado adecuado
    const y = 25 + Math.abs(offset) * 8
    const r = offset * 3
    const s = 0.95 - Math.abs(offset) * 0.05
    
    return {
      transform: `translate(${x}px, ${y}px) rotate(${r}deg) scale(${s})`,
      zIndex: 10 + index,
      opacity: 1,
      transition: transition
    }
  }
}

// Obtener clase de estilo según progreso de rondas
const getProgressClass = (simulation) => {
  const current = simulation.current_round || 0
  const total = simulation.total_rounds || 0
  const status = getNormalizedStatus(simulation)
  
  if (status === 'failed') {
    return 'failed'
  } else if (['paused', 'stopped'].includes(status)) {
    return 'paused'
  } else if (status === 'running' || status === 'starting') {
    return 'in-progress'
  } else if (status === 'completed' || (total > 0 && current >= total)) {
    return 'completed'
  } else if (total === 0 || current === 0) {
    // No iniciado
    return 'not-started'
  } else {
    // En progreso
    return 'in-progress'
  }
}

const getNormalizedStatus = (simulation) => {
  return (simulation?.runner_status || simulation?.status || '').toLowerCase()
}

// Formatear fecha (mostrar solo la parte de fecha)
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    return date.toISOString().slice(0, 10)
  } catch {
    return dateStr?.slice(0, 10) || ''
  }
}

// Formatear hora (mostrar hora:minuto)
const formatTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    const hours = date.getHours().toString().padStart(2, '0')
    const minutes = date.getMinutes().toString().padStart(2, '0')
    return `${hours}:${minutes}`
  } catch {
    return ''
  }
}

// Truncar texto
const truncateText = (text, maxLength) => {
  if (!text) return ''
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text
}

// Generar título desde el requisito de simulación (primeros 20 caracteres)
const getSimulationTitle = (requirement) => {
  if (!requirement) return 'Simulación sin nombre'
  const title = requirement.slice(0, 20)
  return requirement.length > 20 ? title + '...' : title
}

// Formatear visualización de simulation_id (primeros 6 caracteres)
const formatSimulationId = (simulationId) => {
  if (!simulationId) return 'SIM_UNKNOWN'
  const prefix = simulationId.replace('sim_', '').slice(0, 6)
  return `SIM_${prefix.toUpperCase()}`
}

// Formatear visualización de rondas (ronda actual/total de rondas)
const formatRounds = (simulation) => {
  const current = simulation.current_round || 0
  const total = simulation.total_rounds || 0
  const status = getNormalizedStatus(simulation)
  const roundText = total === 0 ? 'No iniciado' : `${current}/${total} rondas`
  if (status === 'failed') return `Fallida · ${roundText}`
  if (status === 'paused') return `Pausada · ${roundText}`
  if (status === 'stopped') return `Detenida · ${roundText}`
  if (status === 'running' || status === 'starting') return `Activa · ${roundText}`
  if (status === 'completed') return `Completa · ${roundText}`
  if (total === 0) return 'No iniciado'
  return roundText
}

// Obtener tipo de archivo (para estilos)
const getFileType = (filename) => {
  if (!filename) return 'other'
  const ext = filename.split('.').pop()?.toLowerCase()
  const typeMap = {
    'pdf': 'pdf',
    'doc': 'doc', 'docx': 'doc',
    'xls': 'xls', 'xlsx': 'xls', 'csv': 'xls',
    'ppt': 'ppt', 'pptx': 'ppt',
    'txt': 'txt', 'md': 'txt', 'json': 'code',
    'jpg': 'img', 'jpeg': 'img', 'png': 'img', 'gif': 'img',
    'zip': 'zip', 'rar': 'zip', '7z': 'zip'
  }
  return typeMap[ext] || 'other'
}

// Obtener texto de etiqueta de tipo de archivo
const getFileTypeLabel = (filename) => {
  if (!filename) return 'FILE'
  const ext = filename.split('.').pop()?.toUpperCase()
  return ext || 'FILE'
}

// Truncar nombre de archivo (preservar extensión)
const truncateFilename = (filename, maxLength) => {
  if (!filename) return 'Archivo desconocido'
  if (filename.length <= maxLength) return filename
  
  const ext = filename.includes('.') ? '.' + filename.split('.').pop() : ''
  const nameWithoutExt = filename.slice(0, filename.length - ext.length)
  const truncatedName = nameWithoutExt.slice(0, maxLength - ext.length - 3) + '...'
  return truncatedName + ext
}

// Abrir ventana emergente de detalles del proyecto
const navigateToProject = (simulation) => {
  selectedProject.value = simulation
}

// Cerrar ventana emergente
const closeModal = () => {
  selectedProject.value = null
  deleteError.value = ''
}

const deleteSelectedProject = async () => {
  const simulation = selectedProject.value
  if (!simulation || !canDeleteSelected.value || deletingId.value) return

  const confirmed = window.confirm('Eliminar esta simulación del historial?')
  if (!confirmed) return

  deletingId.value = simulation.simulation_id
  deleteError.value = ''

  try {
    const response = await deleteSimulation(simulation.simulation_id)
    if (!response.success) {
      throw new Error(response.error || 'No se pudo eliminar la simulación')
    }

    projects.value = projects.value.filter(project => project.simulation_id !== simulation.simulation_id)
    closeModal()
  } catch (error) {
    deleteError.value = error?.response?.data?.error || error.message || 'No se pudo eliminar'
  } finally {
    deletingId.value = null
  }
}

// Navegar a la página de Construcción del grafo (Project)
const goToProject = () => {
  if (selectedProject.value?.project_id) {
    router.push({
      name: 'Process',
      params: { projectId: selectedProject.value.project_id }
    })
    closeModal()
  }
}

// Navegar a la página de Configuración del entorno (Simulation)
const goToSimulation = () => {
  if (selectedProject.value?.simulation_id) {
    router.push({
      name: 'Simulation',
      params: { simulationId: selectedProject.value.simulation_id }
    })
    closeModal()
  }
}

// Navegar a la página de Informe de análisis (Report)
const goToReport = () => {
  if (selectedProject.value?.report_id) {
    router.push({
      name: 'Report',
      params: { reportId: selectedProject.value.report_id }
    })
    closeModal()
  }
}

// Cargar proyectos históricos
const loadHistory = async () => {
  try {
    loading.value = true
    const response = await getSimulationHistory(20)
    if (response.success) {
      projects.value = response.data || []
    }
  } catch (error) {
    console.error('Error al cargar proyectos históricos:', error)
    projects.value = []
  } finally {
    loading.value = false
  }
}

// Inicializar IntersectionObserver
const initObserver = () => {
  if (observer) {
    observer.disconnect()
  }
  
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const shouldExpand = entry.isIntersecting
        
        // Actualizar el estado objetivo pendiente (registrar siempre el estado más reciente)
        pendingState = shouldExpand
        
        // Limpiar el temporizador de debounce anterior (la nueva intención de scroll sobreescribe la anterior)
        if (expandDebounceTimer) {
          clearTimeout(expandDebounceTimer)
          expandDebounceTimer = null
        }
        
        // Si está en animación, solo registrar estado y procesarlo después
        if (isAnimating) return
        
        // Si el estado objetivo es igual al actual, no es necesario procesarlo
        if (shouldExpand === isExpanded.value) {
          pendingState = null
          return
        }
        
        // Usar debounce para retrasar el cambio de estado y evitar parpadeo rápido
        // Al expandir delay corto (50ms), al colapsar delay mayor (200ms) para mayor estabilidad
        const delay = shouldExpand ? 50 : 200
        
        expandDebounceTimer = setTimeout(() => {
          // Verificar si está en animación
          if (isAnimating) return
          
          // Verificar si el estado pendiente aún necesita ejecutarse (puede haber sido sobreescrito)
          if (pendingState === null || pendingState === isExpanded.value) return
          
          // Establecer bloqueo de animación
          isAnimating = true
          isExpanded.value = pendingState
          pendingState = null
          
          // Desbloquear después de completar la animación y verificar cambios de estado pendientes
          setTimeout(() => {
            isAnimating = false
            
            // Después de la animación, verificar si hay nuevos estados pendientes
            if (pendingState !== null && pendingState !== isExpanded.value) {
              // Esperar un momento antes de ejecutar para evitar cambios demasiado rápidos
              expandDebounceTimer = setTimeout(() => {
                if (pendingState !== null && pendingState !== isExpanded.value) {
                  isAnimating = true
                  isExpanded.value = pendingState
                  pendingState = null
                  setTimeout(() => {
                    isAnimating = false
                  }, 750)
                }
              }, 100)
            }
          }, 750)
        }, delay)
      })
    },
    {
      // Usar múltiples umbrales para detección más suave
      threshold: [0.4, 0.6, 0.8],
      // Ajustar rootMargin, contraer hacia arriba el fondo del viewport para requerir más scroll antes de expandir
      rootMargin: '0px 0px -150px 0px'
    }
  )
  
  // Comenzar observación
  if (historyContainer.value) {
    observer.observe(historyContainer.value)
  }
}

// Escuchar cambios de ruta y recargar datos al volver a la página principal
watch(() => route.path, (newPath) => {
  if (newPath === '/') {
    loadHistory()
  }
})

onMounted(async () => {
  // Asegurar que el DOM esté renderizado antes de cargar datos
  await nextTick()
  await loadHistory()
  
  // Esperar a que el DOM esté renderizado para inicializar el observador
  setTimeout(() => {
    initObserver()
  }, 100)
})

// Si se usa keep-alive, recargar datos al activar el componente
onActivated(() => {
  loadHistory()
})

onUnmounted(() => {
  // Limpiar Intersection Observer
  if (observer) {
    observer.disconnect()
    observer = null
  }
  // Limpiar temporizador de debounce
  if (expandDebounceTimer) {
    clearTimeout(expandDebounceTimer)
    expandDebounceTimer = null
  }
})
</script>

<style scoped>
/* Contenedor */
.history-database {
  position: relative;
  width: 100%;
  min-height: 280px;
  margin-top: 40px;
  padding: 35px 0 40px;
  overflow: visible;
}

/* Visualización simplificada sin proyectos */
.history-database.no-projects {
  min-height: auto;
  padding: 40px 0 20px;
}

/* Fondo de cuadrícula técnica */
.tech-grid-bg {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  overflow: hidden;
  pointer-events: none;
}

/* Usar patrón de fondo CSS para crear cuadrícula de cuadrados con espaciado fijo */
.grid-pattern {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-image: 
    linear-gradient(to right, rgba(0, 0, 0, 0.05) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(0, 0, 0, 0.05) 1px, transparent 1px);
  background-size: 50px 50px;
  /* Posicionar desde la esquina superior izquierda, al cambiar altura solo expandir en la parte inferior */
  background-position: top left;
}

.gradient-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: 
    linear-gradient(to right, rgba(255, 255, 255, 0.9) 0%, transparent 15%, transparent 85%, rgba(255, 255, 255, 0.9) 100%),
    linear-gradient(to bottom, rgba(255, 255, 255, 0.8) 0%, transparent 20%, transparent 80%, rgba(255, 255, 255, 0.8) 100%);
  pointer-events: none;
}

/* Área de título */
.section-header {
  position: relative;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  margin-bottom: 24px;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  padding: 0 40px;
}

.section-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, #E5E7EB, transparent);
  max-width: 300px;
}

.section-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: #9CA3AF;
  letter-spacing: 3px;
  text-transform: uppercase;
}

/* Contenedor de tarjetas */
.cards-container {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 0 40px;
  transition: min-height 700ms cubic-bezier(0.23, 1, 0.32, 1);
  /* min-height calculado dinámicamente por JS según cantidad de tarjetas */
}

/* Tarjeta de proyecto */
.project-card {
  position: absolute;
  width: 280px;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 0;
  padding: 14px;
  cursor: pointer;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  transition: box-shadow 0.3s ease, border-color 0.3s ease, transform 700ms cubic-bezier(0.23, 1, 0.32, 1), opacity 700ms cubic-bezier(0.23, 1, 0.32, 1);
}

.project-card:hover {
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
  border-color: rgba(0, 0, 0, 0.4);
  z-index: 1000 !important;
}

.project-card.hovering {
  z-index: 1000 !important;
}

/* Encabezado de tarjeta */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #F3F4F6;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  font-size: 0.7rem;
}

.card-id {
  color: #6B7280;
  letter-spacing: 0.5px;
  font-weight: 500;
}

/* Grupo de iconos de estado de función */
.card-status-icons {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-icon {
  font-size: 0.75rem;
  transition: all 0.2s ease;
  cursor: default;
}

.status-icon.available {
  opacity: 1;
}

/* Colores para diferentes funciones */
.status-icon:nth-child(1).available { color: #3B82F6; } /* Construcción del grafo - azul */
.status-icon:nth-child(2).available { color: #F59E0B; } /* Configuración del entorno - naranja */
.status-icon:nth-child(3).available { color: #10B981; } /* Informe de análisis - verde */

.status-icon.unavailable {
  color: #D1D5DB;
  opacity: 0.5;
}

/* Visualización del progreso de rondas */
.card-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.5px;
  font-weight: 600;
  font-size: 0.65rem;
}

.status-dot {
  font-size: 0.5rem;
}

/* Colores de estado de progreso */
.card-progress.completed { color: #10B981; }    /* Completado - verde */
.card-progress.in-progress { color: #F59E0B; }  /* En progreso - naranja */
.card-progress.not-started { color: #9CA3AF; }  /* No iniciado - gris */
.card-progress.failed { color: #DC2626; }
.card-progress.paused { color: #6B7280; }
.card-status.pending { color: #9CA3AF; }

/* Área de lista de archivos */
.card-files-wrapper {
  position: relative;
  width: 100%;
  min-height: 48px;
  max-height: 110px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: linear-gradient(135deg, #f8f9fa 0%, #f1f3f4 100%);
  border-radius: 4px;
  border: 1px solid #e8eaed;
  overflow: hidden;
}

.files-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Aviso de más archivos */
.files-more {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3px 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  color: #6B7280;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 3px;
  letter-spacing: 0.3px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  background: rgba(255, 255, 255, 0.7);
  border-radius: 3px;
  transition: all 0.2s ease;
}

.file-item:hover {
  background: rgba(255, 255, 255, 1);
  transform: translateX(2px);
  border-color: #e5e7eb;
}

/* Estilo minimalista de etiquetas de archivo */
.file-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
  padding: 0 4px;
  border-radius: 2px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.55rem;
  font-weight: 600;
  line-height: 1;
  text-transform: uppercase;
  letter-spacing: 0.2px;
  flex-shrink: 0;
  min-width: 28px;
}

/* Esquema de colores de baja saturación - gama Morandi */
.file-tag.pdf { background: #f2e6e6; color: #a65a5a; }
.file-tag.doc { background: #e6eff5; color: #5a7ea6; }
.file-tag.xls { background: #e6f2e8; color: #5aa668; }
.file-tag.ppt { background: #f5efe6; color: #a6815a; }
.file-tag.txt { background: #f0f0f0; color: #757575; }
.file-tag.code { background: #eae6f2; color: #815aa6; }
.file-tag.img { background: #e6f2f2; color: #5aa6a6; }
.file-tag.zip { background: #f2f0e6; color: #a69b5a; }
.file-tag.other { background: #f3f4f6; color: #6b7280; }

.file-name {
  font-family: 'Inter', sans-serif;
  font-size: 0.7rem;
  color: #4b5563;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.1px;
}

/* Placeholder cuando no hay archivos */
.files-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 48px;
  color: #9CA3AF;
}

.empty-file-icon {
  font-size: 1rem;
  opacity: 0.5;
}

.empty-file-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  letter-spacing: 0.5px;
}

/* Efecto del área de archivos al hover */
.project-card:hover .card-files-wrapper {
  border-color: #d1d5db;
  background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
}

/* Decoración de esquina */
.corner-mark.top-left-only {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 8px;
  height: 8px;
  border-top: 1.5px solid rgba(0, 0, 0, 0.4);
  border-left: 1.5px solid rgba(0, 0, 0, 0.4);
  pointer-events: none;
  z-index: 10;
}

/* Título de tarjeta */
.card-title {
  font-family: 'Inter', -apple-system, sans-serif;
  font-size: 0.9rem;
  font-weight: 700;
  color: #111827;
  margin: 0 0 6px 0;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color 0.3s ease;
}

.project-card:hover .card-title {
  color: #2563EB;
}

/* Descripción de tarjeta */
.card-desc {
  font-family: 'Inter', sans-serif;
  font-size: 0.75rem;
  color: #6B7280;
  margin: 0 0 16px 0;
  line-height: 1.5;
  height: 34px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* Pie de tarjeta */
.card-footer {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px solid #F3F4F6;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  color: #9CA3AF;
  font-weight: 500;
}

/* Combinación de fecha y hora */
.card-datetime {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Visualización del progreso de rondas en la parte inferior */
.card-footer .card-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.5px;
  font-weight: 600;
  font-size: 0.65rem;
}

.card-footer .status-dot {
  font-size: 0.5rem;
}

/* Colores de estado de progreso - parte inferior */
.card-footer .card-progress.completed { color: #10B981; }
.card-footer .card-progress.in-progress { color: #F59E0B; }
.card-footer .card-progress.not-started { color: #9CA3AF; }
.card-footer .card-progress.failed { color: #DC2626; }
.card-footer .card-progress.paused { color: #6B7280; }

/* Línea decorativa inferior */
.card-bottom-line {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 2px;
  width: 0;
  background-color: #000;
  transition: width 0.5s cubic-bezier(0.23, 1, 0.32, 1);
  z-index: 20;
}

.project-card:hover .card-bottom-line {
  width: 100%;
}

/* Estado vacío */
.empty-state, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 48px;
  color: #9CA3AF;
}

.empty-icon {
  font-size: 2rem;
  opacity: 0.5;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #E5E7EB;
  border-top-color: #6B7280;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Responsive */
@media (max-width: 1200px) {
  .project-card {
    width: 240px;
  }
}

@media (max-width: 768px) {
  .cards-container {
    padding: 0 20px;
  }
  .project-card {
    width: 200px;
  }
}

/* ===== Estilos de ventana emergente de historial ===== */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  backdrop-filter: blur(4px);
}

.modal-content {
  background: #FFFFFF;
  width: 560px;
  max-width: 90vw;
  max-height: 85vh;
  overflow-y: auto;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
}

/* Transición de animación */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.3s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .modal-content {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.modal-leave-active .modal-content {
  transition: all 0.2s ease-in;
}

.modal-enter-from .modal-content {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

.modal-leave-to .modal-content {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

/* Encabezado de ventana emergente */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 32px;
  border-bottom: 1px solid #F3F4F6;
  background: #FFFFFF;
}

.modal-title-section {
  display: flex;
  align-items: center;
  gap: 16px;
}

.modal-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1rem;
  font-weight: 600;
  color: #111827;
  letter-spacing: 0.5px;
}

.modal-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  background: #F9FAFB;
}

.modal-progress.completed { color: #10B981; background: rgba(16, 185, 129, 0.1); }
.modal-progress.in-progress { color: #F59E0B; background: rgba(245, 158, 11, 0.1); }
.modal-progress.not-started { color: #9CA3AF; background: #F3F4F6; }
.modal-progress.failed { color: #DC2626; background: rgba(220, 38, 38, 0.08); }
.modal-progress.paused { color: #6B7280; background: #F3F4F6; }

.modal-create-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #9CA3AF;
  letter-spacing: 0.3px;
}

.modal-close {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  font-size: 1.5rem;
  color: #9CA3AF;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  border-radius: 6px;
}

.modal-close:hover {
  background: #F3F4F6;
  color: #111827;
}

/* Contenido de ventana emergente */
.modal-body {
  padding: 24px 32px;
}

.modal-section {
  margin-bottom: 24px;
}

.modal-section:last-child {
  margin-bottom: 0;
}

.modal-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #6B7280;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 10px;
  font-weight: 500;
}

.modal-requirement {
  font-size: 0.95rem;
  color: #374151;
  line-height: 1.6;
  padding: 16px;
  background: #F9FAFB;
  border: 1px solid #F3F4F6;
  border-radius: 8px;
}

.modal-files {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

/* Estilos personalizados de barra de desplazamiento */
.modal-files::-webkit-scrollbar {
  width: 4px;
}

.modal-files::-webkit-scrollbar-track {
  background: #F3F4F6;
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb {
  background: #D1D5DB;
  border-radius: 2px;
}

.modal-files::-webkit-scrollbar-thumb:hover {
  background: #9CA3AF;
}

.modal-file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.modal-file-item:hover {
  border-color: #D1D5DB;
  box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
}

.modal-file-name {
  font-size: 0.85rem;
  color: #4B5563;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-empty {
  font-size: 0.85rem;
  color: #9CA3AF;
  padding: 16px;
  background: #F9FAFB;
  border: 1px dashed #E5E7EB;
  border-radius: 6px;
  text-align: center;
}

/* Separador de reproducción de simulación */
.modal-divider {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 32px 0;
  background: #FFFFFF;
}

.divider-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, #E5E7EB, transparent);
}

.divider-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #9CA3AF;
  letter-spacing: 2px;
  text-transform: uppercase;
  white-space: nowrap;
}

/* Botones de navegación */
.modal-actions {
  display: flex;
  gap: 16px;
  padding: 20px 32px;
  background: #FFFFFF;
}

.modal-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  background: #FFFFFF;
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
  overflow: hidden;
}

.modal-btn:hover:not(:disabled) {
  border-color: #000000;
  transform: translateY(-2px);
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.modal-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #F9FAFB;
}

.btn-step {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  font-weight: 500;
  color: #9CA3AF;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.btn-icon {
  font-size: 1.4rem;
  line-height: 1;
  transition: color 0.2s ease;
}

.btn-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #4B5563;
}

.modal-btn.btn-project .btn-icon { color: #3B82F6; }
.modal-btn.btn-simulation .btn-icon { color: #F59E0B; }
.modal-btn.btn-report .btn-icon { color: #10B981; }

.modal-btn:hover:not(:disabled) .btn-text {
  color: #111827;
}

/* Aviso de no reproducible */
.modal-playback-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 32px 20px;
  background: #FFFFFF;
}

.hint-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #9CA3AF;
  letter-spacing: 0.3px;
  text-align: center;
  line-height: 1.5;
}

.modal-maintenance {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 32px 24px;
  background: #FFFFFF;
}

.modal-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid rgba(220, 38, 38, 0.24);
  border-radius: 6px;
  background: #FFFFFF;
  color: #B91C1C;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.modal-delete-btn:hover:not(:disabled) {
  border-color: #DC2626;
  background: rgba(220, 38, 38, 0.06);
}

.modal-delete-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.modal-delete-error {
  color: #DC2626;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  text-align: right;
}

@media (max-width: 768px) {
  .modal-maintenance {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
