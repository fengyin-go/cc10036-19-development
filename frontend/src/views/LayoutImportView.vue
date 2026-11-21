<template>
  <section class="page" data-module="layout_import">
    <header class="page-head">
      <div>
        <h2>布局导入</h2>
        <p class="page-desc">
          解析布局文件里的卡片和口径，按目标业务域重新核验指标粒度，输出带待核项的看板导入数据。
        </p>
      </div>
      <div class="page-actions">
        <label class="btn" :class="{ disabled: importing }">
          {{ importing ? '导入中…' : '选择布局文件' }}
          <input
            type="file"
            accept=".json,.yaml,.yml,application/json,text/yaml"
            data-testid="layout-file"
            class="hidden-input"
            @change="onFileChange"
          />
        </label>
        <button class="btn primary" data-testid="import-sample" type="button" :disabled="importing" @click="importSample">
          载入示例布局
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">已导入布局</span>
        <strong class="stat-value">{{ imports.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前布局卡片</span>
        <strong class="stat-value">{{ current ? current.card_count : 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已核验绑定</span>
        <strong class="stat-value">{{ current ? current.confirmed_count : 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核项</span>
        <strong class="stat-value">{{ current ? current.pending_items.length : 0 }}</strong>
      </article>
    </div>

    <div class="import-layout">
      <aside class="import-list">
        <h3>导入记录</h3>
        <button
          v-for="item in imports"
          :key="item.id"
          type="button"
          class="import-list-item"
          :class="{ active: current && current.id === item.id }"
          :data-testid="`import-${item.id}`"
          @click="selectImport(item.id)"
        >
          <strong>#{{ item.id }} {{ item.dashboard_name }}</strong>
          <span>{{ item.source_domain }} → {{ item.target_domain }}</span>
          <span class="tag" :class="statusClass(item.status)">{{ item.status }}</span>
          <span class="muted">待核 {{ item.pending_count }} · 已核 {{ item.confirmed_count }}</span>
        </button>
        <p v-if="!imports.length" class="empty-state">还没有导入记录，先载入一套布局。</p>
      </aside>

      <div v-if="current" class="import-main">
        <article class="panel">
          <header class="panel-head">
            <div>
              <h3>{{ current.dashboard_name }}</h3>
              <p class="muted">
                {{ current.source_domain }} → {{ current.target_domain }} ·
                刷新 {{ current.refresh_interval || '—' }} · 导入于 {{ current.imported_at }}
              </p>
            </div>
            <span class="tag" :class="statusClass(current.status)">{{ current.status }}</span>
          </header>

          <table class="data-table">
            <thead>
              <tr>
                <th>卡片</th>
                <th>导入指标 / 来源口径</th>
                <th>目标域指标</th>
                <th>实际采用绑定</th>
                <th>核验状态</th>
                <th>待核项</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="card in current.cards" :key="card.card_id" :data-testid="`card-${card.card_id}`">
                <td>
                  <strong>{{ card.title }}</strong>
                  <div class="muted">{{ card.type }} · {{ card.data_summary.row_count }} 行示例</div>
                </td>
                <td>
                  {{ card.metric.name }}
                  <div class="muted">
                    {{ card.metric.code || '无编码' }} · {{ card.metric.granularity || '未声明' }} ·
                    {{ card.metric.aggregator || '未声明' }}
                  </div>
                </td>
                <td>
                  <template v-if="card.binding.target">
                    {{ card.binding.target.code }}
                    <div class="muted">
                      {{ card.binding.target.granularity }} · {{ card.binding.target.aggregator }}
                    </div>
                  </template>
                  <span v-else class="muted">目标域无同名指标</span>
                </td>
                <td>
                  <code>{{ card.binding.effective_binding.metric_code }}</code>
                  <div class="muted">
                    {{ card.binding.effective_binding.granularity }} ·
                    {{ card.binding.effective_binding.aggregator }} ·
                    v{{ card.binding.effective_binding.caliber_version ?? '—' }}
                  </div>
                  <div class="muted">绑定 {{ card.binding.binding_id }}</div>
                </td>
                <td>
                  <span class="tag" :class="statusClass(card.binding.status)">{{ card.binding.status }}</span>
                  <div v-if="card.binding.confirmed_by" class="muted">
                    {{ card.binding.confirmed_by }} · {{ card.binding.confirmed_at }}
                  </div>
                </td>
                <td>
                  <ul v-if="card.binding.pending_items.length" class="pending-list">
                    <li v-for="item in card.binding.pending_items" :key="item.code">
                      <span class="tag warn">{{ item.field }}</span>
                      {{ item.message }}
                    </li>
                  </ul>
                  <span v-else class="muted">无</span>
                </td>
                <td>
                  <form class="confirm-form" @submit.prevent="submitConfirm(card)">
                    <select v-model="confirmForms[card.card_id]" :data-testid="`granularity-${card.card_id}`">
                      <option value="">选择目标粒度</option>
                      <option v-for="unit in granularityOptions(card)" :key="unit" :value="unit">{{ unit }}</option>
                    </select>
                    <button
                      class="btn"
                      type="submit"
                      :data-testid="`confirm-${card.card_id}`"
                      :disabled="!confirmForms[card.card_id] || confirming === card.card_id"
                    >
                      核准粒度
                    </button>
                  </form>
                </td>
              </tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <header class="panel-head">
            <h3>订阅与日报</h3>
            <div class="page-actions">
              <button class="btn primary" data-testid="send-report" type="button" :disabled="sending || !current.subscription" @click="sendNow">
                {{ sending ? '发送中…' : '发送日报' }}
              </button>
            </div>
          </header>
          <p v-if="current.subscription" class="muted">
            订阅 #{{ current.subscription.id }} {{ current.subscription.name }} ·
            {{ current.subscription.schedule }} · {{ current.subscription.channel }} ·
            最近发送 {{ current.subscription.last_sent_at || '—' }}
          </p>
          <p v-else class="muted">该布局没有订阅配置。</p>

          <table class="data-table">
            <thead>
              <tr>
                <th>投递</th>
                <th>报告日期</th>
                <th>已发布 / 不发布</th>
                <th>待核项</th>
                <th>数值</th>
                <th>文件</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="delivery in current.deliveries" :key="delivery.id" :data-testid="`delivery-${delivery.id}`">
                <td>#{{ delivery.id }}<div class="muted">{{ delivery.sent_at }}</div></td>
                <td>{{ delivery.report_date }}</td>
                <td>{{ delivery.published_count }} / {{ delivery.withheld_count }}</td>
                <td>{{ delivery.pending_count }}</td>
                <td>
                  <div v-for="value in delivery.values" :key="value.card_id" class="muted">
                    {{ value.title }}：{{ value.value ?? '未发布' }}（{{ value.publish_state }}）
                  </div>
                </td>
                <td>
                  <button class="link" type="button" :data-testid="`download-${delivery.id}`" @click="downloadDelivery(delivery.id)">
                    {{ delivery.file_name }}
                  </button>
                </td>
              </tr>
              <tr v-if="!current.deliveries.length">
                <td colspan="6" class="empty-state">还没有日报，先发送一次。</td>
              </tr>
            </tbody>
          </table>

          <div v-if="current.deliveries.length >= 2" class="compare-bar">
            <label>
              对比
              <select v-model.number="compareLeft" data-testid="compare-left">
                <option v-for="delivery in current.deliveries" :key="delivery.id" :value="delivery.id">#{{ delivery.id }}</option>
              </select>
            </label>
            <label>
              ↔
              <select v-model.number="compareRight" data-testid="compare-right">
                <option v-for="delivery in current.deliveries" :key="delivery.id" :value="delivery.id">#{{ delivery.id }}</option>
              </select>
            </label>
            <button class="btn" type="button" data-testid="run-compare" @click="runCompare">比较两次日报</button>
          </div>

          <table v-if="comparison" class="data-table" data-testid="compare-result">
            <thead>
              <tr>
                <th>卡片</th>
                <th>#{{ comparison.left.id }} 数值 / 粒度</th>
                <th>#{{ comparison.right.id }} 数值 / 粒度</th>
                <th>已消除待核项</th>
                <th>新增待核项</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in comparison.cards" :key="row.card_id">
                <td>{{ row.title }}</td>
                <td>
                  {{ row.left.value ?? '未发布' }}
                  <div class="muted">{{ row.left.granularity }} · {{ row.left.aggregator }} · {{ row.left.publish_state }}</div>
                </td>
                <td>
                  {{ row.right.value ?? '未发布' }}
                  <div class="muted">{{ row.right.granularity }} · {{ row.right.aggregator }} · {{ row.right.publish_state }}</div>
                </td>
                <td>{{ row.pending_resolved.join('、') || '—' }}</td>
                <td>{{ row.pending_added.join('、') || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <header class="panel-head">
            <h3>日报原文</h3>
            <span v-if="lastSent" class="muted">{{ lastSent.file_name }} · {{ lastSent.content_sha256 }}</span>
          </header>
          <pre v-if="lastSent" class="report-preview" data-testid="report-preview">{{ lastSent.file_content }}</pre>
          <p v-else class="muted">发送日报后在这里查看实际发出的内容。</p>
        </article>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  compareDeliveries,
  confirmBinding,
  getDelivery,
  getLayoutImport,
  importLayout,
  listLayoutImports,
  sendDailyReport,
  uploadLayoutFile,
} from '@/api/layout-import'
import type {
  CompareResult,
  DeliveryDetail,
  ImportCard,
  LayoutImport,
  LayoutImportListItem,
} from '@/data/layout-import'

const imports = ref<LayoutImportListItem[]>([])
const current = ref<LayoutImport | null>(null)
const errorMessage = ref('')
const importing = ref(false)
const sending = ref(false)
const confirming = ref('')
const confirmForms = reactive<Record<string, string>>({})
const lastSent = ref<DeliveryDetail | null>(null)
const comparison = ref<CompareResult | null>(null)
const compareLeft = ref(0)
const compareRight = ref(0)

const GRANULARITY_UNITS = ['小时', '日', '周', '月', '季', '年']

function statusClass(status: string) {
  if (status === '已确认' || status === '已全部核验' || status === '已核验') return 'ok'
  if (status === '待核' || status === '待复核' || status === '待核项' || status === '部分待核') return 'warn'
  return 'danger'
}

function granularityOptions(card: ImportCard): string[] {
  const declared = String(card.binding.target?.granularity ?? '')
  return GRANULARITY_UNITS.filter((unit) => declared.includes(unit)).map((unit) => `按${unit}`)
}

async function refreshList() {
  const payload = await listLayoutImports()
  imports.value = payload.items
}

async function loadImport(importId: number) {
  current.value = await getLayoutImport(importId)
  for (const card of current.value.cards) {
    if (!(card.card_id in confirmForms)) {
      confirmForms[card.card_id] = ''
    }
  }
  const deliveries = current.value.deliveries
  if (deliveries.length >= 2) {
    compareLeft.value = deliveries[deliveries.length - 1].id
    compareRight.value = deliveries[0].id
  }
}

async function selectImport(importId: number) {
  errorMessage.value = ''
  comparison.value = null
  lastSent.value = null
  try {
    await loadImport(importId)
    const latest = current.value?.deliveries[0]
    lastSent.value = latest ? await getDelivery(latest.id) : null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取导入详情失败'
  }
}

async function afterImport(result: LayoutImport) {
  await refreshList()
  await selectImport(result.id)
}

async function importSample() {
  importing.value = true
  errorMessage.value = ''
  try {
    const result = await importLayout({ layout: '' })
    await afterImport(result)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '布局导入失败'
  } finally {
    importing.value = false
  }
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  errorMessage.value = ''
  try {
    const result = await uploadLayoutFile(file)
    await afterImport(result)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '布局文件导入失败'
  } finally {
    importing.value = false
    input.value = ''
  }
}

async function submitConfirm(card: ImportCard) {
  if (!current.value) return
  confirming.value = card.card_id
  errorMessage.value = ''
  try {
    await confirmBinding(current.value.id, card.binding.binding_id, {
      granularity: confirmForms[card.card_id],
    })
    confirmForms[card.card_id] = ''
    await loadImport(current.value.id)
    await refreshList()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑定核验失败'
  } finally {
    confirming.value = ''
  }
}

async function sendNow() {
  if (!current.value) return
  sending.value = true
  errorMessage.value = ''
  try {
    lastSent.value = await sendDailyReport(current.value.id)
    await loadImport(current.value.id)
    await refreshList()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日报发送失败'
  } finally {
    sending.value = false
  }
}

async function downloadDelivery(deliveryId: number) {
  try {
    const detail = await getDelivery(deliveryId)
    const blob = new Blob(['﻿' + detail.file_content.replace(/^﻿/, '')], {
      type: 'text/csv;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = detail.file_name
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日报下载失败'
  }
}

async function runCompare() {
  errorMessage.value = ''
  try {
    comparison.value = await compareDeliveries(compareLeft.value, compareRight.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日报对比失败'
  }
}

onMounted(async () => {
  try {
    await refreshList()
    if (imports.value.length) await selectImport(imports.value[0].id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '布局导入列表读取失败'
  }
})
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.btn.disabled {
  opacity: 0.6;
  pointer-events: none;
}
.hidden-input {
  display: none;
}
.import-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  align-items: start;
}
.import-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.import-list-item {
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
}
.import-list-item.active {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgb(31 111 235 / 15%);
}
.import-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid transparent;
}
.tag.ok {
  background: #e7f6ec;
  color: #15803d;
  border-color: #b7e4c7;
}
.tag.warn {
  background: #fff7e6;
  color: #b45309;
  border-color: #fbdba7;
}
.tag.danger {
  background: #fdecec;
  color: #b42318;
  border-color: #f5c2c0;
}
.pending-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: #7a4d0b;
}
.confirm-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.confirm-form select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
}
.compare-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 12px 0;
  font-size: 12px;
  color: var(--muted);
}
.compare-bar select {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.report-preview {
  margin: 0;
  max-height: 280px;
  overflow: auto;
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>
