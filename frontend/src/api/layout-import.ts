import { request } from './client'
import type {
  CompareResult,
  DeliveryDetail,
  DeliverySummary,
  LayoutImport,
  LayoutImportListItem,
  RecalcResult,
} from '@/data/layout-import'

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = fallback
    try {
      const payload = await response.json()
      if (payload && typeof payload.detail === 'string') detail = payload.detail
    } catch {
      /* 保持默认提示 */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function listLayoutImports(): Promise<{
  ok: boolean
  items: LayoutImportListItem[]
  total: number
}> {
  const response = await request('/api/layout-imports')
  return readJson(response, '布局导入列表读取失败')
}

export async function importLayout(payload: {
  layout: string
  target_domain?: string
  created_by?: string
}): Promise<LayoutImport> {
  const response = await request('/api/layout-imports', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return readJson(response, '布局导入失败')
}

export async function getLayoutImport(importId: number): Promise<LayoutImport> {
  const response = await request(`/api/layout-imports/${importId}`)
  return readJson(response, '布局导入详情读取失败')
}

export async function confirmBinding(
  importId: number,
  bindingId: string,
  payload: { granularity: string; note?: string; metric_code?: string },
): Promise<{ ok: boolean; binding: LayoutImport['bindings'][number] }> {
  const response = await request(
    `/api/layout-imports/${importId}/bindings/${bindingId}/confirm`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  return readJson(response, '绑定核验失败')
}

export async function sendDailyReport(
  importId: number,
  reportDate?: string,
): Promise<DeliveryDetail> {
  const response = await request(`/api/layout-imports/${importId}/send`, {
    method: 'POST',
    body: JSON.stringify({ report_date: reportDate ?? null, trigger: 'manual' }),
  })
  return readJson(response, '日报发送失败')
}

export async function compareDeliveries(
  left: number,
  right: number,
): Promise<CompareResult> {
  const response = await request(
    `/api/layout-imports/deliveries/compare?left=${left}&right=${right}`,
  )
  return readJson(response, '日报对比失败')
}

export async function listDeliveries(importId: number): Promise<{
  ok: boolean
  items: DeliverySummary[]
  total: number
}> {
  const response = await request(`/api/layout-imports/deliveries?import_id=${importId}`)
  return readJson(response, '日报记录读取失败')
}

export async function getDelivery(deliveryId: number): Promise<DeliveryDetail> {
  const response = await request(`/api/layout-imports/deliveries/${deliveryId}`)
  return readJson(response, '日报详情读取失败')
}

export async function changeCaliber(payload: {
  metric_code: string
  caliber: string
  aggregator?: string
  granularity?: string
}): Promise<{ ok: boolean; affected_bindings: unknown[] }> {
  const response = await request('/api/layout-imports/caliber-changes', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return readJson(response, '口径变更失败')
}

export async function recalcMetric(metricCode: string): Promise<RecalcResult> {
  const response = await request(
    `/api/layout-imports/caliber-recalc?metric_code=${encodeURIComponent(metricCode)}`,
  )
  return readJson(response, '重算结果读取失败')
}

export async function uploadLayoutFile(file: File): Promise<LayoutImport> {
  const text = await file.text()
  const response = await request('/api/layout-imports', {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: text,
  })
  return readJson(response, '布局文件导入失败')
}
