"use client";

import { useEffect, useState } from "react";
import { api, type LlmMode } from "@/lib/api";

/**
 * 显示当前用的 LLM。
 *
 * 存在的理由：后端在 OpenAI 探活失败时会**自动降级**成 Mock，
 * 宁可降级也不要让演示在第一位客户那里挂掉。但静默降级会让人
 * 以为自己在用 GPT-4o，所以这里必须显式提示。
 */
export function LlmBanner() {
  const [mode, setMode] = useState<LlmMode | null>(null);

  useEffect(() => {
    api
      .get<LlmMode>("/api/llm/mode")
      .then(setMode)
      .catch(() => setMode(null));
  }, []);

  if (!mode) return null;

  if (mode.mode === "openai") {
    return (
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-success/25 bg-success-soft px-3 py-2 text-xs text-success">
        <span className="h-1.5 w-1.5 rounded-full bg-success" />
        正在使用真实模型 <span className="font-mono">{mode.model}</span>
      </div>
    );
  }

  const probeFailed = mode.probe && !mode.probe.ok;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-warn/25 bg-warn-soft px-3 py-2 text-xs text-warn">
      <span className="h-1.5 w-1.5 rounded-full bg-warn" />
      <span>
        当前使用 <span className="font-medium">本地规则式 Mock</span>，未调用 OpenAI。
      </span>
      <span className="text-warn/80">
        {probeFailed
          ? `（探活失败已自动降级：${mode.probe?.message}）`
          : "（后端 .env 里填入 OPENAI_API_KEY 即可切换到真实模型，其它代码不用改）"}
      </span>
    </div>
  );
}
