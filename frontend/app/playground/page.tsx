"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type ChatMessage,
  type Conversation,
  type TurnStep,
} from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  inputClass,
  PageHeader,
  Spinner,
  ToolCallChip,
} from "@/components/ui";

type WebhookResult = {
  status: string;
  deduplicated?: boolean;
  ai_enabled?: boolean;
  conversation_id?: number;
  turn_id?: string;
  turn_status?: string;
  reply_text?: string;
  escalated?: boolean;
  note?: string;
  steps?: TurnStep[];
  guard?: { violations: string[] };
};

/** 出站 payload 就是 Cloud API /messages 的请求体形态，两种类型二选一 */
type OutboundPayload =
  | { type: "text"; to: string; text: { body: string; preview_url?: boolean } }
  | { type: "image"; to: string; image: { link: string; caption?: string } };

type OutboundItem = {
  wa_message_id: string;
  to: string;
  sent_by: string;
  payload: OutboundPayload;
};

const DEMO_PHONE = "8613600136000";

export default function PlaygroundPage() {
  const [phone, setPhone] = useState(DEMO_PHONE);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<WebhookResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [outbox, setOutbox] = useState<OutboundItem[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);

  const outboxCursor = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    try {
      const data = await api.get<{ items: Conversation[] }>("/api/conversations");
      setConversations(data.items);
      return data.items;
    } catch (e) {
      setError(String(e));
      return [];
    }
  }, []);

  const loadMessages = useCallback(async (id: number) => {
    setLoadingMessages(true);
    try {
      const data = await api.get<{ items: ChatMessage[] }>(
        `/api/conversations/${id}/messages`,
      );
      setMessages(data.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  const refreshOutbox = useCallback(async () => {
    try {
      const data = await api.get<{ messages: OutboundItem[] }>(
        `/api/mock-webhook/outbox?since=${outboxCursor.current}`,
      );
      if (data.messages.length) {
        outboxCursor.current += data.messages.length;
        setOutbox((prev) => [...prev, ...data.messages].slice(-40));
      }
    } catch {
      /* 发件箱只是辅助显示，失败不打断主流程 */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      const items = await loadConversations();
      if (cancelled || items.length === 0) return;
      setActiveConv(items[0].id);
      await loadMessages(items[0].id);
    }

    void bootstrap();
    return () => {
      cancelled = true; // 卸载后不要再 setState
    };
  }, [loadConversations, loadMessages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    setError(null);
    const outgoing = text;
    setText("");

    try {
      const data = await api.post<WebhookResult>("/api/mock-webhook", {
        phone,
        text: outgoing,
      });
      setResult(data);
      await refreshOutbox();

      const items = await loadConversations();
      const convId = data.conversation_id ?? items.find((c) => c.phone === phone)?.id;
      if (convId) {
        setActiveConv(convId);
        await loadMessages(convId);
      }
    } catch (e) {
      setError(String(e));
      setText(outgoing);
    } finally {
      setSending(false);
    }
  }

  async function toggleTakeover(conv: Conversation, enabled: boolean) {
    try {
      await api.patch(`/api/conversations/${conv.id}/takeover`, {
        enabled,
        by: "admin",
        reason: enabled ? "手动接管" : null,
      });
      await loadConversations();
    } catch (e) {
      setError(String(e));
    }
  }

  const activeConversation = conversations.find((c) => c.id === activeConv) ?? null;

  return (
    <div>
      <PageHeader
        title="消息调试"
        description="模拟客户给 WhatsApp 发消息。这里是 Mock 接口，不会真实发送 —— 但走的是和真实接入完全相同的解析、去重、AI 编排和落库链路。"
      />

      <ErrorNote error={error} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* ── 左：发消息 + 对话 ── */}
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="发送一条客户消息"
              subtitle="试试「有黑色XL吗」，然后回一句「要了」"
            />
            <div className="space-y-3 px-5 py-4">
              <div className="grid gap-3 sm:grid-cols-[200px_minmax(0,1fr)]">
                <Field label="客户手机号">
                  <input
                    className={inputClass}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="8613800138000"
                  />
                </Field>
                <Field label="消息内容">
                  <input
                    className={inputClass}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && send()}
                    placeholder="有黑色XL吗"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap gap-2">
                {["有黑色XL吗", "要了", "我是VIP批发商，给我批发价", "有蓝色XXL吗"].map(
                  (sample) => (
                    <button
                      key={sample}
                      onClick={() => setText(sample)}
                      className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[11px] text-muted hover:border-border-strong hover:text-text"
                    >
                      {sample}
                    </button>
                  ),
                )}
              </div>
              <Button variant="primary" onClick={send} disabled={sending || !text.trim()}>
                {sending ? "AI 处理中…" : "发送"}
              </Button>
            </div>
          </Card>

          {result && (
            <Card>
              <CardHeader
                title="这一次 AI 做了什么"
                subtitle={
                  result.deduplicated
                    ? "重投消息，已忽略（不会触发第二次 AI 处理）"
                    : result.ai_enabled === false
                      ? "会话在人工接管中，AI 未介入"
                      : `turn ${result.turn_id?.slice(0, 8)} · ${result.turn_status}`
                }
              />
              <div className="space-y-3 px-5 py-4">
                {result.steps && result.steps.length > 0 && (
                  <ol className="space-y-1.5">
                    {result.steps.map((step) => (
                      <li
                        key={`${step.step}-${step.tool}`}
                        className="flex flex-wrap items-center gap-2 text-xs"
                      >
                        <span className="w-10 text-faint tabular">#{step.step}</span>
                        <ToolCallChip name={step.tool} ok={step.ok} />
                        <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">
                          {JSON.stringify(step.args)}
                        </code>
                        <span className="text-faint tabular">{step.duration_ms}ms</span>
                      </li>
                    ))}
                  </ol>
                )}

                {result.reply_text && (
                  <div className="rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                    <div className="mb-1 text-[11px] font-medium text-faint">AI 回复</div>
                    <p className="text-sm whitespace-pre-wrap text-text">
                      {result.reply_text}
                    </p>
                  </div>
                )}

                {result.escalated && (
                  <div className="rounded-lg border border-warn/25 bg-warn-soft px-3 py-2 text-xs text-warn">
                    该会话已转人工接管，AI 不再自动回复。
                  </div>
                )}

                {result.guard?.violations?.length ? (
                  <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">
                    接地校验拦截了未授权数字：{result.guard.violations.join("、")}
                  </div>
                ) : null}
              </div>
            </Card>
          )}

          <Card>
            <CardHeader
              title="对话记录"
              subtitle={
                activeConversation
                  ? `${activeConversation.phone}${activeConversation.customer_name ? " · " + activeConversation.customer_name : ""}`
                  : "还没有会话"
              }
              action={
                activeConversation && (
                  <Button
                    variant={activeConversation.ai_enabled ? "secondary" : "success"}
                    onClick={() =>
                      toggleTakeover(activeConversation, activeConversation.ai_enabled)
                    }
                  >
                    {activeConversation.ai_enabled ? "转人工接管" : "交还 AI"}
                  </Button>
                )
              }
            />
            <div ref={scrollRef} className="max-h-[420px] overflow-y-auto px-5 py-4">
              {loadingMessages ? (
                <Spinner />
              ) : messages.length === 0 ? (
                <EmptyState title="还没有消息" hint="在上面发一条试试" />
              ) : (
                <ul className="space-y-2.5">
                  {messages.map((m) => (
                    <li
                      key={m.id}
                      className={`flex ${m.direction === "in" ? "justify-start" : "justify-end"}`}
                    >
                      <div
                        className={`max-w-[78%] rounded-2xl px-3.5 py-2 text-sm ${
                          m.direction === "in"
                            ? "bg-surface-2 text-text"
                            : "bg-accent-soft text-text"
                        }`}
                      >
                        {m.media_url && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={m.media_url}
                            alt=""
                            className="mb-1.5 max-h-44 rounded-lg border border-border"
                          />
                        )}
                        {m.body && <p className="whitespace-pre-wrap">{m.body}</p>}
                        <div className="mt-1 flex items-center gap-1.5 text-[10px] text-faint">
                          <span>{m.sent_by === "ai" ? "AI" : m.sent_by === "human" ? "人工" : "系统"}</span>
                          <span>·</span>
                          <span>{new Date(m.created_at).toLocaleTimeString("zh-CN")}</span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Card>
        </div>

        {/* ── 右：会话列表 + 发件箱 ── */}
        <div className="space-y-4">
          <Card>
            <CardHeader title="会话" subtitle={`${conversations.length} 个`} />
            <ul className="divide-y divide-border">
              {conversations.length === 0 && <EmptyState title="暂无会话" />}
              {conversations.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => {
                      setActiveConv(c.id);
                      loadMessages(c.id);
                    }}
                    className={`w-full px-4 py-2.5 text-left transition-colors hover:bg-surface-2 ${
                      c.id === activeConv ? "bg-surface-2" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium text-text">
                        {c.customer_name ?? c.phone}
                      </span>
                      <div className="flex shrink-0 gap-1">
                        {c.tier && <Badge tone="accent">{c.tier}</Badge>}
                        {!c.ai_enabled && <Badge tone="warn">人工</Badge>}
                      </div>
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-faint">{c.phone}</div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <Card>
            <CardHeader
              title="发件箱"
              subtitle="Mock 通道实际「发出」的消息体"
            />
            <div className="max-h-[380px] space-y-2 overflow-y-auto px-4 py-3">
              {outbox.length === 0 && (
                <p className="py-6 text-center text-xs text-faint">
                  还没有发出的消息
                </p>
              )}
              {[...outbox].reverse().map((item) => {
                const payload = item.payload;
                return (
                  <div
                    key={item.wa_message_id}
                    className="rounded-lg border border-border bg-surface-2 px-3 py-2"
                  >
                    <div className="mb-1 flex items-center gap-1.5">
                      <Badge tone={payload.type === "image" ? "accent" : "neutral"}>
                        {payload.type}
                      </Badge>
                      <span className="text-[10px] text-faint">{item.to}</span>
                    </div>
                    {payload.type === "image" ? (
                      <>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={payload.image.link}
                          alt=""
                          className="max-h-28 rounded border border-border"
                        />
                        <p className="mt-1 text-[11px] text-muted">
                          {payload.image.caption}
                        </p>
                      </>
                    ) : (
                      <p className="text-xs whitespace-pre-wrap text-text">
                        {payload.text.body}
                      </p>
                    )}
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-[10px] text-faint hover:text-muted">
                        看 Cloud API 请求体
                      </summary>
                      <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 font-mono text-[10px] text-muted">
                        {JSON.stringify(item.payload, null, 2)}
                      </pre>
                    </details>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
