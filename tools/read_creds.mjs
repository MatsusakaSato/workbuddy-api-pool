// 读取 WorkBuddy 桌面端凭据（含 at-rest 加密字段的解密）。
//
// 复用 dsh-connect-workbuddy 的 at-rest 实现（MIT, dingminhua/dsh-connect-workbuddy）。
// 该模块的导出名是压缩别名，这里**按函数名动态解析**，以免插件升级后失效。
//
// 用法：
//   node read_creds.mjs [凭据文件路径]          # 输出完整凭据 JSON（供程序消费，含明文 token）
//   node read_creds.mjs --safe [凭据文件路径]   # 输出脱敏摘要（供人看）
//
// 退出码恒为 0；成功与否看 JSON 里的 ok 字段。
import { readFile } from "node:fs/promises";
import * as ns from "../plugin-src/package/lib/host-heartbeat-De95rlXS.js";

const byName = (name) =>
  Object.values(ns).find((v) => typeof v === "function" && v.name === name);

const argv = process.argv.slice(2);
const safe = argv.includes("--safe");
const positional = argv.filter((a) => a !== "--safe");

const readAtRestKey = byName("readAtRestKey");
const defaultDesktopAuthPath = byName("defaultDesktopAuthPath");
const parseWorkBuddyAuth = byName("parseWorkBuddyAuth");

const emit = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");

if (!readAtRestKey || !defaultDesktopAuthPath || !parseWorkBuddyAuth) {
  emit({ ok: false, reason: "missing-exports" });
  process.exit(0);
}

const target = positional[0] || defaultDesktopAuthPath();

try {
  // 加密字段需要 App 的 at-rest 密钥。明文文件用不到，所以失败也不致命。
  let key;
  try {
    key = await readAtRestKey();
  } catch {
    key = undefined;
  }
  const text = await readFile(target, "utf8");
  const cred = parseWorkBuddyAuth(text, target, key);
  if (!cred) {
    emit({ ok: false, reason: "parse-failed", file: target });
    process.exit(0);
  }

  const payload = {
    ok: true,
    file: target,
    accessToken: cred.accessToken ?? "",
    refreshToken: cred.refreshToken ?? "",
    expiresAtMs: cred.expiresAtMs ?? 0,
    refreshExpiresAtMs: cred.refreshExpiresAtMs ?? 0,
    domain: cred.domain ?? "",
    uid: cred.uid ?? "",
    uin: String(cred.uin ?? ""),
    nickname: typeof cred.nickname === "string" ? cred.nickname : "",
  };

  if (safe) {
    const mask = (s) =>
      typeof s === "string" && s.length > 8 ? `<len=${s.length} head=${s.slice(0, 4)}…>` : s;
    emit({ ...payload, accessToken: mask(payload.accessToken), refreshToken: mask(payload.refreshToken) });
  } else {
    emit(payload);
  }
} catch (e) {
  emit({ ok: false, reason: String((e && e.message) || e), file: target });
}
