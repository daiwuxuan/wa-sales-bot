/**
 * 后端 API 客户端。
 *
 * 通过 next.config.ts 的 rewrites 代理到 FastAPI，
 * 所以这里用相对路径就行 —— 同源，没有 CORS，也不用配后端地址。
 */

const BASE = "";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json; charset=utf-8" }),
      ...init?.headers,
    },
    cache: "no-store",
  });

  const text = await response.text();
  const data = text ? safeParse(text) : null;

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractDetail(data) ?? text ?? `HTTP ${response.status}`,
    );
  }

  return data as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** FastAPI 的错误体是 {detail: "..."}，校验失败时 detail 是数组 */
function extractDetail(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const detail = record.detail ?? record.message;
  if (typeof detail === "string") return detail;
  if (detail) return JSON.stringify(detail);
  return null;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", body: form });
  },
};

// ── 类型 ────────────────────────────────────────────────
export type Variant = {
  id: number;
  variant_sku: string;
  color_code: string;
  color_label: string;
  size_code: string;
  size_label: string;
  list_price_cents: number;
  list_price_display: string;
  floor_price_cents: number;
  floor_price_display: string;
  qty_on_hand: number;
  qty_reserved: number;
  available_qty: number;
  is_active: boolean;
};

export type Product = {
  id: number;
  sku: string;
  name: string;
  description: string | null;
  category: string | null;
  image_url: string | null;
  image_url_absolute: string | null;
  is_active: boolean;
  variant_count: number;
  total_stock: number;
  variants: Variant[];
};

export type OrderItemView = {
  sku: string;
  name: string;
  color: string;
  size: string;
  qty: number;
  unit_price_display: string;
  floor_price_display: string;
  line_total_display: string;
};

export type Order = {
  order_no: string;
  status: string;
  source: string;
  customer_phone: string;
  customer_name: string | null;
  total_cents: number;
  total_display: string;
  created_at: string;
  items: OrderItemView[];
};

export type Conversation = {
  id: number;
  phone: string;
  customer_name: string | null;
  tier: string | null;
  ai_enabled: boolean;
  assigned_to: string | null;
  takeover_reason: string | null;
  last_inbound_at: string | null;
};

export type ChatMessage = {
  id: number;
  direction: "in" | "out";
  type: string;
  body: string | null;
  media_url: string | null;
  sent_by: string;
  created_at: string;
};

export type TurnStep = {
  step: number;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error_code: string | null;
  duration_ms: number;
  result?: Record<string, unknown>;
};

export type LlmMode = {
  mode: string;
  model: string;
  configured_mode: string;
  has_api_key: boolean;
  probe: { ok: boolean; message: string } | null;
};

export type ReportSummary = {
  range_days: number;
  gmv_display: string;
  order_count: number;
  avg_order_display: string;
  inquiry_conversion_pct: number;
  quote_conversion_pct: number;
  repeat_rate_pct: number;
  repeat_customers: number;
  human_takeover_count: number;
  conversation_customers: number;
};

export type TopSku = {
  sku: string;
  name: string;
  units: number;
  revenue_cents: number;
  revenue_display: string;
  orders: number;
};

export type PushRecommendation = {
  id: number;
  customer_id: number;
  customer_name: string | null;
  phone: string | null;
  tier: string | null;
  interest: string | null;
  history_summary: string | null;
  recommended_sku: string | null;
  recommended_name: string | null;
  image_url: string | null;
  ai_copy: string | null;
  edited_copy: string | null;
  status: string;
  created_at: string;
};

export type Campaign = {
  id: number;
  name: string;
  message_text: string;
  image_url: string | null;
  status: string;
  total_targets: number;
  sent_count: number;
  failed_count: number;
  created_at: string;
};
