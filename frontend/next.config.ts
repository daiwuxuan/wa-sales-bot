import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Next 16 默认拦截「跨源」的 dev 资源请求。dev server 绑定的是局域网地址
  // （如 192.168.x.x），这时用 127.0.0.1 访问就会被判为跨源，
  // hydration 需要的 RSC 载荷被拦掉 —— 现象是页面能显示（SSR 的 HTML 还在），
  // 但完全点不动、数据也永远停在「加载中」，且控制台几乎没有报错。
  // 这个坑很难查，所以直接把本地几个常见来源都放行。
  allowedDevOrigins: ["127.0.0.1", "localhost", "192.168.*.*"],
  // 把 /api 和 /uploads 代理到 FastAPI。
  //
  // 这样做而不是让浏览器直连 :8000，有两个实际好处：
  //   1. 同源，不需要处理 CORS，也不需要在浏览器里存后端地址
  //   2. 商品图的相对路径 /uploads/xxx.jpg 直接就能显示，
  //      不用把绝对 URL 拼进前端代码
  //
  // ★ 这里只代理到 FastAPI。前端永远不会持有 OPENAI_API_KEY ——
  //   浏览器和后端之间只有这些 HTTP 调用，密钥只存在于后端进程环境变量里。
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/uploads/:path*", destination: `${BACKEND}/uploads/:path*` },
    ];
  },

  // 注意：这里刻意不放 turbopack.rules 去接管 *.css。
  // 脚手架默认生成的那条规则（交给 @tailwindcss/turbopack）实测没有生效，
  // 产物里只有字体声明，Tailwind 的工具类和 @theme 令牌全部丢失。
  // Tailwind 4 的样式改由 postcss.config.mjs 里的 @tailwindcss/postcss 处理。
};

export default nextConfig;
