import { useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "./api";
import { Confirm } from "./forms";
import BrandIcon from "./BrandIcon";
import { invitationDate } from "./AccountForms";
import "./TelegramConnection.css";

type Destination = { id: string; title: string; connected_at: string };
type Destinations = { items: Destination[]; manage_url: string | null };
export default function TelegramDestinations({
  onChanged,
}: {
  onChanged?: () => void;
}) {
  const [data, setData] = useState<Destinations | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0),
    [removing, setRemoving] = useState<Destination | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<Destinations>("/telegram/destinations", { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  const manageUrl = (() => {
    try {
      const url = new URL(data?.manage_url || "");
      return url.protocol === "https:" &&
        ["t.me", "telegram.me"].includes(url.hostname)
        ? url.href
        : null;
    } catch {
      return null;
    }
  })();
  return (
    <section className="settings-card telegram-connection">
      <div className="settings-icon">
        <BrandIcon name="telegram" size={22} />
      </div>
      <div className="settings-card-title">
        <h2>Группы в Telegram</h2>
        <button
          className="icon-button"
          aria-label="Обновить список групп"
          disabled={loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} className={loading ? "spin" : ""} />
        </button>
      </div>
      <p>
        Подключайте группы через личный кабинет бота. Напоминания поступают во
        все подключённые группы.
      </p>
      {manageUrl && (
        <a
          className="button secondary"
          href={manageUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Управление в боте
          <ExternalLink size={15} />
        </a>
      )}
      {loading ? (
        <div className="telegram-connection__loading">
          <LoaderCircle className="spin" size={18} />
          Загрузка групп…
        </div>
      ) : error ? (
        <div className="form-error" role="alert">
          {error}
        </div>
      ) : data?.items.length ? (
        data.items.map((destination) => (
          <div className="telegram-connection__current" key={destination.id}>
            <strong>{destination.title}</strong>
            <span>Подключена {invitationDate(destination.connected_at)}</span>
            <button
              className="text-button"
              onClick={() => setRemoving(destination)}
            >
              Отключить эту группу
            </button>
          </div>
        ))
      ) : (
        <p className="telegram-connection__hint">
          Подключённых групп пока нет.
        </p>
      )}
      {removing && (
        <Confirm
          title="Отключить группу?"
          text={`Уведомления в «${removing.title}» прекратятся. Остальные группы останутся подключены.`}
          confirm="Отключить"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api(
              `/telegram/destinations/${encodeURIComponent(removing.id)}`,
              { method: "DELETE" },
            );
            setRemoving(null);
            setRevision((value) => value + 1);
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}
