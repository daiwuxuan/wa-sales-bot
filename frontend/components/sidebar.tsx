"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/playground", label: "消息调试", hint: "模拟客户发消息" },
  { href: "/orders", label: "订单管理", hint: "AI 生成的商单" },
  { href: "/products", label: "商品管理", hint: "图片 / 价格 / 库存" },
  { href: "/analytics", label: "报表统计", hint: "销量与转化" },
  { href: "/review", label: "推送审核", hint: "发送前人工确认" },
  { href: "/broadcast", label: "群发广告", hint: "分群投放" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="shrink-0 border-b border-border bg-surface lg:w-56 lg:border-r lg:border-b-0">
      <div className="px-4 py-4 lg:px-5 lg:py-6">
        <div className="text-sm font-semibold text-text">AI 销售后台</div>
        <div className="mt-0.5 text-[11px] text-faint">WhatsApp · Mock 阶段</div>
      </div>

      <nav className="flex gap-1 overflow-x-auto px-2 pb-3 lg:flex-col lg:overflow-visible lg:px-2 lg:pb-6">
        {NAV.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group shrink-0 rounded-lg px-3 py-2 transition-colors lg:shrink ${
                active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-text"
              }`}
            >
              <div className="text-sm font-medium whitespace-nowrap">{item.label}</div>
              <div
                className={`hidden text-[11px] lg:block ${active ? "text-accent/80" : "text-faint"}`}
              >
                {item.hint}
              </div>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
