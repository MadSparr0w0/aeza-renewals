import { useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  CircleHelp,
  Clock3,
  LoaderCircle,
  Plus,
  Trash2,
} from "lucide-react";
import { api, setCsrf } from "./api";
import type { Occurrence, Recurring, Session, User } from "./api";
import {
  currencies,
  dateLabel,
  Logo,
  Modal,
  money,
  moveMonth,
  symbols,
} from "./ui";

export function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<Session>("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: data.get("username"),
          password: data.get("password"),
        }),
      });
      setCsrf(result.csrf_token);
      onLogin(result);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Не удалось войти. Проверьте соединение.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <section className="login-story">
        <Logo />
        <div className="story-content">
          <h1>
            Услуги
            <br />и продления
          </h1>
          <p>
            Стоимость услуг, сроки продления
            <br />и история оплат в одном модуле.
          </p>
          <div className="login-brand-note">
            <span>RUB · USD · EUR</span>
          </div>
        </div>
        <div className="brand-pattern" aria-hidden="true">
          <i />
          <i />
          <i />
          <b />
        </div>
      </section>
      <section className="login-form-side">
        <div className="mobile-login-brand">
          <Logo />
        </div>
        <div className="login-form-wrap">
          <h2>Вход в модуль</h2>
          <p className="login-intro">Учётная запись модуля продлений.</p>
          <form onSubmit={submit}>
            <label>
              Логин
              <input
                name="username"
                autoComplete="username"
                required
                maxLength={64}
                placeholder="Логин модуля"
                autoFocus
              />
            </label>
            <label>
              Пароль
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                placeholder="Пароль модуля"
              />
            </label>
            {error && (
              <div role="alert" className="form-error">
                {error}
              </div>
            )}
            <button className="button primary login-submit" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <>
                  Войти <ArrowRight size={18} />
                </>
              )}
            </button>
          </form>
          <div className="login-help">
            <CircleHelp size={17} />
            <p>
              Для входа используйте учётную запись приложения. Личный кабинет
              Aéza —{" "}
              <a
                href="https://my.aeza.ru"
                target="_blank"
                rel="noopener noreferrer"
              >
                my.aeza.ru
              </a>
              .
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
export function AddPayment({
  today,
  initialName = "",
  onClose,
  onSuccess,
}: {
  today: string;
  initialName?: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/recurring-payments", {
        method: "POST",
        body: JSON.stringify({
          name: String(data.get("name")).trim(),
          amount: String(data.get("amount")).replace(",", "."),
          currency: data.get("currency"),
          next_due_date: data.get("next_due_date"),
          frequency: "monthly",
        }),
      });
      onSuccess();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Добавить услугу"
      subtitle="Запись для учёта стоимости и сроков продления уже заказанной услуги Aéza."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Название услуги
          <input
            name="name"
            required
            maxLength={160}
            defaultValue={initialName}
            placeholder="Например, VPS для проекта"
            autoFocus
          />
        </label>
        <div className="form-row amount-row">
          <label>
            Стоимость продления
            <input
              name="amount"
              type="number"
              inputMode="decimal"
              min="0.01"
              max="999999999.99"
              step="0.01"
              required
              placeholder="0,00"
            />
          </label>
          <label>
            Валюта
            <select name="currency">
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c} {symbols[c]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-row">
          <label>
            Ближайшее продление
            <input
              name="next_due_date"
              type="date"
              defaultValue={today}
              required
            />
          </label>
          <label>
            Периодичность
            <select name="frequency" defaultValue="monthly">
              <option value="monthly">Каждый месяц</option>
            </select>
          </label>
        </div>
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
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <Plus size={17} />
            )}{" "}
            Добавить услугу
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function PaymentModal({
  item,
  today,
  isAdmin,
  onClose,
  onSuccess,
  onDelete,
  onEditSeries,
}: {
  item: Occurrence;
  today: string;
  isAdmin: boolean;
  onClose: () => void;
  onSuccess: () => void;
  onDelete: () => void;
  onEditSeries?: () => void;
}) {
  const [isPaid, setIsPaid] = useState(item.is_paid);
  const [paidAt, setPaidAt] = useState(item.paid_at || today);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const actualPaidAt = formData.get("paid_at");
    setBusy(true);
    setError("");
    try {
      await api(`/payment-occurrences/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          is_paid: isPaid,
          paid_at: isPaid ? actualPaidAt : null,
        }),
      });
      onSuccess();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={item.name} onClose={onClose}>
      <div className="payment-detail-summary">
        <div>
          <span>СТОИМОСТЬ ПРОДЛЕНИЯ</span>
          <strong>{money(item.amount, item.currency)}</strong>
        </div>
        <div>
          <span>СРОК ПРОДЛЕНИЯ</span>
          <strong>{dateLabel(item.due_date, true)}</strong>
        </div>
      </div>
      <form onSubmit={submit}>
        <div className="payment-status-control">
          <button
            type="button"
            className={!isPaid ? "selected" : ""}
            onClick={() => setIsPaid(false)}
          >
            <Clock3 size={17} /> Не оплачено
          </button>
          <button
            type="button"
            className={isPaid ? "selected paid" : ""}
            onClick={() => setIsPaid(true)}
          >
            <CheckCircle2 size={17} /> Оплачено
          </button>
        </div>
        {isPaid ? (
          <label>
            Фактическая дата оплаты
            <input
              type="date"
              name="paid_at"
              required
              max={today}
              value={paidAt}
              onChange={(e) => setPaidAt(e.target.value)}
            />
            <small className="field-help">
              Можно исправить дату: просрочка пересчитается автоматически.
            </small>
          </label>
        ) : (
          <div className="form-note">
            <Clock3 size={18} />
            <p>
              Фактическая дата оплаты будет очищена. Срок продления и история
              остальных оплат сохранятся.
            </p>
          </div>
        )}
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
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <Check size={17} />
            )}{" "}
            Сохранить
          </button>
        </div>
      </form>
      {isAdmin && item.recurring_active && (
        <div className="modal-danger-zone">
          {onEditSeries && (
            <button
              type="button"
              className="text-button edit-series-button"
              onClick={onEditSeries}
            >
              <Clock3 size={15} /> Изменить будущие продления
            </button>
          )}
          <button className="text-button danger" onClick={onDelete}>
            <Trash2 size={15} /> Прекратить учёт
          </button>
          <p>Останавливает учёт продлений и напоминания в этом модуле.</p>
        </div>
      )}
    </Modal>
  );
}
export function EditRecurringModal({
  series,
  today,
  onClose,
  onSuccess,
}: {
  series: Recurring;
  today: string;
  onClose: () => void;
  onSuccess: (effectiveFrom: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nextPeriod = `${moveMonth(today.slice(0, 7), 1)}-01`;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<Recurring & { effective_from: string }>(
        `/recurring-payments/${series.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            name: String(data.get("name")).trim(),
            amount: String(data.get("amount")).replace(",", "."),
            currency: data.get("currency"),
          }),
        },
      );
      onSuccess(result.effective_from);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Не удалось обновить условия продлений.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Условия будущих продлений"
      subtitle={`Новые условия — с ${dateLabel(nextPeriod, true)}; текущий месяц и уже оплаченные продления не изменятся.`}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Название услуги
          <input
            name="name"
            required
            maxLength={160}
            defaultValue={series.name}
            autoFocus
          />
        </label>
        <div className="form-row amount-row">
          <label>
            Стоимость продления
            <input
              name="amount"
              type="number"
              inputMode="decimal"
              min="0.01"
              max="999999999.99"
              step="0.01"
              required
              defaultValue={series.amount}
            />
          </label>
          <label>
            Валюта
            <select name="currency" defaultValue={series.currency}>
              {currencies.map((currency) => (
                <option key={currency} value={currency}>
                  {currency} {symbols[currency]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-note">
          <Clock3 size={18} />
          <p>
            Изменения применяются к будущим неоплаченным продлениям. Число
            продления и ежемесячный график сохраняются.
          </p>
        </div>
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
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <Check size={17} />
            )}{" "}
            Сохранить условия
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function UserModal({
  user,
  onClose,
  onSuccess,
}: {
  user: User | "new";
  onClose: () => void;
  onSuccess: () => void;
}) {
  const isNew = user === "new";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {
      username: String(data.get("username")).trim(),
      full_name: String(data.get("full_name")).trim(),
    };
    const password = String(data.get("password"));
    if (password) body.password = password;
    setBusy(true);
    setError("");
    try {
      await api(`/users${isNew ? "" : `/${user.id}`}`, {
        method: isNew ? "POST" : "PATCH",
        body: JSON.stringify(body),
      });
      onSuccess();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={isNew ? "Добавить пользователя" : "Изменить пользователя"}
      subtitle="Учётная запись модуля: просмотр услуг и подтверждение оплаты продлений."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Имя
          <input
            name="full_name"
            autoComplete="name"
            required
            maxLength={100}
            defaultValue={isNew ? "" : user.full_name}
            placeholder="Имя и фамилия"
            autoFocus
          />
        </label>
        <label>
          Логин
          <input
            name="username"
            autoComplete="off"
            required
            minLength={3}
            maxLength={64}
            pattern="[a-zA-Z0-9_.@-]+"
            title="Латинские буквы, цифры, точка, дефис и подчёркивание"
            defaultValue={isNew ? "" : user.username}
            placeholder="Например, anna"
          />
          <small className="field-help">
            От 3 символов: латинские буквы, цифры, точка, дефис, подчёркивание.
          </small>
        </label>
        <label>
          {isNew ? "Пароль" : "Новый пароль"}
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            required={isNew}
            minLength={12}
            maxLength={256}
            placeholder={
              isNew
                ? "Не менее 12 символов"
                : "Оставьте пустым, чтобы сохранить пароль"
            }
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
            {busy ? (
              <LoaderCircle size={17} className="spin" />
            ) : (
              <Check size={17} />
            )}{" "}
            {isNew ? "Добавить пользователя" : "Сохранить"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function Confirm({
  title,
  text,
  confirm,
  onConfirm,
  onClose,
}: {
  title: string;
  text: string;
  confirm: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    setBusy(true);
    setError("");
    try {
      await onConfirm();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <p className="confirm-text">{text}</p>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      <div className="modal-actions">
        <button className="button secondary" onClick={onClose} disabled={busy}>
          Отмена
        </button>
        <button
          className="button danger-button"
          onClick={submit}
          disabled={busy}
        >
          {busy ? (
            <LoaderCircle className="spin" size={17} />
          ) : (
            <Trash2 size={17} />
          )}{" "}
          {confirm}
        </button>
      </div>
    </Modal>
  );
}
