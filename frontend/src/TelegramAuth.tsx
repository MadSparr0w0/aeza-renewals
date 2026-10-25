import { useEffect, useRef, useState } from "react";
import {
  Check,
  ExternalLink,
  Link2,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { api, setCsrf } from "./api";
import type { Session } from "./api";
import BrandIcon from "./BrandIcon";
import { Modal } from "./ui";
import "./TelegramAuth.css";

type TelegramConfig = { enabled: boolean; bot_username: string };
type TelegramIdentity = {
  linked: boolean;
  display_name?: string;
  linked_at?: string;
};
type TelegramChallenge = { id: string; url: string; expires_in: number };
type TelegramComplete = Session | { linked: true };
type ChallengePhase =
  | "starting"
  | "pending"
  | "completing"
  | "completed"
  | "expired"
  | "used"
  | "revoked"
  | "error";

function telegramUrl(value: string) {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Не удалось получить ссылку на бота. Повторите попытку.");
  }
  if (
    url.protocol !== "https:" ||
    !["t.me", "telegram.me"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.port
  ) {
    throw new Error("Не удалось получить ссылку на бота. Повторите попытку.");
  }
  return url.href;
}

function dateTime(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date)
    : "";
}

function TelegramChallengeModal({
  mode,
  onSuccess,
  onCancel,
}: {
  mode: "login" | "link";
  onSuccess: (result: TelegramComplete) => void;
  onCancel: () => void;
}) {
  const [challenge, setChallenge] = useState<TelegramChallenge | null>(null);
  const [phase, setPhase] = useState<ChallengePhase>("starting");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    let expiresAt = 0;
    let current: TelegramChallenge;
    const active = () => !disposed && !controller.signal.aborted;
    const clearTimers = () => {
      clearTimeout(pollTimer);
      clearTimeout(deadlineTimer);
    };
    const finish = (state: ChallengePhase) => {
      if (!active()) return;
      clearTimers();
      setPhase(state);
      controller.abort();
    };
    const fail = (message: string) => {
      if (!active()) return;
      setError(message);
      finish("error");
    };
    const poll = async () => {
      if (!active()) return;
      if (Date.now() >= expiresAt) {
        finish("expired");
        return;
      }
      try {
        const result = await api<{
          state: "pending" | "confirmed" | "used" | "expired" | "revoked";
        }>("/auth/telegram/status?id=" + encodeURIComponent(current.id), {
          signal: controller.signal,
        });
        if (!active()) return;
        if (result.state === "pending") {
          pollTimer = setTimeout(poll, 2500);
          return;
        }
        if (result.state !== "confirmed") {
          if (["used", "expired", "revoked"].includes(result.state)) {
            finish(result.state as "used" | "expired" | "revoked");
          } else {
            fail("Не удалось проверить подтверждение. Повторите попытку.");
          }
          return;
        }
        setPhase("completing");
        const completed = await api<TelegramComplete>(
          "/auth/telegram/complete",
          {
            method: "POST",
            body: JSON.stringify({ id: current.id }),
            signal: controller.signal,
          },
        );
        if (!active()) return;
        if (
          !completed ||
          typeof completed !== "object" ||
          (mode === "login" &&
            (!("user" in completed) || !completed.csrf_token)) ||
          (mode === "link" && (!("linked" in completed) || !completed.linked))
        ) {
          fail("Сервер вернул некорректный ответ. Повторите попытку.");
          return;
        }
        clearTimers();
        setPhase("completed");
        onSuccessRef.current(completed);
      } catch (reason) {
        if (active()) fail((reason as Error).message);
      }
    };
    const start = async () => {
      setChallenge(null);
      setError("");
      setPhase("starting");
      deadlineTimer = setTimeout(
        () => fail("Не удалось создать ссылку вовремя. Повторите попытку."),
        30000,
      );
      try {
        const result = await api<TelegramChallenge>("/auth/telegram/start", {
          method: "POST",
          body: JSON.stringify({ mode }),
          signal: controller.signal,
        });
        if (!active()) return;
        const lifetime = Number(result.expires_in);
        if (!result.id || !Number.isFinite(lifetime) || lifetime <= 0) {
          fail("Не удалось начать подтверждение. Повторите попытку.");
          return;
        }
        current = { ...result, url: telegramUrl(result.url) };
        expiresAt = Date.now() + Math.min(lifetime, 300) * 1000;
        clearTimeout(deadlineTimer);
        deadlineTimer = setTimeout(
          () => finish("expired"),
          expiresAt - Date.now(),
        );
        setChallenge(current);
        setPhase("pending");
        pollTimer = setTimeout(poll, 2500);
      } catch (reason) {
        if (active()) fail((reason as Error).message);
      }
    };
    void start();
    return () => {
      disposed = true;
      clearTimers();
      controller.abort();
    };
  }, [mode, revision]);

  const terminal = ["expired", "used", "revoked", "error"].includes(phase);
  const terminalMessage =
    phase === "expired"
      ? "Время подтверждения истекло. Создайте новую ссылку."
      : phase === "used"
        ? "Эта ссылка уже использована. Создайте новую, чтобы продолжить."
        : phase === "revoked"
          ? "Подтверждение отменено. Можно начать снова."
          : error;

  return (
    <Modal
      title={mode === "login" ? "Вход через Telegram" : "Привязать Telegram"}
      subtitle="Подтвердите действие в личном чате с ботом."
      onClose={onCancel}
    >
      <div className="telegram-auth__flow">
        {phase === "starting" && (
          <div className="telegram-auth__progress" role="status">
            <LoaderCircle className="spin" size={20} />
            Создаём ссылку подтверждения…
          </div>
        )}
        {challenge && phase === "pending" && (
          <>
            <ol className="telegram-auth__steps">
              <li>Откройте бота по кнопке ниже.</li>
              <li>
                Нажмите «Начать» в Telegram и подтвердите{" "}
                {mode === "login" ? "вход" : "привязку аккаунта"}.
              </li>
              <li>
                Вернитесь в приложение. Подтверждение появится автоматически.
              </li>
            </ol>
            <a
              className="button primary telegram-auth__open-bot"
              href={challenge.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <BrandIcon name="telegram" size={18} />
              Открыть бота
              <ExternalLink size={15} />
            </a>
            <p className="telegram-auth__hint">Ссылка действует до 5 минут.</p>
            <div className="telegram-auth__progress" role="status">
              <LoaderCircle className="spin" size={17} />
              Ждём подтверждения в Telegram…
            </div>
          </>
        )}
        {phase === "completing" && (
          <div className="telegram-auth__progress" role="status">
            <LoaderCircle className="spin" size={20} />
            {mode === "login" ? "Входим в аккаунт…" : "Завершаем привязку…"}
          </div>
        )}
        {phase === "completed" && (
          <div className="telegram-auth__progress" role="status">
            <Check size={20} />
            Подтверждено.
          </div>
        )}
        {terminal && (
          <div
            className={
              phase === "error" ? "form-error" : "telegram-auth__notice"
            }
            role="alert"
          >
            {terminalMessage}
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            {terminal ? "Закрыть" : "Отмена"}
          </button>
          {terminal && (
            <button
              type="button"
              className="button primary"
              onClick={() => setRevision((value) => value + 1)}
            >
              <RefreshCw size={16} />
              Начать снова
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

export function TelegramLogin({
  onSession,
}: {
  onSession: (session: Session) => void;
}) {
  const [config, setConfig] = useState<TelegramConfig | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<TelegramConfig>("/auth/telegram/config", { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setConfig(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      });
    return () => controller.abort();
  }, [revision]);
  if (!error && !config?.enabled) return null;
  return (
    <div className="telegram-auth__login">
      <div className="telegram-auth__divider" aria-hidden="true">
        или
      </div>
      {config?.enabled && (
        <button
          type="button"
          className="button secondary telegram-auth__login-button"
          onClick={() => setOpen(true)}
        >
          <BrandIcon name="telegram" size={18} />
          Войти через Telegram
        </button>
      )}
      {error && (
        <div className="telegram-auth__load-error">
          <p role="alert">{error}</p>
          <button
            type="button"
            className="text-button"
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={14} />
            Проверить вход через Telegram
          </button>
        </div>
      )}
      {open && (
        <TelegramChallengeModal
          mode="login"
          onCancel={() => setOpen(false)}
          onSuccess={(result) => {
            if (!("user" in result)) return;
            setCsrf(result.csrf_token);
            setOpen(false);
            onSession(result);
          }}
        />
      )}
    </div>
  );
}

export function TelegramIdentityPanel({ onLinked }: { onLinked?: () => void }) {
  const [identity, setIdentity] = useState<TelegramIdentity | null>(null);
  const [config, setConfig] = useState<TelegramConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const [linkOpen, setLinkOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError("");
    void Promise.allSettled([
      api<TelegramIdentity>("/auth/telegram/identity", {
        signal: controller.signal,
      }),
      api<TelegramConfig>("/auth/telegram/config", {
        signal: controller.signal,
      }),
    ]).then(([identityResult, configResult]) => {
      if (controller.signal.aborted) return;
      const messages: string[] = [];
      if (identityResult.status === "fulfilled")
        setIdentity(identityResult.value);
      else messages.push((identityResult.reason as Error).message);
      if (configResult.status === "fulfilled") setConfig(configResult.value);
      else messages.push((configResult.reason as Error).message);
      setError([...new Set(messages)].join(" "));
      setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);

  async function disconnect() {
    const controller = requestRef.current;
    if (!controller || controller.signal.aborted || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/auth/telegram/identity", {
        method: "DELETE",
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setIdentity({ linked: false });
      setDisconnectOpen(false);
      setNotice("Telegram отвязан. Вход по логину и паролю доступен.");
      onLinked?.();
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const linkedAt = identity?.linked_at ? dateTime(identity.linked_at) : "";
  return (
    <section className="settings-card telegram-auth__identity-panel">
      <div className="settings-icon">
        <BrandIcon name="telegram" size={22} />
      </div>
      <div className="settings-card-title">
        <h2>Вход через Telegram</h2>
        {identity && (
          <span className="status">
            <i />
            {identity.linked ? "Привязан" : "Не привязан"}
          </span>
        )}
      </div>
      <p>
        Привяжите личный Telegram для входа через бота. Чат команды для
        уведомлений настраивается отдельно.
      </p>
      {loading && (
        <div className="telegram-auth__progress" role="status">
          <LoaderCircle className="spin" size={18} />
          Загружаем настройки…
        </div>
      )}
      {identity?.linked && (
        <div className="telegram-auth__linked-account">
          <span>Аккаунт Telegram</span>
          <strong>{identity.display_name || "Telegram привязан"}</strong>
          {linkedAt && <span>Привязан {linkedAt}</span>}
        </div>
      )}
      {!loading && config && !config.enabled && (
        <p className="telegram-auth__hint">
          Вход через Telegram сейчас недоступен. Обратитесь к администратору.
        </p>
      )}
      {disconnectOpen && (
        <div className="telegram-auth__disconnect">
          <p>
            Отвязать Telegram? Для входа в приложение используйте логин и
            пароль.
          </p>
          <div className="telegram-auth__actions">
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => setDisconnectOpen(false)}
            >
              Отмена
            </button>
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={disconnect}
            >
              {busy && <LoaderCircle className="spin" size={15} />}Отвязать
              Telegram
            </button>
          </div>
        </div>
      )}
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <p className="telegram-auth__notice" role="status">
          {notice}
        </p>
      )}
      <div className="telegram-auth__actions">
        {identity?.linked
          ? !disconnectOpen && (
              <button
                type="button"
                className="text-button"
                disabled={busy || loading}
                onClick={() => {
                  setNotice("");
                  setDisconnectOpen(true);
                }}
              >
                Отвязать Telegram
              </button>
            )
          : identity &&
            config?.enabled && (
              <button
                type="button"
                className="button secondary small"
                disabled={busy || loading}
                onClick={() => {
                  setNotice("");
                  setLinkOpen(true);
                }}
              >
                <Link2 size={16} />
                Привязать Telegram
              </button>
            )}
        {error && (
          <button
            type="button"
            className="text-button"
            disabled={busy || loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={15} />
            Повторить загрузку
          </button>
        )}
      </div>
      {linkOpen && (
        <TelegramChallengeModal
          mode="link"
          onCancel={() => setLinkOpen(false)}
          onSuccess={() => {
            setLinkOpen(false);
            setNotice("Telegram привязан. Теперь можно входить через бота.");
            setRevision((value) => value + 1);
            onLinked?.();
          }}
        />
      )}
    </section>
  );
}

export function TelegramBotLinkConfirm({
  token,
  accountName,
  onSuccess,
  onCancel,
}: {
  token: string;
  accountName: string;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [identity, setIdentity] = useState<{
    display_name: string;
    expires_at: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    setIdentity(null);
    setLoading(true);
    setBusy(false);
    setExpired(false);
    setError("");
    api<{ display_name: string; expires_at: string }>(
      "/auth/telegram/bot-link?token=" + encodeURIComponent(token),
      { signal: controller.signal },
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        const remaining = new Date(result.expires_at).getTime() - Date.now();
        if (!Number.isFinite(remaining))
          throw new Error("Не удалось проверить ссылку. Повторите попытку.");
        setIdentity(result);
        setExpired(remaining <= 0);
        if (remaining > 0)
          expiryTimer = setTimeout(
            () => setExpired(true),
            Math.min(remaining, 2147483647),
          );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      clearTimeout(expiryTimer);
    };
  }, [token, revision]);

  async function confirm() {
    const controller = requestRef.current;
    if (
      !identity ||
      expired ||
      busy ||
      !controller ||
      controller.signal.aborted
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api("/auth/telegram/bot-link", {
        method: "POST",
        body: JSON.stringify({ token }),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) onSuccess();
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <Modal
      title="Подтвердить привязку Telegram"
      subtitle="Проверьте, что обе учётные записи принадлежат вам."
      onClose={onCancel}
    >
      <div className="telegram-auth__bot-confirm">
        {loading ? (
          <div className="telegram-auth__progress" role="status">
            <LoaderCircle className="spin" size={20} />
            Проверяем ссылку…
          </div>
        ) : (
          identity && (
            <>
              <dl className="telegram-auth__accounts">
                <div>
                  <dt>Учётная запись приложения</dt>
                  <dd>{accountName}</dd>
                </div>
                <div>
                  <dt>Аккаунт Telegram</dt>
                  <dd>{identity.display_name}</dd>
                </div>
              </dl>
              <p className="telegram-auth__hint">
                После привязки через этот Telegram можно будет входить в вашу
                учётную запись приложения.
              </p>
            </>
          )
        )}
        {expired && (
          <p className="telegram-auth__notice" role="alert">
            Срок действия ссылки истёк. Запросите новую ссылку у бота.
          </p>
        )}
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel}>
            Отмена
          </button>
          {identity && !expired ? (
            <button
              type="button"
              className="button primary"
              disabled={busy || loading}
              onClick={confirm}
            >
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Link2 size={16} />
              )}
              Привязать Telegram
            </button>
          ) : (
            !loading &&
            !expired && (
              <button
                type="button"
                className="button primary"
                onClick={() => setRevision((value) => value + 1)}
              >
                <RefreshCw size={16} />
                Проверить снова
              </button>
            )
          )}
        </div>
      </div>
    </Modal>
  );
}
