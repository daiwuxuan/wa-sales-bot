# 前端

Next.js 16 (App Router) + Tailwind 4 + Recharts。

完整说明见仓库根目录的 [`../README.md`](../README.md)。

## 本地运行

```bash
npm install
npm run dev
```

打开 **http://localhost:3000**

> 用 `localhost` 而不是 `127.0.0.1`。dev server 绑定局域网地址时，
> Next 16 会把 `127.0.0.1` 判为跨源并拦截 dev 资源 —— 现象是页面能显示，
> 但完全点不动、数据永远停在「加载中」，控制台还几乎没有报错。

需要后端同时跑在 `:8000`。`next.config.ts` 里的 rewrites 会把 `/api` 和 `/uploads`
代理过去，所以前端代码里用相对路径即可，不用配后端地址、也不会有 CORS 问题。

## 页面

| 路由 | 说明 |
|---|---|
| `/playground` | 消息调试 —— 模拟客户发消息，看 AI 的完整工具调用轨迹 |
| `/orders` | 订单管理，含最低价对照与取消回补库存 |
| `/products` | 商品管理 —— 图片上传、改价改库存 |
| `/analytics` | 报表统计（数字全部由后端 SQL 聚合） |
| `/review` | 推送审核 —— 发送 / 不发送 / 编辑 |
| `/broadcast` | 群发广告（创建后需二次批准才发送） |

## 截图脚本

`scripts/shoot.mjs` 用系统已装的 Chrome 给各页面截图，会等待数据加载完成：

```bash
npm install --no-save playwright
node scripts/shoot.mjs
```
