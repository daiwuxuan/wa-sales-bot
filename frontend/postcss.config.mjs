/**
 * Tailwind 4 走标准 PostCSS 管线。
 *
 * 脚手架生成的 next.config.ts 里原本有一条 turbopack.rules 把 *.css 交给
 * @tailwindcss/turbopack 处理，但那条规则实际没生效 —— 构建产物里只有
 * 字体声明，globals.css 的 `@import "tailwindcss"` 和 `@theme` 都没被解析，
 * 整个界面会完全没有样式。改用这个官方 PostCSS 插件，行为稳定可预期。
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
