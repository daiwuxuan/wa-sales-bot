"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Campaign } from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  PageHeader,
  Spinner,
  inputClass,
} from "@/components/ui";

type Preview = { count: number; sample: { phone: string; name: string | null; tier: string }[] };

const TIERS = [
  { code: "retail", label: "零售" },
  { code: "silver", label: "银卡" },
  { code: "gold", label: "金卡" },
  { code: "vip", label: "VIP" },
];

export default function BroadcastPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [message, setMessage] = useState("换季清仓，全场新款上架，回复「想要」我发图给您看看。");
  const [tiers, setTiers] = useState<string[]>([]);
  const [minOrders, setMinOrders] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);

  // 不在 effect 里同步 setLoading —— 见 orders 页同处的说明
  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: Campaign[] }>("/api/campaigns");
      setCampaigns(data.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
  // 下面这行豁免的是误报：load() 里的 setState 全部在 await 之后，
  // effect 体内没有任何同步 setState。这条规则只看调用点，不跨 async
  // 边界追踪。React 官方文档认可「在 await 之后 setState」这种取数写法。
  // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const audience = { tiers, min_total_orders: minOrders, has_conversation: true };

  async function doPreview() {
    try {
      setPreview(await api.post<Preview>("/api/campaigns/preview", audience));
    } catch (e) {
      setError(String(e));
    }
  }

  async function create() {
    if (!name.trim() || !message.trim()) {
      setError("活动名称和文案都不能为空");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/campaigns", {
        name,
        message_text: message,
        audience,
        throttle_per_minute: 60,
      });
      setName("");
      setPreview(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approveAndSend(id: number) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/campaigns/${id}/approve?by=admin`);
      await api.post(`/api/campaigns/${id}/send`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="群发广告"
        description="按客户等级分群投放。创建后需要再点一次「批准并发送」——两步走是刻意的，群发不可撤回，不该有一键直发的路径。"
      />

      <ErrorNote error={error} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader title="新建活动" />
          <div className="space-y-4 px-5 py-4">
            <Field label="活动名称">
              <input
                className={inputClass}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="10 月换季清仓"
              />
            </Field>

            <Field label="广告文案" hint="真实环境下，24 小时客服窗口外只能发送已审批模板。这里走 Mock，不受限制。">
              <textarea
                className={`${inputClass} min-h-[96px] resize-y`}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
            </Field>

            <Field label="目标客户等级" hint="不选则包含全部等级">
              <div className="flex flex-wrap gap-2">
                {TIERS.map((tier) => {
                  const active = tiers.includes(tier.code);
                  return (
                    <button
                      key={tier.code}
                      onClick={() =>
                        setTiers(
                          active ? tiers.filter((t) => t !== tier.code) : [...tiers, tier.code],
                        )
                      }
                      className={`rounded-lg border px-2.5 py-1 text-xs transition-colors ${
                        active
                          ? "border-accent/30 bg-accent-soft text-accent"
                          : "border-border bg-surface text-muted hover:text-text"
                      }`}
                    >
                      {tier.label}
                    </button>
                  );
                })}
              </div>
            </Field>

            <Field label="最少历史订单数">
              <input
                type="number"
                min={0}
                className={`${inputClass} w-28`}
                value={minOrders}
                onChange={(e) => setMinOrders(Number(e.target.value) || 0)}
              />
            </Field>

            <div className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" onClick={doPreview}>
                预览命中人数
              </Button>
              {preview && (
                <span className="text-xs text-muted">
                  命中 <span className="font-semibold text-text tabular">{preview.count}</span> 位客户
                </span>
              )}
            </div>

            {preview && preview.sample.length > 0 && (
              <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
                <div className="mb-1 text-[11px] text-faint">抽样</div>
                <ul className="space-y-0.5 text-xs text-muted">
                  {preview.sample.map((c) => (
                    <li key={c.phone}>
                      {c.name ?? "—"} <span className="font-mono text-faint">{c.phone}</span>{" "}
                      <Badge tone="accent">{c.tier}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="rounded-lg border border-warn/25 bg-warn-soft px-3 py-2 text-[11px] leading-relaxed text-warn">
              退订和拉黑的客户会被自动排除。真实环境里给退订客户群发会导致账号被封，
              这条规则虽然当前是 Mock 也照常执行。
            </div>

            <Button variant="primary" onClick={create} disabled={busy}>
              {busy ? "处理中…" : "创建活动（待批准）"}
            </Button>
          </div>
        </Card>

        <Card>
          <CardHeader title="活动列表" subtitle={`${campaigns.length} 个`} />
          {loading ? (
            <Spinner />
          ) : campaigns.length === 0 ? (
            <EmptyState title="还没有群发活动" />
          ) : (
            <ul className="divide-y divide-border">
              {campaigns.map((campaign) => (
                <li key={campaign.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-text">
                        {campaign.name}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted">
                        {campaign.message_text}
                      </p>
                      <div className="mt-1 text-[11px] text-faint">
                        目标 {campaign.total_targets} · 成功 {campaign.sent_count} · 失败{" "}
                        {campaign.failed_count}
                      </div>
                    </div>
                    <Badge
                      tone={
                        campaign.status === "done"
                          ? "success"
                          : campaign.status === "pending_approval"
                            ? "warn"
                            : "neutral"
                      }
                    >
                      {campaign.status === "done"
                        ? "已完成"
                        : campaign.status === "pending_approval"
                          ? "待批准"
                          : campaign.status}
                    </Badge>
                  </div>

                  {campaign.status === "pending_approval" && (
                    <Button
                      variant="success"
                      className="mt-2 !px-2.5 !py-1 !text-[11px]"
                      disabled={busy}
                      onClick={() => approveAndSend(campaign.id)}
                    >
                      批准并发送
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
