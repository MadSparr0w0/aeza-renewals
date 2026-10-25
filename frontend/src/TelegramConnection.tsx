import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { api } from "./api";
import { invitationDate } from "./AccountForms";
import BrandIcon from "./BrandIcon";
import "./TelegramConnection.css";

type Chat = { id: string; title: string; type: string };
type Connection = {
  bot_configured: boolean;
  bot_username: string;
  connected: boolean;
  chat: Chat | null;
  pending: { id: number; expires_at: string; chat: Chat | null } | null;
};
type Setup = {
  request_id: number;
  code: string;
  command: string;
  expires_at: string;
  add_bot_url: string;
};

export default function TelegramConnection({
  onChanged,
}: {
  onChanged?: () => void;
}) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [operation, setOperation] = useState<string | null>("load");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyHint, setCopyHint] = useState("");
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [expired, setExpired] = useState(false);
  const commandRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);

  function applyConnection(result: Connection) {
    if (!alive.current) return;
    setConnection(result);
    setSetup((previous) =>
      previous && result.pending?.id === previous.request_id ? previous : null,
    );
  }

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    api<Connection>("/telegram/connection", { signal: controller.signal })
      .then(applyConnection)
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setOperation(null);
      });
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, []);

  const expiresAt = connection?.pending?.expires_at || setup?.expires_at;
  useEffect(() => {
    if (!expiresAt) {
      setExpired(false);
      return;
    }
    const remaining = new Date(expiresAt).getTime() - Date.now();
    setExpired(remaining <= 0);
    if (remaining <= 0) return;
    const timeout = setTimeout(() => setExpired(true), remaining);
    return () => clearTimeout(timeout);
  }, [expiresAt]);

  async function check() {
    setOperation("check");
    setError("");
    setNotice("");
    try {
      applyConnection(await api<Connection>("/telegram/connection"));
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setOperation(null);
    }
  }

  async function request() {
    setOperation("request");
    setError("");
    setNotice("");
    setCopied(false);
    setCopyHint("");
    try {
      const result = await api<Setup>("/telegram/connection/request", {
        method: "POST",
      });
      if (!alive.current) return;
      setSetup(result);
      setConnection((previous) =>
        previous
          ? {
              ...previous,
              pending: {
                id: result.request_id,
                expires_at: result.expires_at,
                chat: null,
              },
            }
          : null,
      );
      setExpired(false);
      applyConnection(await api<Connection>("/telegram/connection"));
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setOperation(null);
    }
  }

  async function confirm() {
    if (!connection?.pending?.chat || expired) return;
    setOperation("confirm");
    setError("");
    setNotice("");
    try {
      const result = await api<Connection>("/telegram/connection/confirm", {
        method: "POST",
        body: JSON.stringify({ request_id: connection.pending.id }),
      });
      if (!alive.current) return;
      applyConnection(result);
      setSetup(null);
      setNotice("Группа подключена.");
      onChanged?.();
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setOperation(null);
    }
  }

  async function disconnect() {
    setOperation("disconnect");
    setError("");
    setNotice("");
    try {
      await api("/telegram/connection", { method: "DELETE" });
      if (!alive.current) return;
      setConnection((previous) =>
        previous
          ? { ...previous, connected: false, chat: null, pending: null }
          : null,
      );
      setSetup(null);
      setDisconnectOpen(false);
      setNotice("Уведомления в группу отключены.");
      onChanged?.();
    } catch (reason) {
      if (alive.current) setError((reason as Error).message);
    } finally {
      if (alive.current) setOperation(null);
    }
  }

  async function copyCommand() {
    if (!setup || expired) return;
    try {
      await navigator.clipboard.writeText(setup.command);
      setCopied(true);
      setCopyHint("");
    } catch {
      commandRef.current?.focus();
      commandRef.current?.select();
      setCopyHint("Скопируйте выделенную команду вручную.");
    }
  }

  const busy = operation !== null;
  const pending = connection?.pending;
  return (
    <section className="settings-card telegram-connection">
      <div className="settings-icon">
        <BrandIcon name="telegram" size={22} />
      </div>
      <div className="settings-card-title">
        <h2>Чат команды в Telegram</h2>
        {connection && (
          <span className="status">
            <i />
            {connection.connected ? "Подключён" : "Не подключён"}
          </span>
        )}
      </div>
      <p>
        В общий чат приходят имя ответственного, платёж, сумма и срок оплаты.
        Отметки об оплате доступны в приложении.
      </p>
      {operation === "load" && (
        <div className="telegram-connection__loading" role="status">
          <LoaderCircle className="spin" size={18} />
          Загружаем настройки…
        </div>
      )}

      {connection?.chat && connection.connected && (
        <div className="telegram-connection__current">
          <span>Подключённая группа</span>
          <strong>{connection.chat.title}</strong>
          {disconnectOpen ? (
            <div className="telegram-connection__disconnect-confirm">
              <p>Отключить уведомления в эту группу?</p>
              <div className="telegram-connection__actions">
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={() => setDisconnectOpen(false)}
                >
                  Отмена
                </button>
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={disconnect}
                >
                  {operation === "disconnect" && (
                    <LoaderCircle className="spin" size={15} />
                  )}
                  Отключить
                </button>
              </div>
            </div>
          ) : (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => setDisconnectOpen(true)}
            >
              Отключить группу
            </button>
          )}
        </div>
      )}

      {connection && !connection.bot_configured && (
        <p className="telegram-connection__hint">
          Бот пока не настроен на сервере.
        </p>
      )}

      {setup && !expired && (
        <div className="telegram-connection__setup">
          <h3>
            {connection?.connected
              ? "Подключение другой группы"
              : "Подключение группы"}
          </h3>
          <ol>
            <li>
              <span>Добавьте бота в нужную группу.</span>
              <a
                className="button secondary small"
                href={setup.add_bot_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Добавить бота
                <ExternalLink size={14} />
              </a>
            </li>
            <li>
              <span>Администратор группы должен отправить в ней команду:</span>
              <label className="telegram-connection__command-label">
                <span className="sr-only">Команда подключения</span>
                <input
                  ref={commandRef}
                  value={setup.command}
                  readOnly
                  autoComplete="off"
                  spellCheck={false}
                  onFocus={(event) => event.currentTarget.select()}
                />
              </label>
              <button className="button secondary small" onClick={copyCommand}>
                {copied ? <Check size={15} /> : <Copy size={15} />}
                {copied ? "Скопировано" : "Скопировать команду"}
              </button>
              <span className="telegram-connection__copy-status" role="status">
                {copyHint}
              </span>
            </li>
            <li>
              <span>
                Вернитесь сюда, проверьте подключение и подтвердите название
                группы.
              </span>
            </li>
          </ol>
          <p className="telegram-connection__hint">
            Код действует до {invitationDate(setup.expires_at)} и показывается
            только сейчас.
          </p>
        </div>
      )}

      {pending && !setup && !pending.chat && !expired && (
        <p className="telegram-connection__hint">
          Код подключения уже создан и действует до{" "}
          {invitationDate(pending.expires_at)}. Если команда не сохранена,
          создайте новый код.
        </p>
      )}
      {expired && (
        <p className="telegram-connection__hint" role="status">
          Код подключения истёк. Создайте новый и повторите команду в группе.
        </p>
      )}

      {pending?.chat && !expired && (
        <div className="telegram-connection__candidate">
          <span>Группа найдена</span>
          <strong>{pending.chat.title}</strong>
          <p>
            После подтверждения уведомления будут отправляться в эту группу.
          </p>
          <button className="button primary" disabled={busy} onClick={confirm}>
            {operation === "confirm" && (
              <LoaderCircle className="spin" size={16} />
            )}
            Подключить эту группу
          </button>
        </div>
      )}

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <p className="telegram-connection__notice" role="status">
          {notice}
        </p>
      )}
      <div className="telegram-connection__actions">
        {connection?.bot_configured && (
          <button
            className="button secondary small"
            disabled={busy}
            onClick={request}
          >
            {operation === "request" && (
              <LoaderCircle className="spin" size={15} />
            )}
            {pending || setup
              ? "Создать новый код"
              : connection.connected
                ? "Сменить группу"
                : "Подключить группу"}
          </button>
        )}
        <button className="text-button" disabled={busy} onClick={check}>
          <RefreshCw
            size={15}
            className={operation === "check" ? "spin" : ""}
          />
          Проверить подключение
        </button>
      </div>
    </section>
  );
}
