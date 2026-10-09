import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { LlmBanner } from "@/components/llm-banner";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "WhatsApp AI 销售后台",
  description: "AI 销售机器人的订单、报表、推送与商品管理",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <div className="flex min-h-screen flex-col lg:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 px-4 py-5 lg:px-8 lg:py-7">
            {/* 当前用的是真实模型还是 Mock，必须一眼可见 ——
                探活失败会静默降级，不显示的话很容易以为在跑 GPT-4o */}
            <LlmBanner />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
