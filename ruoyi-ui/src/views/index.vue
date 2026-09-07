<template>
  <div class="industrial-home">
    <header class="home-hero">
      <div>
        <span class="home-kicker">采煤机摇臂 · 状态监测控制台</span>
        <h1>设备运行总览</h1>
        <p>从设备状态到告警处置，快速进入当前班次的监测任务。</p>
      </div>
      <div class="home-connection" :class="connectionState">
        <i />
        <span>{{ connectionState === 'online' ? '实时数据已连接' : '实时数据连接中断' }}</span>
        <small>最后采样 {{ formatTime(summary.latestSampleTime) }}</small>
      </div>
    </header>

    <section class="home-kpis">
      <article><span>在线设备</span><strong>{{ summary.onlineDevices || 0 }}</strong><small>当前授权范围</small></article>
      <article class="warning"><span>异常设备</span><strong>{{ summary.abnormalDevices || 0 }}</strong><small>需要优先复核</small></article>
      <article class="danger"><span>待处置告警</span><strong>{{ summary.unacknowledgedAlarms || 0 }}</strong><small>等待确认或指派</small></article>
      <article><span>数据延迟</span><strong>{{ delayText }}</strong><small>最后采样至今</small></article>
    </section>

    <device-model-viewer
      :devices="sortedDevices"
      :selected-code.sync="selectedDeviceCode"
      :can-open-monitoring="hasPermission('sensor:monitoring:view')"
      @open-monitoring="openDevice"
    />

    <section class="home-panel device-overview-panel">
      <div class="panel-head device-overview-head">
        <div><strong>设备状态总览</strong><span>按告警优先级排列，共 {{ summary.totalDevices || devices.length }} 台授权设备</span></div>
        <div class="device-overview-actions">
          <el-button v-if="canExpandDevices" size="mini" plain @click="showAllDevices = !showAllDevices">{{ showAllDevices ? '收起设备' : `查看全部 ${devices.length} 台` }}</el-button>
          <el-button v-if="hasPermission('sensor:monitoring:view')" size="mini" type="primary" plain @click="openPath('/monitoring-center/index')">进入实时监测</el-button>
        </div>
      </div>
      <div v-if="overviewLoading" class="home-empty">正在加载授权设备状态…</div>
      <div v-else-if="overviewError" class="home-empty home-error">{{ overviewError }}</div>
      <div v-else-if="visibleDevices.length" class="device-card-grid">
        <button
          v-for="device in visibleDevices"
          :key="device.id || device.deviceCode"
          type="button"
          class="device-overview-card"
          :class="device.status || 'unknown'"
          :aria-label="`进入 ${device.deviceName || device.deviceCode} 的实时监测`"
          @click="openDevice(device)"
        >
          <span class="device-status-line"><i /><span>{{ device.statusText || statusText(device.status) }}</span><small v-if="device.telemetryFreshness !== 'realtime'">{{ freshnessText(device.telemetryFreshness) }}</small></span>
          <strong class="device-name">{{ device.deviceName || device.deviceCode }}</strong>
          <small class="device-context">{{ device.orgName || device.organization || device.location || '未分配位置' }} · {{ device.deviceCode || '--' }}</small>
          <div class="device-health"><span>健康指数</span><strong>{{ percent(device.healthIndex) }}</strong></div>
          <div class="device-metrics">
            <span><small>振动</small><strong>{{ metric(device.latestVibration) }}</strong></span>
            <span><small>温度</small><strong>{{ metric(device.latestTemperature) }}</strong></span>
          </div>
          <small class="device-sample">最新采样 {{ formatTime(device.latestSampleTime) }}</small>
        </button>
      </div>
      <div v-else class="home-empty">当前授权范围暂无设备，可先在 PHM 配置管理中建立设备授权与测点关系。</div>
    </section>

    <main class="home-grid">
      <section class="home-panel workflow-panel">
        <div class="panel-head"><div><strong>值班工作入口</strong><span>按现场处置顺序进入功能</span></div></div>
        <div class="workflow-list">
          <button v-for="item in availableWorkflows" :key="item.path" type="button" @click="openWorkflow(item)">
            <i :class="item.icon" /><span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span><i class="el-icon-arrow-right" />
          </button>
          <div v-if="!availableWorkflows.length" class="home-empty workflow-empty">当前账号暂无可用的值班功能，请联系管理员分配菜单权限。</div>
        </div>
      </section>

      <section class="home-panel alarm-panel">
        <div class="panel-head"><div><strong>最近待处置告警</strong><span>先确认，再执行现场处置</span></div><el-button v-if="hasPermission('phm:alarm:list')" size="mini" type="text" @click="openPath('/phm/alarms')">查看全部</el-button></div>
        <button v-for="alarm in visibleAlarms" :key="alarm.id" type="button" class="alarm-row" @click="openAlarm(alarm)">
          <i :class="alarm.alarmLevel >= 3 ? 'is-danger' : 'is-warning'" /><span><strong>{{ alarm.pointName || alarm.deviceName || '设备告警' }}</strong><small>{{ alarm.diagnosisResult || '监测指标触发告警规则' }} · {{ formatTime(alarm.alarmTime) }}</small></span><i class="el-icon-arrow-right" />
        </button>
        <div v-if="!visibleAlarms.length" class="home-empty">{{ alarmEmptyText }}</div>
      </section>
    </main>
  </div>
</template>

<script>
import { mapState } from 'vuex'
import DeviceModelViewer from '@/components/DeviceModelViewer'

export default {
  name: 'Index',
  components: { DeviceModelViewer },
  data() {
    return {
      overviewLoading: false,
      overviewError: '',
      showAllDevices: false,
      selectedDeviceCode: '',
      workflows: [
        { path: '/monitoring-center/index', permission: 'sensor:monitoring:view', icon: 'el-icon-data-line', title: '实时监测', description: '查看设备、测点和最新采样质量' },
        { path: '/monitoring-center/vibration', permission: 'sensor:vibration:list', icon: 'el-icon-pie-chart', title: '振动分析', description: '查看趋势、波形、频谱和文件数据' },
        { path: '/analysis-toolkit/bearing-diagnosis', permission: 'sensor:diagnosis:view', icon: 'el-icon-cpu', title: '模型诊断', description: '选择模型版本并执行可追溯诊断' },
        { path: '/phm/alarms', permission: 'phm:alarm:list', icon: 'el-icon-warning-outline', title: '告警处置', description: '确认、指派、处理和关闭告警' }
      ]
    }
  },
  computed: {
    ...mapState('monitoring', ['overview', 'connectionState']),
    summary() { return this.overview.summary || {} },
    alarms() { return this.overview.alarms || [] },
    devices() { return this.overview.devices || [] },
    sortedDevices() {
      return [...this.devices].sort((left, right) => this.statusWeight(left.status) - this.statusWeight(right.status) || String(left.deviceCode || '').localeCompare(String(right.deviceCode || '')))
    },
    visibleDevices() {
      return this.showAllDevices ? this.sortedDevices : this.sortedDevices.slice(0, 8)
    },
    canExpandDevices() { return this.devices.length > 8 },
    visibleAlarms() {
      return this.canOpenAlarmDetails ? this.alarms.slice(0, 4) : []
    },
    canOpenAlarmDetails() {
      return this.hasPermission('phm:alarm:list') && this.hasPermission('phm:alarm:query')
    },
    alarmEmptyText() {
      if (!this.hasPermission('phm:alarm:list')) return '当前账号暂无告警查看权限。'
      if (!this.hasPermission('phm:alarm:query')) return '当前账号暂无告警详情权限。'
      return '当前没有待处置告警。'
    },
    availableWorkflows() {
      return this.workflows.filter(item => this.hasPermission(item.permission))
    },
    delayText() {
      const value = this.summary.dataDelaySeconds
      if (value === null || value === undefined) return '--'
      return Number(value) < 60 ? `${value} 秒` : `${Math.floor(value / 60)} 分钟`
    }
  },
  watch: {
    sortedDevices: {
      immediate: true,
      handler(devices) {
        if (!devices.length) {
          this.selectedDeviceCode = ''
          return
        }
        if (!devices.some(device => device.deviceCode === this.selectedDeviceCode)) {
          this.selectedDeviceCode = devices[0].deviceCode
        }
      }
    }
  },
  async created() {
    if (!this.hasPermission('sensor:monitoring:view')) return
    this.overviewLoading = true
    this.overviewError = ''
    try {
      await this.$store.dispatch('monitoring/loadOverview')
      this.$store.dispatch('monitoring/connect')
    } catch (error) {
      this.overviewError = '设备总览暂时不可用，请检查服务状态后刷新重试。'
    } finally {
      this.overviewLoading = false
    }
  },
  beforeDestroy() {
    this.$store.dispatch('monitoring/disconnect')
  },
  methods: {
    hasPermission(permission) {
      const permissions = this.$store.getters.permissions || []
      return permissions.includes('*:*:*') || permissions.includes(permission)
    },
    openWorkflow(item) {
      if (!item || !this.hasPermission(item.permission)) return
      this.openPath(item.path)
    },
    openPath(path) {
      if (path) this.$router.push(path)
    },
    openAlarm(alarm) {
      if (!this.canOpenAlarmDetails || !alarm || !alarm.id) return
      this.$router.push({ path: '/phm/alarms', query: { id: alarm.id } })
    },
    openDevice(device) {
      if (!device || !device.deviceCode || !this.hasPermission('sensor:monitoring:view')) return
      this.$router.push({ path: '/monitoring-center/index', query: { deviceCode: device.deviceCode } })
    },
    statusWeight(status) {
      const weights = { level5: 0, level4: 1, level3: 2, level2: 3, level1: 4, stopped: 5, normal: 6 }
      return weights[status] === undefined ? 7 : weights[status]
    },
    statusText(status) {
      if (status === 'normal') return '正常运行'
      if (status === 'stopped') return '已停机'
      if (/^level[1-5]$/.test(status || '')) return `${status.slice(-1)}级告警`
      return '状态待确认'
    },
    freshnessText(value) {
      if (value === 'delayed') return '数据滞后'
      if (value === 'offline') return '无实时数据'
      return ''
    },
    metric(value) {
      return value === null || value === undefined || value === '' ? '--' : value
    },
    percent(value) {
      return value === null || value === undefined ? '--' : `${value}%`
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
.industrial-home{min-height:calc(100vh - 84px);padding:24px;color:var(--color-text);background:var(--color-canvas)}
.home-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:6px 0 22px;border-bottom:1px solid var(--color-border)}
.home-kicker{color:var(--color-accent);font-size:12px;letter-spacing:.08em}.home-hero h1{margin:8px 0 6px;font-size:30px;letter-spacing:.02em}.home-hero p{margin:0;color:var(--color-muted);font-size:14px}
.home-connection{display:grid;grid-template-columns:10px auto;align-items:center;gap:7px;color:var(--color-success);font-size:13px}.home-connection i{width:8px;height:8px;border-radius:50%;background:currentColor}.home-connection small{grid-column:2;color:var(--color-muted)}.home-connection.offline{color:var(--color-danger)}
.home-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.home-kpis article,.home-panel{border:1px solid var(--color-border);border-radius:var(--radius-lg);background:var(--color-surface);box-shadow:none}.home-kpis article{display:grid;gap:8px;padding:16px;border-top:2px solid var(--color-accent)}.home-kpis article.warning{border-top-color:var(--color-warning)}.home-kpis article.danger{border-top-color:var(--color-danger)}.home-kpis span,.home-kpis small{color:var(--color-muted);font-size:12px}.home-kpis strong{font:600 30px/1 var(--font-data);color:var(--color-accent)}.home-kpis article.warning strong{color:var(--color-warning)}.home-kpis article.danger strong{color:var(--color-danger)}
.device-overview-panel{margin-bottom:14px}.home-grid{display:grid;grid-template-columns:1.1fr 1fr;gap:14px}.home-panel{min-height:250px;padding:18px}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.panel-head strong{display:block;font-size:16px}.panel-head span{display:block;margin-top:4px;color:var(--color-muted);font-size:12px}.device-overview-actions{display:flex;gap:8px;flex:none}.device-card-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.device-overview-card{position:relative;display:flex;min-height:220px;flex-direction:column;align-items:stretch;padding:15px;border:1px solid var(--color-border);border-left:4px solid var(--color-muted);border-radius:var(--radius-md);background:var(--color-surface-soft);color:var(--color-text);text-align:left;cursor:pointer;transition:border-color .16s ease,transform .16s ease,background .16s ease}.device-overview-card:hover,.device-overview-card:focus-visible{border-color:var(--color-accent-strong);border-left-color:var(--color-accent);background:var(--color-surface-raised);outline:none;transform:translateY(-2px)}.device-overview-card.normal{border-left-color:var(--color-success)}.device-overview-card.level1,.device-overview-card.level2{border-left-color:var(--color-warning)}.device-overview-card.level3,.device-overview-card.level4,.device-overview-card.level5{border-left-color:var(--color-danger)}.device-status-line{display:flex;align-items:center;gap:6px;color:var(--color-muted);font-size:12px}.device-status-line i{width:7px;height:7px;border-radius:50%;background:var(--color-muted)}.normal .device-status-line i{background:var(--color-success)}.level1 .device-status-line i,.level2 .device-status-line i{background:var(--color-warning)}.level3 .device-status-line i,.level4 .device-status-line i,.level5 .device-status-line i{background:var(--color-danger)}.device-status-line small{margin-left:auto;color:var(--color-warning);white-space:nowrap}.device-name{margin-top:14px;overflow:hidden;font-size:16px;text-overflow:ellipsis;white-space:nowrap}.device-context,.device-sample{margin-top:5px;color:var(--color-muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.device-health{display:flex;align-items:baseline;justify-content:space-between;margin-top:18px;padding-top:10px;border-top:1px solid var(--color-border)}.device-health span,.device-metrics small{color:var(--color-muted);font-size:12px}.device-health strong{color:var(--color-accent);font:600 22px var(--font-data)}.device-metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.device-metrics span{min-width:0}.device-metrics small,.device-metrics strong{display:block}.device-metrics strong{margin-top:4px;font:600 16px var(--font-data);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.device-sample{margin-top:auto;padding-top:14px}.workflow-list,.alarm-row{display:grid;gap:8px}.workflow-list button,.alarm-row{display:grid;grid-template-columns:28px minmax(0,1fr) 16px;align-items:center;gap:12px;width:100%;padding:12px;border:1px solid transparent;border-radius:var(--radius-md);background:var(--color-surface-soft);color:var(--color-text);text-align:left;cursor:pointer}.workflow-list button:hover,.alarm-row:hover{border-color:var(--color-accent-strong);background:var(--color-surface-raised)}.workflow-list button>i:first-child{color:var(--color-accent);font-size:18px}.workflow-list span,.alarm-row span{min-width:0}.workflow-list strong,.workflow-list small,.alarm-row strong,.alarm-row small{display:block}.workflow-list small,.alarm-row small{margin-top:4px;color:var(--color-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.home-empty{padding:28px 8px;color:var(--color-muted);text-align:center}.home-error{color:var(--color-warning)}.workflow-empty{padding:16px 8px}.alarm-row{grid-template-columns:9px minmax(0,1fr) 16px;margin-bottom:8px}.alarm-row>i:first-child{width:9px;height:9px;border-radius:50%;background:var(--color-warning)}.alarm-row>i.is-danger{background:var(--color-danger)}.alarm-row>i.el-icon-arrow-right{width:auto;height:auto;background:none;color:var(--color-muted)}
@media(max-width:1280px){.device-card-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:900px){.device-card-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.home-grid{grid-template-columns:1fr 1fr}}@media(max-width:720px){.industrial-home{padding:16px}.home-hero,.device-overview-head{align-items:flex-start;flex-direction:column}.home-kpis{grid-template-columns:repeat(2,1fr)}.device-overview-actions{width:100%;justify-content:space-between}.device-card-grid,.home-grid{grid-template-columns:1fr}.device-overview-card{min-height:205px}}@media(prefers-reduced-motion:reduce){.workflow-list button,.alarm-row,.device-overview-card{transition:none}.device-overview-card:hover,.device-overview-card:focus-visible{transform:none}}
</style>
