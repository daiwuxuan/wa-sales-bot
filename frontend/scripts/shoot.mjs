/**
 * 用系统已装的 Chrome 给各页面截图。
 *
 * 不用 chrome --headless --screenshot 的原因：--virtual-time-budget 会把
 * 虚拟时钟快进得比真实网络还快，客户端取数还没回来就截图了 ——
 * 结果是「页面卡在加载中」的假象，会让人去查一个根本不存在的 bug。
 * 这里显式等待网络空闲 + 关键元素出现。
 *
 * 用法：node scripts/shoot.mjs [输出目录]
 * 需要先起好前端 (3000) 和后端 (8000)。
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const OUT = process.argv[2] ?? "../data/shots";
const BASE = process.env.FRONTEND_URL ?? "http://localhost:3000";

const PAGES = [
  { path: "/playground", name: "playground", waitFor: "text=发送一条客户消息" },
  { path: "/orders", name: "orders", waitFor: "text=订单管理" },
  { path: "/products", name: "products", waitFor: "text=商品管理" },
  { path: "/analytics", name: "analytics", waitFor: "text=成交额" },
  { path: "/review", name: "review", waitFor: "text=推送审核" },
  { path: "/broadcast", name: "broadcast", waitFor: "text=新建活动" },
];

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
  locale: "zh-CN",
});

await mkdir(OUT, { recursive: true });

for (const page of PAGES) {
  const tab = await context.newPage();
  const errors = [];
  tab.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  tab.on("pageerror", (e) => errors.push(String(e)));

  await tab.goto(`${BASE}${page.path}`, { waitUntil: "networkidle", timeout: 30000 });

  try {
    await tab.waitForSelector(page.waitFor, { timeout: 15000 });
  } catch {
    console.log(`  ! ${page.path} 等待 "${page.waitFor}" 超时`);
  }
  // 再等一拍，让图表动画和布局稳定下来
  await tab.waitForTimeout(900);

  await tab.screenshot({ path: `${OUT}/${page.name}.png`, fullPage: true });
  console.log(
    `  ${errors.length ? "✗" : "✓"} ${page.path}${errors.length ? `  (${errors.length} 个控制台错误)` : ""}`,
  );
  errors.slice(0, 3).forEach((e) => console.log(`      ${e.slice(0, 160)}`));
  await tab.close();
}

await browser.close();
console.log(`截图已写入 ${OUT}`);
