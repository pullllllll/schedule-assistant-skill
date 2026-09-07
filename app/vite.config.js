import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

// 正式计划在仓库根目录的 data/plan.json，不入库也不在应用目录内。
// 中间件直读磁盘，保证 apply_plan.py 提交成功后刷新即最新。
const PLAN_PATH = resolve(process.cwd(), "../data/plan.json");
const FALLBACK_PATH = resolve(process.cwd(), "../fixtures/sample-plan.json");

function servePlan() {
  const handler = async (req, res, next) => {
    if (!req.url?.startsWith("/plan.json")) return next();
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    try {
      res.end(await readFile(PLAN_PATH));
    } catch (error) {
      if (error.code !== "ENOENT") {
        res.statusCode = 500;
        res.end(JSON.stringify({ error: "plan_read_failed", message: error.message }));
        return;
      }
      try {
        res.setHeader("X-Plan-Source", "fixture");
        res.end(await readFile(FALLBACK_PATH));
      } catch {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: "plan_missing", message: "data/plan.json 与样例都不存在" }));
      }
    }
  };
  return {
    name: "serve-official-plan",
    configureServer(server) { server.middlewares.use(handler); },
    configurePreviewServer(server) { server.middlewares.use(handler); },
  };
}

export default { server: { host: "127.0.0.1", port: 5180 }, plugins: [servePlan()] };
