import { useState, useEffect } from "react";
import { apiBase } from "../config/api";

const API = apiBase();

interface NewsArticle {
  title: string;
  source: string;
  url?: string;
  thumbnail?: string;
}

interface CacheEntry {
  articles: NewsArticle[];
  fetchedAt: number;
}

let cache: CacheEntry | null = null;
let inflight: Promise<NewsArticle[]> | null = null;
const listeners = new Set<() => void>();

async function fetchNews(): Promise<NewsArticle[]> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await fetch(`${API}/dashboard/news`, { signal: AbortSignal.timeout(3000) });
      const data = await res.json();
      const articles: NewsArticle[] = data.articles?.slice(0, 8) ?? [];
      cache = { articles, fetchedAt: Date.now() };
      listeners.forEach((fn) => fn());
      return articles;
    } catch {
      return cache?.articles ?? [];
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function useNewsCache() {
  const [articles, setArticles] = useState<NewsArticle[]>(cache?.articles ?? []);

  useEffect(() => {
    const update = () => setArticles(cache?.articles ?? []);
    listeners.add(update);

    const needsFetch = !cache || Date.now() - cache.fetchedAt > 60_000;
    if (needsFetch) void fetchNews();

    const id = setInterval(() => void fetchNews(), 120_000);
    return () => { listeners.delete(update); clearInterval(id); };
  }, []);

  return articles;
}
