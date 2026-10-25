import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  ReceiptText,
  ScrollText,
} from "lucide-react";
import BrandIcon from "./BrandIcon";
import type { Currency, Occurrence, Settings } from "./api";
import TelegramDestinations from "./TelegramDestinations";
import { dateLabel, money } from "./ui";

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
export { default as Calendar } from "./CalendarPanel";
export { default as Team } from "./TeamPanel";
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
        <TelegramDestinations onChanged={onTelegramChanged} />
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
            отправляет уведомления о платежах в подключённые группы.
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
