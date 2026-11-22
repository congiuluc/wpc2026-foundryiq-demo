import type { Demo, Mode } from "./types";

export const demoRoutes: Record<Demo, { path: string; label: string; mode: Mode; kb: string }> = {
  "multi-fonte": { path: "/demo/multi-fonte", label: "Multi-fonte", mode: "noacl", kb: "demo" },
  acl: { path: "/demo/acl", label: "ACL", mode: "compare", kb: "acl" },
  agenti: { path: "/demo/agenti", label: "Agenti RAG vs IQ", mode: "agents", kb: "demo" },
};
export const demos: readonly Demo[] = ["multi-fonte", "acl", "agenti"];

export function demoForPath(path: string): Demo | null {
  return demos.find((key) => demoRoutes[key].path === path.replace(/\/$/, "")) ?? null;
}

export function isMode(value: string | null): value is Mode {
  return value === "noacl" || value === "acl" || value === "compare" || value === "agents";
}
