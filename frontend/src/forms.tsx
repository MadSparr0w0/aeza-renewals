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
import OwnerPicker from "./OwnerPicker";
import { TelegramLogin } from "./TelegramAuth";
import type { Occurrence, Recurring, Session, User } from "./api";
import {
  currencies,
  dateLabel,
  Logo,
  Modal,
  money,
  moveMonth,
  Status,
  symbols,
} from "./ui";

export function Login({
  onLogin,
  notice,
}: {
  onLogin: (s: Session) => void;
  notice?: string;
}) {
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
            Регулярные
            <br />
            платежи
          </h1>
          <p>
            Сроки, напоминания
            <br />и история оплат.
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
          <h2>Вход</h2>
          <p className="login-intro">Войдите в свою учётную запись.</p>
          {notice && (
            <div className="form-notice" role="status">
              {notice}
            </div>
          )}
          <form onSubmit={submit}>
            <label>
              Логин
              <input
                name="username"
                autoComplete="username"
                required
                maxLength={64}
                placeholder="Логин"
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
                placeholder="Пароль"
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
          <TelegramLogin onSession={onLogin} />
          <div className="login-help">
            <CircleHelp size={17} />
            <p>Используйте учётную запись приложения.</p>
          </div>
        </div>
      </section>
    </main>
  );
}
export function AddPayment({
  today,
  initialName = "",
  owner,
  onClose,
  onSuccess,
}: {
  today: string;
  initialName?: string;
  owner?: User;
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
          ...(owner ? { owner_id: Number(data.get("owner_id")) } : {}),
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
      title="Добавить платёж"
      subtitle="Укажите сумму и ближайший срок оплаты."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        {owner && <OwnerPicker initial={owner} />}
        <label>
          Название платежа
          <input
            name="name"
            required
            maxLength={160}
            defaultValue={initialName}
            placeholder="Например, аренда офиса"
            autoFocus
          />
        </label>
        <div className="form-row amount-row">
          <label>
            Сумма
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
            Ближайшая оплата
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
            Добавить платёж
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function PaymentModal({
  item,
  today,
  canManage,
  onClose,
  onSuccess,
  onDelete,
  onEditSeries,
}: {
  item: Occurrence;
  today: string;
  canManage: boolean;
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
      {item.owner_name && (
        <p className="payment-detail-owner">Ответственный: {item.owner_name}</p>
      )}
      <div className="payment-detail-summary">
        <div>
          <span>СУММА</span>
          <strong>{money(item.amount, item.currency)}</strong>
        </div>
        <div>
          <span>СРОК ОПЛАТЫ</span>
          <strong>{dateLabel(item.due_date, true)}</strong>
        </div>
      </div>
      {!canManage && <Status item={item} />}
      {canManage && (
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
                Фактическая дата оплаты будет очищена. Срок оплаты и история
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
      )}
      {canManage && item.recurring_active && (
        <div className="modal-danger-zone">
          {onEditSeries && (
            <button
              type="button"
              className="text-button edit-series-button"
              onClick={onEditSeries}
            >
              <Clock3 size={15} /> Изменить будущие платежи
            </button>
          )}
          <button className="text-button danger" onClick={onDelete}>
            <Trash2 size={15} /> Прекратить учёт
          </button>
          <p>Будущие периоды и напоминания будут остановлены.</p>
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
          : "Не удалось обновить условия платежа.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Условия будущих платежей"
      subtitle={`Новые условия — с ${dateLabel(nextPeriod, true)}; текущий месяц и уже оплаченные периоды не изменятся.`}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Название платежа
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
            Сумма
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
            Изменения применяются к будущим неоплаченным периодам. День оплаты и
            ежемесячный график сохраняются.
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
  newAdmin = false,
  onClose,
  onSuccess,
}: {
  user: User | "new";
  newAdmin?: boolean;
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
      await api(newAdmin ? "/admins" : `/users${isNew ? "" : `/${user.id}`}`, {
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
      title={
        newAdmin
          ? "Создать администратора"
          : isNew
            ? "Создать аккаунт"
            : "Изменить пользователя"
      }
      subtitle={
        newAdmin
          ? "Отдельная команда со своей группой Telegram. Передайте пароль администратору безопасным способом."
          : "Учётная запись участника команды."
      }
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
            pattern={"[a-zA-Z0-9_.@\\-]+"}
            title="Латинские буквы, цифры, точка, дефис, подчёркивание или @"
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
            {isNew ? "Создать аккаунт" : "Сохранить"}
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
