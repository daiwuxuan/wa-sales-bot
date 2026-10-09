"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/*
 * 图表约定（整套一致，不要单个文件里破例）：
 *   · 绝不使用双 Y 轴。量纲不同的两个指标拆成上下两张共享 X 轴的小倍数图。
 *   · 单系列不放图例 —— 标题已经说明了它是什么。>= 2 系列必须有图例。
 *   · 网格和坐标轴取弱色，数据是主角。
 *   · 数值直接标注在条形末端（这也是 aqua 色在浅底上对比度不足时的补偿手段）。
 *   · 颜色一律走 CSS 变量，深浅色各自一套验证过的步进值。
 */

const AXIS_TICK = { fill: "var(--axis)", fontSize: 11 } as const;
const GRID_COLOR = "var(--grid)";

/** Recharts 的 tooltip 载荷条目。只声明我们实际读的字段。 */
type TooltipEntry = {
  dataKey?: string | number;
  name?: string;
  value?: number;
  color?: string;
  fill?: string;
  payload?: Record<string, unknown>;
};

type TooltipBoxProps = {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  formatter?: (value: number, entry: TooltipEntry) => string;
};

function TooltipBox({ active, payload, label, formatter }: TooltipBoxProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 shadow-lg">
      {label !== undefined && (
        <div className="mb-1 text-[11px] text-muted">{label}</div>
      )}
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center gap-2 text-xs">
          <span
            className="h-2 w-2 shrink-0 rounded-sm"
            style={{ background: entry.color ?? entry.fill }}
          />
          <span className="text-muted">{entry.name}</span>
          <span className="ml-auto font-medium text-text tabular">
            {formatter && entry.value !== undefined
              ? formatter(entry.value, entry)
              : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 成交额趋势。单系列面积图，颜色走序列蓝。 */
export function GmvTrendChart({
  data,
}: {
  data: { date: string; gmv_cents: number; orders: number }[];
}) {
  const series = data.map((d) => ({ ...d, gmv: d.gmv_cents / 100 }));

  return (
    <ResponsiveContainer width="100%" height={160}>
      <AreaChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: -14 }}>
        <defs>
          <linearGradient id="gmvFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.22} />
            <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID_COLOR} vertical={false} />
        <XAxis
          dataKey="date"
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: GRID_COLOR }}
          minTickGap={24}
        />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={48} />
        <Tooltip
          content={<TooltipBox formatter={(v) => `¥${v.toFixed(2)}`} />}
          cursor={{ stroke: "var(--axis)", strokeDasharray: "3 3" }}
        />
        <Area
          type="monotone"
          dataKey="gmv"
          name="成交额"
          stroke="var(--series-1)"
          strokeWidth={2}
          fill="url(#gmvFill)"
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/**
 * 订单量趋势。
 * 刻意和成交额拆成两张独立图表共享同一时间轴 ——
 * 金额和笔数量纲不同，画成双 Y 轴会让两条线的交叉点看起来有意义，其实没有。
 */
export function OrderTrendChart({
  data,
}: {
  data: { date: string; gmv_cents: number; orders: number }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={120}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -14 }}>
        <CartesianGrid stroke={GRID_COLOR} vertical={false} />
        <XAxis
          dataKey="date"
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: GRID_COLOR }}
          minTickGap={24}
        />
        <YAxis
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={48}
          allowDecimals={false}
        />
        <Tooltip
          content={<TooltipBox formatter={(v) => `${v} 单`} />}
          cursor={{ fill: "var(--surface-2)" }}
        />
        <Bar
          dataKey="orders"
          name="订单量"
          fill="var(--series-1)"
          radius={[4, 4, 0, 0]}
          maxBarSize={22}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

/**
 * 转化漏斗。
 * 用水平条形而不是漏斗形状 —— 漏斗图中间那段的面积没有可读的刻度，
 * 而条形图的长度是可以直接比较的。
 * 颜色取单色有序阶（浅 -> 深），这是「有序」而不是「分类」。
 */
const FUNNEL_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab"];

export function FunnelChart({
  steps,
}: {
  steps: { key: string; label: string; value: number }[];
}) {
  const max = Math.max(...steps.map((s) => s.value), 1);
  const base = steps[0]?.value ?? 0;

  return (
    <div className="space-y-2.5">
      {steps.map((step, index) => {
        const pct = base ? Math.round((step.value * 100) / base) : 0;
        return (
          <div key={step.key}>
            <div className="mb-1 flex items-baseline justify-between text-xs">
              <span className="text-muted">{step.label}</span>
              <span className="tabular text-text">
                {step.value}
                {index > 0 && (
                  <span className="ml-1.5 text-[11px] text-faint">{pct}%</span>
                )}
              </span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max((step.value / max) * 100, step.value > 0 ? 3 : 0)}%`,
                  background: FUNNEL_RAMP[Math.min(index, FUNNEL_RAMP.length - 1)],
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Top SKU 销售额。单系列 -> 单一色相，不需要图例。
 * 数值直接标在条形末端，不需要悬停就能读数。
 */
export function TopSkuChart({
  items,
}: {
  items: { sku: string; name: string; revenue_cents: number; units: number }[];
}) {
  const data = items.map((i) => ({
    label: i.name.length > 10 ? `${i.name.slice(0, 10)}…` : i.name,
    revenue: i.revenue_cents / 100,
    units: i.units,
  }));

  const height = Math.max(data.length * 34 + 16, 100);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 56, bottom: 4, left: 4 }}
      >
        <CartesianGrid stroke={GRID_COLOR} horizontal={false} />
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fill: "var(--muted)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={92}
        />
        <Tooltip
          content={
            <TooltipBox
              formatter={(v, e) => `¥${v.toFixed(2)} · ${e.payload?.units ?? 0} 件`}
            />
          }
          cursor={{ fill: "var(--surface-2)" }}
        />
        <Bar dataKey="revenue" name="销售额" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={18}>
          <LabelList
            dataKey="revenue"
            position="right"
            // Recharts 给的入参是 RenderableText（可能是 undefined），
            // 不能直接标成 number
            formatter={(value: unknown) => `¥${Math.round(Number(value) || 0)}`}
            style={{ fill: "var(--text-muted)", fontSize: 11 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/**
 * AI 与人工的承接量。三个分类，取分类色板前三槽。
 * aqua 在浅色底上对比度不足，所以数值直接标注。
 */
export function AiVsHumanChart({
  series,
}: {
  series: { sent_by: string; count: number }[];
}) {
  const LABELS: Record<string, string> = {
    ai: "AI 自动回复",
    human: "人工回复",
    system: "系统发送",
  };
  const ORDER = ["ai", "human", "system"];
  const data = ORDER.map((key) => ({
    key,
    label: LABELS[key],
    count: series.find((s) => s.sent_by === key)?.count ?? 0,
  })).filter((d) => d.count > 0);

  if (data.length === 0) {
    return <p className="py-6 text-center text-xs text-faint">暂无发送记录</p>;
  }

  return (
    <div className="space-y-3">
      {data.map((row, index) => {
        const max = Math.max(...data.map((d) => d.count), 1);
        return (
          <div key={row.key}>
            <div className="mb-1 flex items-baseline justify-between text-xs">
              <span className="text-muted">{row.label}</span>
              <span className="tabular text-text">{row.count}</span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(row.count / max) * 100}%`,
                  background: `var(--series-${Math.min(index + 1, 3)})`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
