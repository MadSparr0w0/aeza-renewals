import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, LoaderCircle } from "lucide-react";
import { api } from "./api";
import type { Page } from "./api";
import "./DataControls.css";

export function useDebounced(value: string, delay = 300) {
  const [result, setResult] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setResult(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return result;
}
export function usePage<T>(url: string | null, revision = 0) {
  const [page, setPage] = useState<Page<T> | null>(null);
  const [loading, setLoading] = useState(Boolean(url));
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setError("");
    if (!url) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    api<Page<T>>(url, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setPage(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [url, revision, retry]);
  return { page, loading, error, reload: () => setRetry((value) => value + 1) };
}
export function Pagination({
  page,
  offset,
  onOffset,
  loading,
}: {
  page: Page<unknown> | null;
  offset: number;
  onOffset: (offset: number) => void;
  loading: boolean;
}) {
  const limit = page?.limit || 30;
  return (
    <div className="data-pagination" aria-label="Страницы списка">
      <span role="status">
        {loading ? (
          <>
            <LoaderCircle size={14} className="spin" />
            Загрузка…
          </>
        ) : page?.total ? (
          `${page.offset + 1}–${page.offset + page.items.length} из ${page.total}`
        ) : (
          "0 записей"
        )}
      </span>
      <div>
        <button
          type="button"
          className="button secondary small"
          aria-label="Предыдущая страница"
          disabled={loading || offset === 0}
          onClick={() => onOffset(Math.max(0, offset - limit))}
        >
          <ChevronLeft size={16} />
          Назад
        </button>
        <button
          type="button"
          className="button secondary small"
          aria-label="Следующая страница"
          disabled={loading || !page?.has_more}
          onClick={() => onOffset(offset + limit)}
        >
          Далее
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
