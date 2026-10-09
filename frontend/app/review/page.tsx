"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type PushRecommendation } from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  inputClass,
} from "@/components/ui";

type StatusFilter = "pending" | "sent" | "skipped" | "edited" | "all";

export default function ReviewPage() {
  const [items, setItems] = useState<PushRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("pending");
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  // 不在 effect 里同步 setLoading —— 见 orders 页同处的说明
  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: PushRecommendation[] }>(
        `/api/review/queue?status=${filter}`,
      );
      setItems(data.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
  // 下面这行豁免的是误报：load() 里的 setState 全部在 await 之后，
  // effect 体内没有任何同步 setState。这条规则只看调用点，不跨 async
  // 边界追踪。React 官方文档认可「在 await 之后 setState」这种取数写法。
  // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function generate() {
    setBusy(-1);
    setError(null);
    try {
      await api.post("/api/review/seed-demo");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function decide(id: number, action: "approve" | "skip" | "edit") {
    setBusy(id);
    setError(null);
    try {
      const body =
        action === "edit" ? { edited_copy: draft, by: "admin" } : { by: "admin" };
      await api.post(`/api/review/${id}/${action}`, body);
      setEditing(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="推送审核"
        description="AI 起草，你决定发不发。这里没有「AI 直接群发」的路径 —— 所有对外推送都要经过这一页。"
        actions={
          <div className="flex items-center gap-2">
            <select
              className={`${inputClass} w-28`}
              value={filter}
              onChange={(e) => {
                setLoading(true);
                setFilter(e.target.value as StatusFilter);
              }}
            >
              <option value="pending">待审核</option>
              <option value="sent">已发送</option>
              <option value="edited">已编辑发送</option>
              <option value="skipped">未发送</option>
              <option value="all">全部</option>
            </select>
            <Button variant="primary" onClick={generate} disabled={busy === -1}>
              {busy === -1 ? "生成中…" : "生成推荐"}
            </Button>
          </div>
        }
      />

      <ErrorNote error={error} />

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title="没有待审核的推送"
            hint="点右上角「生成推荐」造几条演示数据，或让客户先下几单再自动生成。"
          />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <Card key={item.id}>
              <CardHeader
                title={item.customer_name ?? item.phone ?? "未知客户"}
                subtitle={
                  <span className="font-mono">{item.phone}</span>
                }
                action={
                  <div className="flex gap-1">
                    {item.tier && <Badge tone="accent">{item.tier}</Badge>}
                    <Badge
                      tone={
                        item.status === "pending"
                          ? "warn"
                          : item.status === "skipped"
                            ? "neutral"
                            : "success"
                      }
                    >
                      {item.status === "pending"
                        ? "待审核"
                        : item.status === "sent"
                          ? "已发送"
                          : item.status === "edited"
                            ? "编辑后发送"
                            : "未发送"}
                    </Badge>
                  </div>
                }
              />

              <div className="space-y-3 px-5 py-4">
                <div className="grid gap-2 text-xs sm:grid-cols-2">
                  <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
                    <div className="mb-0.5 text-[11px] text-faint">兴趣画像</div>
                    <div className="text-text">{item.interest ?? "—"}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
                    <div className="mb-0.5 text-[11px] text-faint">历史购买</div>
                    <div className="text-text">{item.history_summary ?? "—"}</div>
                  </div>
                </div>

                {item.recommended_sku && (
                  <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-2">
                    {item.image_url && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={item.image_url}
                        alt=""
                        className="h-14 w-14 rounded border border-border object-cover"
                      />
                    )}
                    <div className="min-w-0">
                      <div className="text-[11px] text-faint">推荐商品</div>
                      <div className="truncate text-xs text-text">
                        {item.recommended_name ?? item.recommended_sku}
                      </div>
                      <div className="font-mono text-[11px] text-faint">
                        {item.recommended_sku}
                      </div>
                    </div>
                  </div>
                )}

                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-[11px] font-medium text-muted">AI 文案</span>
                    {item.ai_copy?.match(/[¥￥]|\d+\s*元/) && (
                      <Badge tone="danger">含金额，请核对</Badge>
                    )}
                  </div>

                  {editing === item.id ? (
                    <textarea
                      className={`${inputClass} min-h-[84px] resize-y`}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                    />
                  ) : (
                    <p className="rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-sm whitespace-pre-wrap text-text">
                      {item.edited_copy ?? item.ai_copy ?? "（无文案）"}
                    </p>
                  )}
                </div>

                {item.status === "pending" ? (
                  <div className="flex gap-2">
                    <Button
                      variant="success"
                      disabled={busy === item.id}
                      onClick={() => decide(item.id, "approve")}
                    >
                      发送
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy === item.id}
                      onClick={() => decide(item.id, "skip")}
                    >
                      不发送
                    </Button>
                    {editing === item.id ? (
                      <>
                        <Button
                          variant="primary"
                          disabled={busy === item.id || !draft.trim()}
                          onClick={() => decide(item.id, "edit")}
                        >
                          保存并发送
                        </Button>
                        <Button variant="ghost" onClick={() => setEditing(null)}>
                          取消
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setEditing(item.id);
                          setDraft(item.ai_copy ?? "");
                        }}
                      >
                        编辑
                      </Button>
                    )}
                  </div>
                ) : (
                  <p className="text-[11px] text-faint">
                    已于 {item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "—"} 处理
                  </p>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
