"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, type Order } from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  inputClass,
} from "@/components/ui";

const STATUS_TONE: Record<string, "neutral" | "success" | "warn" | "danger" | "accent"> = {
  pending_confirm: "warn",
  confirmed: "accent",
  paid: "success",
  shipped: "accent",
  completed: "success",
  cancelled: "danger",
};

const STATUS_LABEL: Record<string, string> = {
  pending_confirm: "待确认",
  confirmed: "已确认",
  paid: "已付款",
  shipped: "已发货",
  completed: "已完成",
  cancelled: "已取消",
};

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  // 注意这里没有 setLoading(true)。初始值就是 true，而后续刷新由
  // 触发它的交互自己设置 —— 在 effect 里同步 setState 会触发级联渲染
  // （react-hooks/set-state-in-effect），而且语义上也不对：
  // loading 应该由「发起请求的动作」负责，不是由请求函数自己负责。
  const load = useCallback(async () => {
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      const data = await api.get<{ items: Order[] }>(`/api/orders${query}`);
      setOrders(data.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
  // 下面这行豁免的是误报：load() 里的 setState 全部在 await 之后，
  // effect 体内没有任何同步 setState。这条规则只看调用点，不跨 async
  // 边界追踪。React 官方文档认可「在 await 之后 setState」这种取数写法。
  // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function cancel(orderNo: string) {
    if (!confirm(`确认取消订单 ${orderNo}？库存会自动回补。`)) return;
    try {
      await api.post(`/api/orders/${orderNo}/cancel`);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  const filtered = orders.filter((o) =>
    search
      ? o.order_no.includes(search) ||
        o.customer_phone.includes(search) ||
        (o.customer_name ?? "").includes(search)
      : true,
  );

  return (
    <div>
      <PageHeader
        title="订单管理"
        description="客户通过 AI 生成的商单。每张单都能追溯到触发它的那次 AI 处理过程。"
        actions={
          <div className="flex items-center gap-2">
            <input
              className={`${inputClass} w-44`}
              placeholder="搜索单号 / 手机号"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className={`${inputClass} w-28`}
              value={statusFilter}
              onChange={(e) => {
                setLoading(true);
                setStatusFilter(e.target.value);
              }}
            >
              <option value="">全部状态</option>
              {Object.entries(STATUS_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        }
      />

      <ErrorNote error={error} />

      <Card>
        {loading ? (
          <Spinner />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="还没有订单"
            hint="到「消息调试」页发一条「有黑色XL吗」，再回一句「要了」，订单就会出现在这里。"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">订单号</th>
                  <th className="px-4 py-2.5 font-medium">客户</th>
                  <th className="px-4 py-2.5 font-medium">商品</th>
                  <th className="px-4 py-2.5 text-right font-medium">金额</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium">时间</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((order) => (
                  <Fragment key={order.order_no}>
                    <tr className="border-b border-border last:border-0 hover:bg-surface-2">
                      <td className="px-4 py-3 font-mono text-xs">{order.order_no}</td>
                      <td className="px-4 py-3">
                        <div className="text-xs">{order.customer_name ?? "—"}</div>
                        <div className="font-mono text-[11px] text-faint">
                          {order.customer_phone}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted">
                        {order.items.map((i) => (
                          <div key={i.sku}>
                            {i.name} {i.color}
                            {i.size} ×{i.qty}
                          </div>
                        ))}
                      </td>
                      <td className="px-4 py-3 text-right tabular">
                        {order.total_display}
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={STATUS_TONE[order.status] ?? "neutral"}>
                          {STATUS_LABEL[order.status] ?? order.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs text-faint">
                        {new Date(order.created_at).toLocaleString("zh-CN")}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        <Button
                          variant="ghost"
                          onClick={() =>
                            setExpanded(expanded === order.order_no ? null : order.order_no)
                          }
                        >
                          {expanded === order.order_no ? "收起" : "明细"}
                        </Button>
                      </td>
                    </tr>

                    {expanded === order.order_no && (
                      <tr className="border-b border-border bg-surface-2">
                        <td colSpan={7} className="px-4 py-4">
                          <div className="grid gap-4 lg:grid-cols-2">
                            <div>
                              <h4 className="mb-2 text-xs font-semibold text-text">
                                商品行 · 含最低价对照
                              </h4>
                              <table className="w-full text-xs">
                                <thead>
                                  <tr className="text-left text-faint">
                                    <th className="py-1 font-normal">SKU</th>
                                    <th className="py-1 font-normal">规格</th>
                                    <th className="py-1 text-right font-normal">数量</th>
                                    <th className="py-1 text-right font-normal">成交价</th>
                                    <th className="py-1 text-right font-normal">最低价</th>
                                  </tr>
                                </thead>
                                <tbody className="tabular">
                                  {order.items.map((i) => (
                                    <tr key={`${i.sku}-${i.color}-${i.size}`}>
                                      <td className="py-1 font-mono">{i.sku}</td>
                                      <td className="py-1">
                                        {i.color} {i.size}
                                      </td>
                                      <td className="py-1 text-right">{i.qty}</td>
                                      <td className="py-1 text-right">
                                        {i.unit_price_display}
                                      </td>
                                      <td className="py-1 text-right text-faint">
                                        {i.floor_price_display}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <p className="mt-2 text-[11px] leading-relaxed text-faint">
                                最低价只在这个后台视图里可见，永远不会进入模型上下文 ——
                                所以客户问「底价多少」时，AI 说不出一个它没见过的数字。
                              </p>
                            </div>

                            <div>
                              <h4 className="mb-2 text-xs font-semibold text-text">
                                操作
                              </h4>
                              {order.status !== "cancelled" ? (
                                <Button
                                  variant="danger"
                                  onClick={() => cancel(order.order_no)}
                                >
                                  取消订单并回补库存
                                </Button>
                              ) : (
                                <p className="text-xs text-faint">订单已取消，库存已回补。</p>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
