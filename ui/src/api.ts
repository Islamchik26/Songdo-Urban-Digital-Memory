export interface Param {
  name: string;
  label: string;
  type: "float" | "int" | "str" | "datetime";
  default: string | number | null;
  min: number | null;
  max: number | null;
}

export interface Scenario {
  id: string;
  domain: string;
  title: string;
  summary: string;
  status: "planned" | "ready";
  sql: string | null;
  params: Param[];
}

export interface Registry {
  domains: Record<string, string>;
  scenarios: Scenario[];
}

export interface RunResult {
  scenario_id: string;
  params: Record<string, unknown>;
  sql: string;
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  rows: Record<string, unknown>[];
  explain: string[] | null;
}

export interface Health {
  status: "ok" | "down";
  database?: string;
  postgres?: string;
  postgis?: string;
  detail?: string;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch("/health");
    return await res.json();
  } catch {
    return { status: "down", detail: "API is not reachable" };
  }
}

export async function getRegistry(): Promise<Registry> {
  const res = await fetch("/api/v1/scenarios");
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function runScenario(
  id: string,
  params: Record<string, string>,
  explain: boolean,
): Promise<RunResult> {
  const res = await fetch(`/api/v1/scenarios/${id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params, explain }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}
