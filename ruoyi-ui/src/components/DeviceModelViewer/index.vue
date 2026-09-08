<template>
  <section class="device-model-panel">
    <div class="model-panel-head">
      <div>
        <strong>设备三维视图</strong>
        <span>拖动旋转 · 滚轮缩放 · 右键平移</span>
      </div>
      <el-select
        class="device-select"
        :value="selectedCode"
        size="small"
        placeholder="选择设备"
        :disabled="!devices.length"
        @change="selectDevice"
      >
        <el-option
          v-for="deviceItem in devices"
          :key="deviceItem.id || deviceItem.deviceCode"
          :label="deviceItem.deviceName || deviceItem.deviceCode"
          :value="deviceItem.deviceCode"
        />
      </el-select>
    </div>

    <div class="model-panel-body">
      <div class="model-stage" :class="{ 'is-unavailable': unavailable }">
        <canvas ref="canvas" aria-label="当前设备三维模型" />
        <div v-if="loading" class="model-state">
          <i class="el-icon-loading" />
          <span>正在初始化三维场景…</span>
        </div>
        <div v-else-if="unavailable" class="model-state model-error">
          <i class="el-icon-warning-outline" />
          <strong>当前浏览器无法显示三维模型</strong>
          <span>{{ errorMessage }}</span>
        </div>
        <div v-else class="model-toolbar" aria-label="三维视图控制">
          <el-button size="mini" plain icon="el-icon-refresh-left" @click="resetView">复位</el-button>
          <el-button size="mini" plain :icon="autoRotate ? 'el-icon-video-pause' : 'el-icon-video-play'" @click="toggleAutoRotate">
            {{ autoRotate ? '停止旋转' : '自动旋转' }}
          </el-button>
        </div>
        <span v-if="!unavailable" class="model-badge">THREE.JS 实时渲染</span>
      </div>

      <aside class="model-device-info">
        <template v-if="device">
          <span class="device-state" :class="device.status || 'unknown'"><i />{{ device.statusText || statusText(device.status) }}</span>
          <h3>{{ device.deviceName || device.deviceCode }}</h3>
          <p>{{ device.orgName || device.organization || device.location || '未分配位置' }} · {{ device.deviceCode || '--' }}</p>
          <dl>
            <div><dt>健康指数</dt><dd>{{ percent(device.healthIndex) }}</dd></div>
            <div><dt>振动</dt><dd>{{ metric(device.latestVibration) }}</dd></div>
            <div><dt>温度</dt><dd>{{ metric(device.latestTemperature) }}</dd></div>
            <div><dt>最新采样</dt><dd>{{ formatTime(device.latestSampleTime) }}</dd></div>
          </dl>
          <el-button v-if="canOpenMonitoring" type="primary" plain size="small" @click="openMonitoring">进入实时监测</el-button>
        </template>
        <div v-else class="model-info-empty">暂无可展示的授权设备</div>
        <small class="placeholder-note">当前为摇臂设备示意模型，后续可直接替换为真实 GLB/GLTF 资产。</small>
      </aside>
    </div>
  </section>
</template>

<script>
export default {
  name: 'DeviceModelViewer',
  props: {
    devices: { type: Array, default: () => [] },
    selectedCode: { type: String, default: '' },
    canOpenMonitoring: { type: Boolean, default: false }
  },
  data() {
    return {
      loading: true,
      unavailable: false,
      errorMessage: '请确认浏览器已启用硬件加速与 WebGL。',
      autoRotate: false,
      isVisible: true,
      frameId: null,
      scene: null,
      camera: null,
      renderer: null,
      controls: null,
      model: null,
      statusMaterials: [],
      statusRing: null,
      resizeObserver: null,
      resizeFrameId: null,
      stageWidth: 0,
      stageHeight: 0,
      intersectionObserver: null,
      THREE: null,
      destroyed: false
    }
  },
  computed: {
    device() {
      return this.devices.find(item => item.deviceCode === this.selectedCode) || this.devices[0] || null
    }
  },
  watch: {
    'device.status'() {
      this.applyDeviceStatus()
    }
  },
  mounted() {
    this.autoRotate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    this['\u0024nextTick'](this.initScene)
  },
  beforeDestroy() {
    this.destroyed = true
    this.stopAnimation()
    if (this.resizeFrameId) cancelAnimationFrame(this.resizeFrameId)
    this.resizeFrameId = null
    document.removeEventListener('visibilitychange', this.handleVisibilityChange)
    window.removeEventListener('resize', this.scheduleResize)
    if (this.resizeObserver) this.resizeObserver.disconnect()
    if (this.intersectionObserver) this.intersectionObserver.disconnect()
    if (this.controls) this.controls.dispose()
    if (this.scene) {
      this.scene.traverse(object => {
        if (object.geometry) object.geometry.dispose()
        const materials = Array.isArray(object.material) ? object.material : [object.material]
        materials.filter(Boolean).forEach(material => material.dispose())
      })
    }
    if (this.renderer) {
      this.renderer.dispose()
      this.renderer.forceContextLoss()
    }
  },
  methods: {
    async initScene() {
      try {
        const [THREE, { OrbitControls }] = await Promise.all([
          import(/* webpackChunkName: "three-core" */ 'three'),
          import(/* webpackChunkName: "three-controls" */ 'three/addons/controls/OrbitControls.js')
        ])
        if (this.destroyed) return
        this.THREE = THREE
        const canvas = this['\u0024refs'].canvas
        this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' })
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
        this.renderer.outputColorSpace = THREE.SRGBColorSpace
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping
        this.renderer.toneMappingExposure = 1.05

        this.scene = new THREE.Scene()
        this.scene.fog = new THREE.Fog(0x0e1826, 13, 27)
        this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
        this.camera.position.set(8.8, 5.4, 9.4)

        this.controls = new OrbitControls(this.camera, canvas)
        this.controls.enableDamping = true
        this.controls.dampingFactor = 0.065
        this.controls.autoRotateSpeed = 0.8
        this.controls.minDistance = 7
        this.controls.maxDistance = 18
        this.controls.maxPolarAngle = Math.PI * 0.48
        this.controls.target.set(0, 0.7, 0)

        this.addLights()
        this.createPlaceholderModel()
        this.applyDeviceStatus()
        this.observeStage()
        this.resize()
        document.addEventListener('visibilitychange', this.handleVisibilityChange)
        this.loading = false
        this.startAnimation()
      } catch (error) {
        this.loading = false
        this.unavailable = true
        this.errorMessage = '三维场景初始化失败，请刷新页面或更新浏览器后重试。'
      }
    },
    addLights() {
      const THREE = this.THREE
      this.scene.add(new THREE.HemisphereLight(0xbfefff, 0x07101a, 2.1))
      const keyLight = new THREE.DirectionalLight(0xffffff, 3.2)
      keyLight.position.set(5, 8, 7)
      this.scene.add(keyLight)
      const accentLight = new THREE.PointLight(0x44c7b5, 16, 18)
      accentLight.position.set(-5, 3, -3)
      this.scene.add(accentLight)
    },
    createPlaceholderModel() {
      const THREE = this.THREE
      const group = new THREE.Group()
      const dark = new THREE.MeshStandardMaterial({ color: 0x1c2d3d, metalness: 0.72, roughness: 0.34 })
      const metal = new THREE.MeshStandardMaterial({ color: 0x617486, metalness: 0.86, roughness: 0.25 })
      const accent = new THREE.MeshStandardMaterial({ color: 0x44c7b5, emissive: 0x0d3834, metalness: 0.55, roughness: 0.28 })
      this.statusMaterials = [accent]

      const addBox = (size, position, material, rotation = [0, 0, 0]) => {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material)
        mesh.position.set(...position)
        mesh.rotation.set(...rotation)
        group.add(mesh)
        return mesh
      }
      const addCylinder = (radius, length, position, material, rotation = [0, 0, Math.PI / 2]) => {
        const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 48), material)
        mesh.position.set(...position)
        mesh.rotation.set(...rotation)
        group.add(mesh)
        return mesh
      }

      addBox([6.4, 1.25, 1.7], [0, 0.85, 0], dark, [0, 0, -0.035])
      addBox([3.5, 0.72, 1.95], [0.7, 1.75, 0], accent, [0, 0, -0.035])
      addBox([1.55, 1.7, 1.9], [3.2, 0.9, 0], dark)
      addCylinder(1.58, 1.2, [-3.55, 0.7, 0], metal)
      addCylinder(0.75, 1.55, [3.55, 1.02, 0], metal)
      addCylinder(0.34, 1.95, [3.55, 1.02, 0], accent)

      for (let index = 0; index < 12; index += 1) {
        const angle = (index / 12) * Math.PI * 2
        const tooth = addBox([0.42, 0.3, 0.72], [-3.55, 0.7, 0], dark)
        tooth.position.y += Math.cos(angle) * 1.72
        tooth.position.z += Math.sin(angle) * 1.72
        tooth.rotation.x = angle
      }

      const rail = new THREE.Mesh(new THREE.TorusGeometry(4.9, 0.025, 8, 96), accent)
      rail.rotation.x = Math.PI / 2
      rail.position.y = -0.62
      this.statusRing = rail
      this.scene.add(rail)

      const floor = new THREE.Mesh(
        new THREE.CircleGeometry(5.8, 96),
        new THREE.MeshStandardMaterial({ color: 0x0b1421, metalness: 0.1, roughness: 0.88, transparent: true, opacity: 0.78 })
      )
      floor.rotation.x = -Math.PI / 2
      floor.position.y = -0.67
      this.scene.add(floor)
      const grid = new THREE.GridHelper(11.5, 24, 0x2d5260, 0x1d3040)
      grid.position.y = -0.64
      this.scene.add(grid)

      group.rotation.y = -0.28
      group.position.y = -0.05
      this.model = group
      this.scene.add(group)
    },
    observeStage() {
      if ('ResizeObserver' in window) {
        this.resizeObserver = new ResizeObserver(this.scheduleResize)
        this.resizeObserver.observe(this['\u0024el'].querySelector('.model-stage'))
      } else {
        window.addEventListener('resize', this.scheduleResize)
      }
      if ('IntersectionObserver' in window) {
        this.intersectionObserver = new IntersectionObserver(entries => {
          this.isVisible = Boolean(entries[0] && entries[0].isIntersecting)
          if (this.isVisible) this.startAnimation()
          else this.stopAnimation()
        }, { threshold: 0.05 })
        this.intersectionObserver.observe(this['\u0024el'])
      }
    },
    scheduleResize() {
      if (this.destroyed || this.resizeFrameId) return
      this.resizeFrameId = requestAnimationFrame(() => {
        this.resizeFrameId = null
        this.resize()
      })
    },
    resize() {
      if (!this.renderer || !this.camera) return
      const stage = this['\u0024el'].querySelector('.model-stage')
      if (!stage) return
      const width = Math.max(stage.clientWidth, 1)
      const height = Math.max(stage.clientHeight, 1)
      if (width === this.stageWidth && height === this.stageHeight) return
      this.stageWidth = width
      this.stageHeight = height
      this.renderer.setSize(width, height, false)
      this.camera.aspect = width / height
      this.camera.updateProjectionMatrix()
    },
    animate() {
      this.frameId = null
      if (this.destroyed || !this.isVisible || document.hidden) return
      this.controls.autoRotate = this.autoRotate
      this.controls.update()
      this.renderer.render(this.scene, this.camera)
      this.frameId = requestAnimationFrame(this.animate)
    },
    startAnimation() {
      if (!this.frameId && !this.destroyed && this.isVisible && !document.hidden && this.renderer) {
        this.frameId = requestAnimationFrame(this.animate)
      }
    },
    stopAnimation() {
      if (this.frameId) cancelAnimationFrame(this.frameId)
      this.frameId = null
    },
    handleVisibilityChange() {
      if (document.hidden) this.stopAnimation()
      else this.startAnimation()
    },
    resetView() {
      if (!this.camera || !this.controls) return
      this.camera.position.set(8.8, 5.4, 9.4)
      this.controls.target.set(0, 0.7, 0)
      this.controls.update()
    },
    toggleAutoRotate() {
      this.autoRotate = !this.autoRotate
      this.startAnimation()
    },
    selectDevice(deviceCode) {
      this['\u0024emit']('update:selectedCode', deviceCode)
    },
    openMonitoring() {
      this['\u0024emit']('open-monitoring', this.device)
    },
    applyDeviceStatus() {
      if (!this.THREE || !this.statusMaterials.length) return
      const color = new this.THREE.Color(this.statusColor(this.device && this.device.status))
      this.statusMaterials.forEach(material => {
        material.color.copy(color)
        material.emissive.copy(color).multiplyScalar(0.2)
      })
      if (this.statusRing && this.statusRing.material) {
        this.statusRing.material.color.copy(color)
        this.statusRing.material.emissive.copy(color).multiplyScalar(0.28)
      }
    },
    statusColor(status) {
      if (status === 'normal') return '#10b981'
      if (status === 'level1' || status === 'level2') return '#f59e0b'
      if (/^level[3-5]$/.test(status || '')) return '#ef4444'
      return '#44c7b5'
    },
    statusText(status) {
      if (status === 'normal') return '正常运行'
      if (status === 'stopped') return '已停机'
      if (/^level[1-5]$/.test(status || '')) return status.slice(-1) + '级告警'
      return '状态待确认'
    },
    metric(value) {
      return value === null || value === undefined || value === '' ? '--' : value
    },
    percent(value) {
      return value === null || value === undefined ? '--' : String(value) + '%'
    },
    formatTime(value) {
      if (!value) return '--'
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
    }
  }
}
</script>

<style scoped>
.device-model-panel{margin-bottom:14px;padding:18px;border:1px solid var(--color-border);border-radius:var(--radius-lg);background:var(--color-surface);overflow:hidden}
.model-panel-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:14px}.model-panel-head strong,.model-panel-head span{display:block}.model-panel-head strong{font-size:16px}.model-panel-head span{margin-top:4px;color:var(--color-muted);font-size:12px}.device-select{width:260px}
.model-panel-body{display:grid;grid-template-columns:minmax(0,1.85fr) minmax(270px,.65fr);min-height:390px;border:1px solid var(--color-border);border-radius:var(--radius-md);overflow:hidden;background:var(--color-surface-soft)}
.model-stage{position:relative;min-width:0;min-height:390px;overflow:hidden;background:radial-gradient(circle at 50% 40%,rgba(68,199,181,.11),transparent 42%),linear-gradient(145deg,#0c1725,#08111d)}.model-stage canvas{display:block;width:100%;height:100%;outline:none}.model-stage.is-unavailable canvas{display:none}
.model-state{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;color:var(--color-muted);font-size:13px}.model-state i{font-size:24px;color:var(--color-accent)}.model-error{padding:24px;text-align:center}.model-error strong{color:var(--color-text);font-size:15px}.model-error i{color:var(--color-warning)}
.model-toolbar{position:absolute;left:14px;bottom:14px;display:flex;gap:8px}.model-toolbar .el-button+.el-button{margin-left:0}.model-badge{position:absolute;top:14px;left:14px;padding:5px 8px;border:1px solid var(--color-accent-strong);border-radius:999px;background:rgba(8,17,29,.78);color:var(--color-accent);font:11px var(--font-data);letter-spacing:.04em}
.model-device-info{display:flex;min-width:0;flex-direction:column;padding:24px;border-left:1px solid var(--color-border);background:linear-gradient(180deg,var(--color-surface-raised),var(--color-surface-soft))}.device-state{display:flex;align-items:center;gap:7px;color:var(--color-muted);font-size:12px}.device-state i{width:8px;height:8px;border-radius:50%;background:currentColor}.device-state.normal{color:var(--color-success)}.device-state.level1,.device-state.level2{color:var(--color-warning)}.device-state.level3,.device-state.level4,.device-state.level5{color:var(--color-danger)}.model-device-info h3{margin:14px 0 6px;color:var(--color-heading);font-size:20px}.model-device-info p{margin:0;color:var(--color-muted);font-size:12px;line-height:1.6}.model-device-info dl{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:24px 0}.model-device-info dl div{min-width:0;padding:12px;border:1px solid var(--color-border);border-radius:var(--radius-md);background:var(--color-surface)}.model-device-info dt{color:var(--color-muted);font-size:11px}.model-device-info dd{margin:7px 0 0;overflow:hidden;color:var(--color-text);font:600 16px var(--font-data);text-overflow:ellipsis;white-space:nowrap}.model-device-info .el-button{align-self:flex-start}.model-info-empty{margin:auto;color:var(--color-muted);text-align:center}.placeholder-note{margin-top:auto;padding-top:18px;color:var(--color-muted);font-size:11px;line-height:1.6}
@media(max-width:900px){.model-panel-body{grid-template-columns:1fr}.model-device-info{border-top:1px solid var(--color-border);border-left:0}.model-stage{min-height:340px}.placeholder-note{margin-top:18px}}
@media(max-width:600px){.model-panel-head{align-items:flex-start;flex-direction:column}.device-select{width:100%}.model-panel-body{min-height:0}.model-stage{min-height:300px}.model-device-info{padding:18px}.model-device-info dl{margin:18px 0}.model-toolbar{right:12px;bottom:12px;left:12px}.model-toolbar .el-button{flex:1}}
</style>
