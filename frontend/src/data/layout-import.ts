/** 布局导入接口的返回结构：与后端 app/services/layout_import.py 保持一致。 */

export type PendingItem = {
  binding_id?: string
  card_id?: string
  code: string
  field: string
  message: string
  imported: unknown
  target: unknown
}

export type EffectiveBinding = {
  metric_code: string | null
  metric_name: string
  granularity: string
  aggregator: string | null
  caliber_version: number | null
  verification_status: string
}

export type ValuePreview = {
  value: number | null
  bucket_count?: number
  bucket_values?: number[]
  note: string
}

export type Binding = {
  binding_id: string
  import_id: number
  card_id: string
  card_title: string
  metric_name: string
  source_metric_code: string
  target_metric_code: string | null
  source: Record<string, string>
  target: Record<string, string | number | null> | null
  status: string
  match_basis: string
  name_only_match: boolean
  confirmation_basis: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  confirmed_granularity: string | null
  confirmed_aggregator: string | null
  confirmed_caliber_version: number | null
  stale: boolean
  pending_items: PendingItem[]
  resolved_items: PendingItem[]
  effective_binding: EffectiveBinding
  preview?: {
    imported: ValuePreview
    target: ValuePreview | null
    effective: ValuePreview
    effective_binding: EffectiveBinding
  }
}

export type ImportCard = {
  card_id: string
  title: string
  type: string
  layout: { w: number; h: number; x: number; y: number }
  metric: Record<string, string>
  data_summary: {
    row_count: number
    time_field: string
    value_field: string
    unit: string
  }
  import_claims: Record<string, unknown>
  binding: Binding
}

export type Subscription = {
  id: number
  import_id: number
  name: string
  report_object: string
  target_domain: string
  schedule: string
  channel: string
  recipients: string
  format: string
  status: string
  last_sent_at: string | null
  last_delivery_id?: number
}

export type DeliverySummary = {
  id: number
  subscription_id: number
  subscription_name: string
  import_id: number
  dashboard_name: string
  target_domain: string
  report_date: string
  sent_at: string
  file_name: string
  published_count: number
  withheld_count: number
  pending_count: number
  values: {
    card_id: string
    title: string
    binding_id: string
    value: number | null
    publish_state: string
  }[]
}

export type DeliveryDetail = DeliverySummary & {
  trigger: string
  file_path: string
  file_size: number
  content_sha256: string
  file_url: string
  file_content: string
  rows: Record<string, unknown>[]
  binding_snapshot: Record<string, unknown>[]
  status: string
  summary: DeliverySummary
}

export type LayoutImport = {
  ok: boolean
  id: number
  dashboard_name: string
  source_domain: string
  target_domain: string
  refresh_interval: string
  source_file: string
  imported_at: string
  status: string
  dashboard_layout_id: number | null
  card_count: number
  pending_count: number
  confirmed_count: number
  cards: ImportCard[]
  pending_items: PendingItem[]
  bindings: Binding[]
  subscription: Subscription | null
  deliveries: DeliverySummary[]
}

export type LayoutImportListItem = {
  id: number
  dashboard_name: string
  source_domain: string
  target_domain: string
  status: string
  card_count: number
  pending_count: number
  confirmed_count: number
  imported_at: string
  subscription_id: number | null
}

export type CompareResult = {
  ok: boolean
  left: DeliverySummary
  right: DeliverySummary
  cards: {
    card_id: string
    title: string
    metric_name: string
    left: {
      delivery_id: number
      binding_id: string
      granularity: string
      aggregator: string
      caliber_version: number | null
      value: number | null
      publish_state: string
      pending_item_codes: string[]
      pending_items: string[]
    }
    right: {
      delivery_id: number
      binding_id: string
      granularity: string
      aggregator: string
      caliber_version: number | null
      value: number | null
      publish_state: string
      pending_item_codes: string[]
      pending_items: string[]
    }
    value_changed: boolean
    pending_resolved: string[]
    pending_added: string[]
  }[]
  summary: {
    left_published: number
    right_published: number
    left_pending: number
    right_pending: number
  }
}

export type RecalcResult = {
  ok: boolean
  metric_code: string
  metric_name: string
  current_caliber_version: number
  current_granularity: string
  current_aggregator: string
  binding_count: number
  bindings: {
    binding_id: string
    import_id: number
    card_id: string
    metric_name: string
    status: string
    stale: boolean
    metric_code: string | null
    granularity: string
    aggregator: string | null
    caliber_version: number | null
    recalculated_value: number | null
    publish_state: string
    used_by_deliveries: {
      delivery_id: number
      report_date: string
      file_name: string
      value: number | null
      publish_state: string
      granularity: string
      aggregator: string
      caliber_version: number | null
      binding_id: string
    }[]
  }[]
}
