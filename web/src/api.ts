import type { ActionKind, FeedEvent, State } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const getState = () => fetch("/api/state").then((r) => json<State>(r));
export const getFeed = () => fetch("/api/feed").then((r) => json<FeedEvent[]>(r));

const post = (url: string, body: unknown) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<unknown>(r));

export const postAction = (id: string, action: ActionKind, note?: string) =>
  post(`/api/cards/${encodeURIComponent(id)}/action`, { action, note });
export const postMode = (mode: State["mode"]) => post("/api/mode", { mode });
