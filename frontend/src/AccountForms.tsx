import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ArrowRight, Check, Copy, LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "./api";
import type { Invitation, Session } from "./api";
import { Logo, Modal } from "./ui";
import "./AccountForms.css";

export const invitationDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

export function InvitationModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [result, setResult] = useState<
    (Invitation & { invitation_url: string }) | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyHint, setCopyHint] = useState("");
  const urlRef = useRef<HTMLInputElement>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      setResult(
        await api<Invitation & { invitation_url: string }>("/invitations", {
          method: "POST",
          body: JSON.stringify({
            username: String(data.get("username")).trim(),
            full_name: String(data.get("full_name")).trim(),
          }),
        }),
      );
      onCreated();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.invitation_url);
      setCopied(true);
      setCopyHint("");
    } catch {
      urlRef.current?.focus();
      urlRef.current?.select();
      setCopyHint("Скопируйте выделенную ссылку вручную.");
    }
  }
  return (
    <Modal
      title={result ? "Приглашение создано" : "Пригласить участника"}
      subtitle={result ? undefined : "Участник задаст пароль самостоятельно."}
      onClose={onClose}
    >
      {result ? (
        <div className="account-forms__invitation-result">
          <div className="account-forms__identity">
            <strong>{result.full_name}</strong>
            <span>{result.username}</span>
          </div>
          <label>
            Ссылка приглашения
            <input
              ref={urlRef}
              value={result.invitation_url}
              readOnly
              autoComplete="off"
              spellCheck={false}
              onFocus={(event) => event.currentTarget.select()}
            />
          </label>
          <p>
            Действует до {invitationDate(result.expires_at)}. После активации
            повторно использовать ссылку нельзя.
          </p>
          <p>Ссылка показывается один раз. Передайте её участнику.</p>
          <div className="account-forms__copy-status" role="status">
            {copied ? "Ссылка скопирована." : copyHint}
          </div>
          <div className="modal-actions">
            <button className="button secondary" onClick={onClose}>
              Закрыть
            </button>
            <button className="button primary" onClick={copy}>
              {copied ? <Check size={16} /> : <Copy size={16} />}
              {copied ? "Скопировано" : "Скопировать ссылку"}
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit}>
          <label>
            Имя
            <input
              name="full_name"
              required
              maxLength={100}
              autoComplete="name"
              placeholder="Имя и фамилия"
            />
          </label>
          <label>
            Логин
            <input
              name="username"
              required
              minLength={3}
              maxLength={64}
              pattern={"[a-zA-Z0-9_.@\\-]+"}
              autoComplete="off"
              placeholder="Например, anna"
            />
            <small className="field-help">
              От 3 символов: латинские буквы, цифры, точка, дефис, подчёркивание
              или @.
            </small>
          </label>
          <p className="account-forms__hint">Приглашение действует 72 часа.</p>
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          <div className="modal-actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
              disabled={busy}
            >
              Отмена
            </button>
            <button className="button primary" disabled={busy}>
              {busy && <LoaderCircle className="spin" size={17} />}Создать
              приглашение
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

export function InvitationAccept({
  token,
  onAccepted,
  onBack,
}: {
  token: string;
  onAccepted: (session: Session) => void;
  onBack: () => void;
}) {
  const [identity, setIdentity] = useState<{
    username: string;
    full_name: string;
    expires_at: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setIdentity(null);
    api<{ username: string; full_name: string; expires_at: string }>(
      "/invitations/inspect",
      {
        method: "POST",
        body: JSON.stringify({ token }),
        signal: controller.signal,
      },
    )
      .then((result) => {
        if (!controller.signal.aborted) setIdentity(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, revision]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password"));
    if (password !== String(data.get("confirmation"))) {
      setError("Пароли не совпадают.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onAccepted(
        await api<Session>("/invitations/accept", {
          method: "POST",
          body: JSON.stringify({ token, password }),
        }),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="account-forms__accept-page">
      <Logo />
      <section className="account-forms__accept-card">
        <h1>Приглашение в команду</h1>
        {loading ? (
          <div className="loading-block">
            <LoaderCircle className="spin" size={22} />
            Проверяем приглашение…
          </div>
        ) : identity ? (
          <>
            <div className="account-forms__identity">
              <strong>{identity.full_name}</strong>
              <span>Логин: {identity.username}</span>
            </div>
            <p className="account-forms__hint">
              Задайте пароль для входа. Ссылка действует до{" "}
              {invitationDate(identity.expires_at)}.
            </p>
            <form onSubmit={submit}>
              <label>
                Пароль
                <input
                  type="password"
                  name="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={256}
                  placeholder="Не менее 12 символов"
                />
              </label>
              <label>
                Повторите пароль
                <input
                  type="password"
                  name="confirmation"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={256}
                />
              </label>
              {error && (
                <div className="form-error" role="alert">
                  {error}
                </div>
              )}
              <button
                className="button primary account-forms__wide"
                disabled={busy}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <ArrowRight size={17} />
                )}
                Принять приглашение
              </button>
            </form>
          </>
        ) : (
          <>
            <div className="form-error" role="alert">
              {error || "Приглашение недоступно."}
            </div>
            <button
              className="button secondary"
              onClick={() => setRevision((value) => value + 1)}
            >
              <RefreshCw size={16} />
              Проверить снова
            </button>
          </>
        )}
        <button
          className="text-button account-forms__back"
          onClick={onBack}
          disabled={busy}
        >
          Вернуться ко входу
        </button>
      </section>
    </main>
  );
}

export function ChangePasswordModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (data.get("new_password") !== data.get("confirmation")) {
      setError("Новые пароли не совпадают.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: data.get("current_password"),
          new_password: data.get("new_password"),
        }),
      });
      onChanged();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Сменить пароль"
      subtitle="После изменения потребуется войти снова на всех устройствах."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Текущий пароль
          <input
            type="password"
            name="current_password"
            autoComplete="current-password"
            required
            maxLength={256}
          />
        </label>
        <label>
          Новый пароль
          <input
            type="password"
            name="new_password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={256}
            placeholder="Не менее 12 символов"
          />
        </label>
        <label>
          Повторите новый пароль
          <input
            type="password"
            name="confirmation"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={256}
          />
        </label>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={busy}
          >
            Отмена
          </button>
          <button className="button primary" disabled={busy}>
            {busy && <LoaderCircle className="spin" size={17} />}Изменить пароль
          </button>
        </div>
      </form>
    </Modal>
  );
}
