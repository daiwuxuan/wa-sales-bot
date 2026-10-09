"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type ReportSummary, type TopSku } from "@/lib/api";
import {
  AiVsHumanChart,
  FunnelChart,
  GmvTrendChart,
  OrderTrendChart,
  TopSkuChart,
} from "@/components/charts";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  StatTile,
  inputClass,
} from "@/components/ui";

type TrendPoint = { date: string; gmv_cents: number; orders: number };
type FunnelStep = { key: string; label: string; value: number };
type SlowMover = { sku: string; name: string; variants: number; note: string };
type Discrepancy = {
  order_no: string;
  order_total_display: string;
  quote_total_display: string;
  diff_cents: number;
};

const RANGES = [
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 90, label: "近 90 天" },
];

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [funnel, setFunnel] = useState<FunnelStep[]>([]);
  const [topSkus, setTopSkus] = useState<TopSku[]>([]);
  const [slow, setSlow] = useState<SlowMover[]>([]);
  const [aiHuman, setAiHuman] = useState<{ sent_by: string; count: number }[]>([]);
  const [discrepancies, setDiscrepancies] = useState<Discrepancy[]>([]);
  const [aiText, setAiText] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [showTable, setShowTable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 不在 effect 里同步 setLoading —— 见 orders 页同处的说明
  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t, f, top, sm, ah, pd] = await Promise.all([
        api.get<ReportSummary>(`/api/reports/summary?days=${days}`),
        api.get<{ series: TrendPoint[] }>(`/api/reports/sales-trend?days=${days}`),
        api.get<{ steps: FunnelStep[] }>(`/api/reports/funnel?days=${days}`),
        api.get<{ items: TopSku[] }>(`/api/reports/top-skus?days=${days}&limit=8`),
        api.get<{ items: SlowMover[] }>(`/api/reports/slow-movers?days=${days}`),
        api.get<{ series: { sent_by: string; count: number }[] }>(
          `/api/reports/ai-vs-human?days=${days}`,
        ),
        api.get<{ items: Discrepancy[] }>("/api/reports/price-discrepancies"),
      ]);
      setSummary(s);
      setTrend(t.series);
      setFunnel(f.steps);
      setTopSkus(top.items);
      setSlow(sm.items);
      setAiHuman(ah.series);
      setDiscrepancies(pd.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
  // 下面这行豁免的是误报：load() 里的 setState 全部在 await 之后，
  // effect 体内没有任何同步 setState。这条规则只看调用点，不跨 async
  // 边界追踪。React 官方文档认可「在 await 之后 setState」这种取数写法。
  // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function generateSummary() {
    setAiLoading(true);
    setError(null);
    try {
      const data = await api.get<{ summary: string; mode: string }>(
        `/api/reports/ai-summary?days=${days}`,
      );
      setAiText(data.summary);
    } catch (e) {
      setError(String(e));
    } finally {
      setAiLoading(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="报表统计"
        description="所有数字都由 SQL 聚合算出，AI 只把算好的结果组织成一段话 —— 它不参与任何计算。转化率和复购率的定义写在 analytics.py 顶部。"
        actions={
          <div className="flex items-center gap-2">
            <select
              className={`${inputClass} w-28`}
              value={days}
              onChange={(e) => {
                setLoading(true);
                setDays(Number(e.target.value));
              }}
            >
              {RANGES.map((r) => (
                <option key={r.days} value={r.days}>
                  {r.label}
                </option>
              ))}
            </select>
            <Button variant="ghost" onClick={() => setShowTable((v) => !v)}>
              {showTable ? "看图表" : "看数据表"}
            </Button>
          </div>
        }
      />

      <ErrorNote error={error} />

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : !summary ? (
        <Card>
          <EmptyState title="暂无数据" />
        </Card>
      ) : (
        <div className="space-y-4">
          {/* KPI —— 单个数用 stat tile，不用只有一根柱子的柱状图 */}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="成交额"
              value={summary.gmv_display}
              hint={`近 ${summary.range_days} 天`}
            />
            <StatTile
              label="订单数"
              value={summary.order_count}
              hint={`客单价 ${summary.avg_order_display}`}
            />
            <StatTile
              label="询盘转化率"
              value={`${summary.inquiry_conversion_pct}%`}
              hint={`${summary.conversation_customers} 位咨询客户中成单占比`}
            />
            <StatTile
              label="复购率"
              value={`${summary.repeat_rate_pct}%`}
              hint={`${summary.repeat_customers} 位客户下过 2 单以上`}
              tone={summary.repeat_rate_pct > 0 ? "success" : "neutral"}
            />
          </div>

          {summary.human_takeover_count > 0 && (
            <div className="rounded-lg border border-warn/25 bg-warn-soft px-3 py-2 text-xs text-warn">
              当前有 {summary.human_takeover_count} 个会话处于人工接管中，AI 不会自动回复。
            </div>
          )}

          {/* 趋势 —— 两张共享 X 轴的小倍数图，不是双 Y 轴 */}
          <Card>
            <CardHeader
              title="成交额与订单量"
              subtitle="拆成上下两张图共享时间轴。金额和笔数量纲不同，合成双 Y 轴会让交叉点看起来有意义。"
            />
            <div className="px-4 py-4">
              {trend.length === 0 ? (
                <EmptyState title="该区间没有成交" hint="换个时间范围试试" />
              ) : showTable ? (
                <TrendTable data={trend} />
              ) : (
                <div className="space-y-1">
                  <div className="text-[11px] font-medium text-muted">成交额</div>
                  <GmvTrendChart data={trend} />
                  <div className="pt-1 text-[11px] font-medium text-muted">订单量</div>
                  <OrderTrendChart data={trend} />
                </div>
              )}
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="转化漏斗"
                subtitle="询盘 → 报价 → 下单 → 付款"
              />
              <div className="px-5 py-4">
                <FunnelChart steps={funnel} />
              </div>
            </Card>

            <Card>
              <CardHeader
                title="承接方式"
                subtitle={`AI 与人工的发送量对比 · 近 ${days} 天`}
              />
              <div className="px-5 py-4">
                <AiVsHumanChart series={aiHuman} />
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader
              title="畅销款 Top 8"
              subtitle="按销售额排序。单系列用单色相，数值直接标在条形末端。"
            />
            <div className="px-5 py-4">
              {topSkus.length === 0 ? (
                <EmptyState title="该区间没有成交" />
              ) : (
                <TopSkuChart
                  items={topSkus.map((s) => ({
                    sku: s.sku,
                    name: s.name,
                    revenue_cents: s.revenue_cents,
                    units: s.units,
                  }))}
                />
              )}
            </div>
          </Card>

          {/* 滞销款用表格，不用图 —— 分类多、且需要看文字信息 */}
          <Card>
            <CardHeader
              title="滞销款"
              subtitle="在售但该区间零成交。这一块用表格而不是图表：条目多、且需要读商品名。"
            />
            {slow.length === 0 ? (
              <EmptyState title="所有在售商品都有成交" />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted">
                      <th className="px-5 py-2 font-medium">SKU</th>
                      <th className="px-5 py-2 font-medium">商品</th>
                      <th className="px-5 py-2 text-right font-medium">变体数</th>
                      <th className="px-5 py-2 font-medium">说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {slow.slice(0, 10).map((item) => (
                      <tr key={item.sku} className="border-b border-border last:border-0">
                        <td className="px-5 py-2 font-mono text-xs">{item.sku}</td>
                        <td className="px-5 py-2">{item.name}</td>
                        <td className="px-5 py-2 text-right tabular">{item.variants}</td>
                        <td className="px-5 py-2 text-xs text-faint">{item.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* 报价 vs 成交价差异 —— 正常情况下永远为空 */}
          <Card>
            <CardHeader
              title="报价 / 成交价差异"
              subtitle="理论上永远为空：下单必须基于报价。这张表存在的意义是万一哪天非空，人工能立刻发现。"
            />
            {discrepancies.length === 0 ? (
              <div className="flex items-center gap-2 px-5 py-4 text-xs text-success">
                <span>✓</span>
                <span>没有差异，报价和成交价完全一致。</span>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <tbody>
                    {discrepancies.map((d) => (
                      <tr key={d.order_no} className="border-b border-border last:border-0">
                        <td className="px-5 py-2 font-mono text-xs">{d.order_no}</td>
                        <td className="px-5 py-2 text-xs">
                          报价 {d.quote_total_display} → 成交 {d.order_total_display}
                        </td>
                        <td className="px-5 py-2 text-right text-xs text-danger tabular">
                          {d.diff_cents > 0 ? "+" : ""}
                          {d.diff_cents / 100}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader
              title="AI 经营总结"
              subtitle="交给模型的是一张已经算好的数字表，不是原始订单。它做的是组织语言，不是计算。"
              action={
                <Button variant="primary" onClick={generateSummary} disabled={aiLoading}>
                  {aiLoading ? "生成中…" : "生成总结"}
                </Button>
              }
            />
            <div className="px-5 py-4">
              {aiText ? (
                <p className="text-sm leading-relaxed whitespace-pre-wrap text-text">
                  {aiText}
                </p>
              ) : (
                <p className="text-xs text-faint">
                  点右上角生成。AI 只会引用已经算好的数字，不会自己计算新的比率或金额。
                </p>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function TrendTable({ data }: { data: TrendPoint[] }) {
  return (
    <div className="max-h-72 overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-border text-left text-muted">
            <th className="py-2 font-medium">日期</th>
            <th className="py-2 text-right font-medium">成交额</th>
            <th className="py-2 text-right font-medium">订单量</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.date} className="border-b border-border last:border-0">
              <td className="py-1.5">{row.date}</td>
              <td className="py-1.5 text-right tabular">
                ¥{(row.gmv_cents / 100).toFixed(2)}
              </td>
              <td className="py-1.5 text-right tabular">{row.orders}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
