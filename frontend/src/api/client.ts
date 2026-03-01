import { API_BASE_URL } from "../config";

async function request<T>(path: string, method: "GET" | "POST" = "GET"): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${path}`);
  }

  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, "GET"),
  post: <T>(path: string) => request<T>(path, "POST"),
};
