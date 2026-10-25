import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  LockKeyhole,
  Pencil,
  ReceiptText,
  ShieldCheck,
  ScrollText,
  Trash2,
} from "lucide-react";
import BrandIcon from "./BrandIcon";
import type {
  Currency,
  Invitation,
  Occurrence,
  Session,
  Settings,
  User,
} from "./api";
import { invitationDate } from "./AccountForms";
import TelegramConnection from "./TelegramConnection";
import {
  dateLabel,
  Empty,
  initials,
  money,
  monthLabel,
  plural,
  Status,
} from "./ui";

export function OverviewMiddle({
  amounts,
  currency,
  paidCount,
  totalCount,
  settings,
  onSettings,
}: {
  amounts?: { planned: string; paid: string; unpaid: string; overdue: string };
  currency: Currency;
  paidCount: number;
  totalCount: number;
  settings: Settings | null;
  onSettings: () => void;
}) {
  const planned = Number(amounts?.planned || 0);
  const hasData = planned > 0;
  const paidPercent = hasData
    ? Math.max(0, Math.min(100, (Number(amounts?.paid || 0) / planned) * 100))
    : 0;
  const portions = [
    {
      label: "Оплачено",
      percent: paidPercent,
      amount: amounts?.paid || "0",
      kind: "paid",
    },
    {
      label: "Осталось",
      percent: hasData ? 100 - paidPercent : 0,
      amount: amounts?.unpaid || "0",
      kind: "remaining",
    },
  ];
  const percentLabel = (value: number) =>
    value > 0 && value < 0.1
      ? "<0,1"
      : value < 100 && value > 99.9
        ? ">99,9"
        : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
            value,
          );
  return (
    <section className="overview-middle">
      <div className="reminder-banner">
        <div className="reminder-content">
          <div className="reminder-card-heading">
            <span className="reminder-card-icon">
              <BrandIcon name="telegram" size={21} />
            </span>
            <h2>Напоминания в Telegram</h2>
          </div>
          <p>
            За {settings?.reminder_days || 3} дня до срока оплаты. Оплаченные
            периоды пропускаются.
          </p>
          <div className="reminder-card-footer">
            <span
              className={`reminder-state ${settings?.telegram_configured ? "configured" : ""}`}
            >
              <i />
              {settings?.telegram_configured ? "Настроено" : "Не настроено"}
            </span>
            <button className="text-button" onClick={onSettings}>
              Настройки <ArrowUpRight size={16} />
            </button>
          </div>
        </div>
      </div>
      <div className="month-progress payment-orbits">
        <div className="card-topline">
          <span>Оплата за месяц</span>
          <span className="orbits-currency">{currency}</span>
        </div>
        <div className="payment-orbits-grid">
          {portions.map((portion) => (
            <div className={`payment-orbit ${portion.kind}`} key={portion.kind}>
              <div
                className="payment-orbit-chart"
                role="img"
                aria-label={
                  hasData
                    ? `${portion.label}: ${percentLabel(portion.percent)} процента, ${money(portion.amount, currency)}`
                    : "Нет данных за месяц в выбранной валюте"
                }
              >
                <svg viewBox="0 0 84 84" aria-hidden="true" focusable="false">
                  <circle className="orbit-track" cx="42" cy="42" r="37" />
                  {hasData && portion.percent > 0 && (
                    <circle
                      className="orbit-value"
                      cx="42"
                      cy="42"
                      r="37"
                      pathLength="100"
                      strokeDasharray={`${portion.percent} 100`}
                    />
                  )}
                </svg>
                <span>
                  {hasData ? percentLabel(portion.percent) : "—"}
                  {hasData && <small>%</small>}
                </span>
              </div>
              <span className="orbit-label">{portion.label}</span>
              <strong className="orbit-amount">
                {amounts ? money(portion.amount, currency) : "—"}
              </strong>
            </div>
          ))}
        </div>
        <p className="orbits-caption">
          {hasData
            ? `${paidCount} из ${totalCount} платежей оплачено`
            : amounts
              ? "Нет платежей в выбранной валюте"
              : "Загружаем итоги…"}
        </p>
      </div>
    </section>
  );
}
export function OverviewBottom({
  items,
  onSelect,
  onCalendar,
}: {
  items: Occurrence[];
  onSelect: (i: Occurrence) => void;
  onCalendar: () => void;
}) {
  return (
    <section className="bottom-grid">
      <div className="upcoming-card">
        <div className="simple-card-heading">
          <h2>Ближайшие платежи</h2>
          <button className="text-button" onClick={onCalendar}>
            Открыть календарь <ArrowUpRight size={15} />
          </button>
        </div>
        {items.length ? (
          items.map((item) => (
            <button
              className="upcoming-item"
              key={item.id}
              onClick={() => onSelect(item)}
            >
              <span className="upcoming-date">
                <strong>{Number(item.due_date.slice(8))}</strong>
                <span>
                  {new Intl.DateTimeFormat("ru-RU", { month: "short" })
                    .format(new Date(`${item.due_date}T12:00:00`))
                    .replace(".", "")}
                </span>
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>Ежемесячно</small>
              </span>
              <b>{money(item.amount, item.currency)}</b>
              <ChevronRight size={16} />
            </button>
          ))
        ) : (
          <div className="upcoming-empty">
            <CheckCircle2 size={23} />
            <span>Ближайших неоплаченных платежей нет.</span>
          </div>
        )}
      </div>
    </section>
  );
}
export function Calendar({
  month,
  today,
  items,
  onSelect,
}: {
  month: string;
  today: string;
  items: Occurrence[];
  onSelect: (i: Occurrence) => void;
}) {
  const first = new Date(`${month}-01T12:00:00`);
  const offset = (first.getDay() + 6) % 7;
  const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  const cells = Math.ceil((offset + days) / 7) * 7;
  const dates = [...new Set(items.map((item) => item.due_date))].sort();
  return (
    <section className="calendar-card">
      <div className="simple-card-heading">
        <h2>{monthLabel(month)}</h2>
        <div className="calendar-legend">
          <span>
            <i className="pending" /> К оплате
          </span>
          <span>
            <i className="paid" /> Оплачено
          </span>
          <span>
            <i className="overdue" /> Просрочено
          </span>
        </div>
      </div>
      <div className="calendar-desktop">
        <div className="calendar-weekdays">
          {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => (
            <span key={d}>{d}</span>
          ))}
        </div>
        <div className="calendar-grid">
          {Array.from({ length: cells }, (_, index) => {
            const day = index - offset + 1;
            const date = `${month}-${String(day).padStart(2, "0")}`;
            const valid = day > 0 && day <= days;
            const dayItems = items.filter((i) => i.due_date === date);
            return (
              <div
                key={index}
                className={`calendar-day ${valid ? "" : "outside"} ${date === today ? "is-today" : ""}`}
              >
                <span className="calendar-number">{valid ? day : ""}</span>
                {dayItems.map((item) => (
                  <button
                    className={`calendar-event ${item.status}`}
                    key={item.id}
                    onClick={() => onSelect(item)}
                    title={`${item.name} — ${money(item.amount, item.currency)}`}
                  >
                    <span>{item.name}</span>
                    <strong>{money(item.amount, item.currency)}</strong>
                    <span className="calendar-event-state">
                      {item.is_paid
                        ? "Оплачено"
                        : item.status === "overdue"
                          ? "Просрочено"
                          : "К оплате"}
                    </span>
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      </div>
      <div className="calendar-agenda">
        {dates.length ? (
          dates.map((date) => (
            <section
              className={`agenda-day ${date === today ? "today" : ""}`}
              key={date}
              aria-label={dateLabel(date, true)}
            >
              <header className="agenda-date">
                <time className="agenda-day-number" dateTime={date}>
                  {Number(date.slice(8))}
                </time>
                <div>
                  <h3>
                    {dateLabel(date, !date.startsWith(today.slice(0, 4)))}
                  </h3>
                  <span>
                    {new Intl.DateTimeFormat("ru-RU", {
                      weekday: "long",
                    }).format(new Date(`${date}T12:00:00`))}
                  </span>
                </div>
                {date === today && (
                  <span className="agenda-today-label">Сегодня</span>
                )}
              </header>
              <ul>
                {items
                  .filter((item) => item.due_date === date)
                  .sort((a, b) => a.name.localeCompare(b.name, "ru"))
                  .map((item) => (
                    <li key={item.id}>
                      <button
                        className="agenda-payment"
                        onClick={() => onSelect(item)}
                      >
                        <span className="agenda-payment-info">
                          <strong>{item.name}</strong>
                          <small>
                            {item.paid_at
                              ? `Оплачено ${dateLabel(item.paid_at)}`
                              : item.status === "overdue"
                                ? `Просрочка: ${item.overdue_days} ${plural(item.overdue_days, ["день", "дня", "дней"])}`
                                : item.recurring_active
                                  ? "Ежемесячный платёж"
                                  : "Учёт прекращён"}
                          </small>
                        </span>
                        <span className="agenda-payment-totals">
                          <strong>{money(item.amount, item.currency)}</strong>
                          <Status item={item} />
                        </span>
                      </button>
                    </li>
                  ))}
              </ul>
            </section>
          ))
        ) : (
          <Empty
            title="В этом месяце платежей нет"
            text="Выберите другой месяц."
          />
        )}
      </div>
    </section>
  );
}
export function Team({
  users,
  session,
  onEdit,
  onDelete,
  invitations,
  onRevokeInvitation,
}: {
  users: User[];
  session: Session;
  onEdit: (u: User) => void;
  onDelete: (u: User) => void;
  invitations: Invitation[];
  onRevokeInvitation: (invitation: Invitation) => void;
}) {
  return (
    <section className="team-card">
      <div className="table-scroll">
        <table className="team-table">
          <thead>
            <tr>
              <th>УЧАСТНИК</th>
              <th>ЛОГИН</th>
              <th>РОЛЬ</th>
              <th>ДОСТУП</th>
              <th>
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="admin-row">
              <td>
                <div className="team-person">
                  <span className="avatar">
                    {initials(session.user.full_name || session.user.username)}
                  </span>
                  <span>
                    <strong>
                      {session.user.full_name || session.user.username}
                    </strong>
                    <small>Это вы</small>
                  </span>
                </div>
              </td>
              <td>{session.user.username}</td>
              <td>
                <span className="role-badge admin">
                  <ShieldCheck size={13} /> Администратор
                </span>
              </td>
              <td>
                <span className="access-label">Все платежи и участники</span>
              </td>
              <td>
                <LockKeyhole size={15} className="muted" />
              </td>
            </tr>
            {users
              .filter((u) => u.role !== "admin")
              .map((user) => (
                <tr key={user.id}>
                  <td>
                    <div className="team-person">
                      <span className="avatar user-avatar">
                        {initials(user.full_name || user.username)}
                      </span>
                      <strong>{user.full_name || user.username}</strong>
                    </div>
                  </td>
                  <td>{user.username}</td>
                  <td>
                    <span className="role-badge">Пользователь</span>
                  </td>
                  <td>
                    <span className="access-label">
                      Управление своими платежами
                    </span>
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="icon-button"
                        aria-label={`Изменить пользователя ${user.username}`}
                        onClick={() => onEdit(user)}
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        className="icon-button danger-icon"
                        aria-label={`Удалить пользователя ${user.username}`}
                        onClick={() => onDelete(user)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <div className="invitations-section">
        <h2>
          Приглашения <span>{invitations.length}</span>
        </h2>
        {invitations.length ? (
          <div className="table-scroll">
            <table className="invitations-table">
              <thead>
                <tr>
                  <th>Участник</th>
                  <th>Логин</th>
                  <th>Действует до</th>
                  <th>Статус</th>
                  <th>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {invitations.map((invitation) => (
                  <tr key={invitation.id}>
                    <td>
                      <strong>{invitation.full_name}</strong>
                    </td>
                    <td>{invitation.username}</td>
                    <td>{invitationDate(invitation.expires_at)}</td>
                    <td>
                      <span className="role-badge">
                        {
                          {
                            active: "Ожидает активации",
                            accepted: "Принято",
                            revoked: "Отозвано",
                            expired: "Истекло",
                          }[invitation.status]
                        }
                      </span>
                    </td>
                    <td>
                      {invitation.status === "active" && (
                        <button
                          className="text-button"
                          onClick={() => onRevokeInvitation(invitation)}
                        >
                          Отозвать
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>Приглашений пока нет.</p>
        )}
      </div>
    </section>
  );
}
export function SettingsPanel({
  settings,
  activeCount,
  onActivity,
  onChangePassword,
  canConfigureTelegram,
  onTelegramChanged,
}: {
  settings: Settings | null;
  activeCount: number;
  onActivity?: () => void;
  onChangePassword?: () => void;
  canConfigureTelegram?: boolean;
  onTelegramChanged?: () => void;
}) {
  return (
    <div className="settings-grid">
      {canConfigureTelegram ? (
        <TelegramConnection onChanged={onTelegramChanged} />
      ) : (
        <section className="settings-card">
          <div className="settings-icon">
            <BrandIcon name="telegram" size={22} />
          </div>
          <div className="settings-card-title">
            <h2>Напоминания в Telegram</h2>
            <span
              className={`status ${settings?.telegram_configured ? "paid" : "pending"}`}
            >
              <i />
              {settings?.telegram_configured ? "Подключено" : "Не настроено"}
            </span>
          </div>
          <p>
            Бот{" "}
            <a
              href="https://t.me/aezainkeep_bot"
              target="_blank"
              rel="noopener noreferrer"
            >
              @aezainkeep_bot
            </a>{" "}
            отправляет уведомления о платежах в общий чат команды.
          </p>
          <div className="setting-line">
            <span>Когда напоминать</span>
            <strong>За {settings?.reminder_days || 3} дня до срока</strong>
          </div>
          <div className="setting-line">
            <span>Оплаченные периоды</span>
            <strong>Не напоминаем</strong>
          </div>
        </section>
      )}
      <section className="settings-card">
        <div className="settings-icon gray">
          <BrandIcon name="settings" size={22} />
        </div>
        <h2>Параметры учёта</h2>
        <div className="setting-line">
          <span>Часовой пояс</span>
          <strong>{settings?.timezone || "—"}</strong>
        </div>
        <div className="setting-line">
          <span>Сегодня в системе</span>
          <strong>{settings ? dateLabel(settings.today, true) : "—"}</strong>
        </div>
        <div className="setting-line">
          <span>Валюты</span>
          <strong>RUB · USD · EUR</strong>
        </div>
        <div className="setting-line">
          <span>Регулярных платежей</span>
          <strong>{activeCount}</strong>
        </div>
        {onChangePassword && (
          <button
            className="button secondary settings-password-button"
            onClick={onChangePassword}
          >
            Сменить пароль
          </button>
        )}
      </section>
      <a
        className="api-docs-link"
        href="/api/v1/docs"
        target="_blank"
        rel="noreferrer"
      >
        <ReceiptText size={16} /> Документация API <ExternalLink size={14} />
      </a>
      {onActivity && (
        <button
          className="api-docs-link settings-activity-link"
          onClick={onActivity}
        >
          <ScrollText size={16} /> История напоминаний{" "}
          <ArrowUpRight size={14} />
        </button>
      )}
    </div>
  );
}
