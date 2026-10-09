import { redirect } from "next/navigation";

export default function Home() {
  // 消息调试页是演示主链路的地方，作为默认落地页
  redirect("/playground");
}
