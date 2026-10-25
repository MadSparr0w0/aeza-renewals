import { useCallback, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  Bell,
  CircleAlert,
  FileClock,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";
import { Pagination, useDebounced, usePage } from "./DataControls";
import "./ActivityPanel.css";

type Tab = "activity" | "reminders";
type Category = "all" | "payments" | "users" | "access";
type ActionRecord = {
  id: number;
  username: string;
  action: string;
  description: string;
  created_at: string;
};
type ReminderRecord = {
  id: number;
  occurrence_id: number;
  name: string;
  due_date: string;
  state: string;
  attempts: number;
  next_attempt_at: number;
  sent_at: string | null;
  last_error: string | null;
};
const PAGE_SIZE = 30;
const actionNames: Record<
  string,
  { label: string; category: Category | "other" }
> = {
  login: { label: "Вход в систему", category: "access" },
  logout: { label: "Выход из системы", category: "access" },
  payment_created: {
    label: "Регулярный платёж добавлен",
    category: "payments",
  },
  payment_updated: {
    label: "Условия платежа изменены",
    category: "payments",
  },
  payment_archived: {
    label: "Регулярный платёж прекращён",
    category: "payments",
  },
  payment_paid: { label: "Оплата подтверждена", category: "payments" },
  payment_unpaid: { label: "Отметка об оплате отменена", category: "payments" },
  user_created: { label: "Создан пользователь", category: "users" },
  user_updated: { label: "Пользователь изменён", category: "users" },
  user_deleted: { label: "Пользователь удалён", category: "users" },
  invitation_created: { label: "Создано приглашение", category: "users" },
  invitation_revoked: { label: "Приглашение отозвано", category: "users" },
  invitation_accepted: { label: "Приглашение принято", category: "users" },
  password_changed: { label: "Пароль изменён", category: "access" },
  telegram_login: { label: "Вход через Telegram", category: "access" },
  telegram_identity_linked: { label: "Telegram привязан", category: "access" },
  telegram_identity_unlinked: { label: "Telegram отвязан", category: "access" },
  telegram_link_requested: {
    label: "Создан код подключения чата",
    category: "access",
  },
  telegram_connected: { label: "Чат Telegram подключён", category: "access" },
  telegram_disconnected: { label: "Чат Telegram отключён", category: "access" },
  demo_seeded: {
    label: "Загружены демонстрационные данные",
    category: "other",
  },
};
const reminderStates: Record<string, string> = {
  pending: "В очереди",
  sending: "Отправляется",
  retry: "Ожидает повтора",
  sent: "Отправлено",
  cancelled: "Отменено",
  unknown: "Требует проверки",
};
const reminderErrors: Record<string, string> = {
  telegram_response_too_large:
    "Ответ Telegram превышает допустимый размер. Результат доставки неизвестен.",
  telegram_invalid_response:
    "Telegram вернул некорректный ответ. Результат доставки неизвестен.",
  telegram_rate_limited: "Telegram ограничил частоту запросов. Повтор отложен.",
  telegram_unavailable: "Telegram временно недоступен. Повтор отложен.",
  telegram_rejected:
    "Telegram отклонил отправку. Проверьте настройки бота и права в чате.",
  telegram_delivery_unknown: "Подтверждение доставки не получено.",
  telegram_connect_failed: "Не удалось установить соединение с Telegram.",
  lease_expired_delivery_unknown:
    "Обработка прервалась до подтверждения доставки.",
};
const actionInfo = (value: string) =>
  Object.hasOwn(actionNames, value)
    ? actionNames[value]
    : { label: "Действие системы", category: "other" };
const stateName = (value: string) =>
  Object.hasOwn(reminderStates, value)
    ? reminderStates[value]
    : "Неизвестный статус";
const who = (value: string) => (value === "system" ? "Система" : value);
function reminderDetail(row: ReminderRecord): string {
  if (row.state === "unknown")
    return "Требует проверки в Telegram, автоматический повтор отключён.";
  if (row.state === "sent") return "Telegram подтвердил отправку сообщения.";
  if (row.state === "cancelled")
    return "Напоминание отменено: период оплачен или регулярный платёж прекращён.";
  if (row.last_error)
    return Object.hasOwn(reminderErrors, row.last_error)
      ? reminderErrors[row.last_error]
      : "Возникла ошибка доставки. Проверьте настройки и состояние Telegram.";
  if (row.state === "pending") return "Ожидает отправки.";
  if (row.state === "sending") return "Ожидаем подтверждение Telegram.";
  if (row.state === "retry") return "Отправка будет повторена после паузы.";
  return "Состояние доставки не определено. Требуется проверка.";
}

export default function ActivityPanel({ timezone }: { timezone: string }) {
  const id = useId();
  const [tab, setTab] = useState<Tab>("activity");
  const [category, setCategory] = useState<Category>("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [queries, setQueries] = useState<Record<Tab, string>>({
    activity: "",
    reminders: "",
  });
  const [offsets, setOffsets] = useState<Record<Tab, number>>({
    activity: 0,
    reminders: 0,
  });
  const tabs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const activitySearch = useDebounced(queries.activity.trim(), 300);
  const reminderSearch = useDebounced(queries.reminders.trim(), 300);
  const activityReady = activitySearch === queries.activity.trim();
  const reminderReady = reminderSearch === queries.reminders.trim();
  const activity = usePage<ActionRecord>(
    tab === "activity" && activityReady
      ? "/activity/page?" +
          new URLSearchParams({
            category,
            search: activitySearch,
            limit: String(PAGE_SIZE),
            offset: String(offsets.activity),
          })
      : null,
  );
  const reminders = usePage<ReminderRecord>(
    tab === "reminders" && reminderReady
      ? "/reminders/page?" +
          new URLSearchParams({
            state: stateFilter,
            search: reminderSearch,
            limit: String(PAGE_SIZE),
            offset: String(offsets.reminders),
          })
      : null,
  );
  const current = tab === "activity" ? activity : reminders;
  const loading =
    current.loading || !(tab === "activity" ? activityReady : reminderReady);
  const hasFilters =
    Boolean(queries[tab].trim()) ||
    (tab === "activity" ? category !== "all" : stateFilter !== "all");
  const actionRows = activity.page?.items || [];
  const reminderRows = reminders.page?.items || [];
  const rowCount = current.page?.items.length || 0;
  const setOffset = (offset: number) => {
    setOffsets((previous) => ({ ...previous, [tab]: offset }));
  };

  const stamp = useCallback(
    (value: string | null): string => {
      if (!value) return "Не отправлено";
      const parsed = new Date(value);
      if (!Number.isFinite(parsed.getTime())) return "Дата не указана";
      try {
        return new Intl.DateTimeFormat("ru-RU", {
          dateStyle: "short",
          timeStyle: "short",
          timeZone: timezone,
        }).format(parsed);
      } catch {
        return "Дата недоступна";
      }
    },
    [timezone],
  );
  const dueDate = (value: string): string => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Не указан";
    const parsed = new Date(`${value}T12:00:00Z`);
    if (
      !Number.isFinite(parsed.getTime()) ||
      parsed.toISOString().slice(0, 10) !== value
    )
      return "Не указан";
    // A due date is a calendar date, so it must not shift across timezones.
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(parsed);
  };
  const changeTab = (next: Tab) => {
    setTab(next);
  };
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    let next: Tab | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight")
      next = tab === "activity" ? "reminders" : "activity";
    if (event.key === "Home") next = "activity";
    if (event.key === "End") next = "reminders";
    if (next) {
      event.preventDefault();
      changeTab(next);
      tabs.current[next]?.focus();
    }
  };
  const resetFilters = () => {
    setQueries((previous) => ({ ...previous, [tab]: "" }));
    if (tab === "activity") setCategory("all");
    else setStateFilter("all");
    setOffset(0);
  };
  const stateBadge = (row: ReminderRecord) => {
    const state = Object.hasOwn(reminderStates, row.state)
      ? row.state
      : "unrecognized";
    return (
      <span className={`activity-panel__state activity-panel__state--${state}`}>
        {stateName(row.state)}
      </span>
    );
  };

  return (
    <section
      className="activity-panel"
      aria-label="Журнал действий и напоминаний"
    >
      <div className="activity-panel__topbar">
        <div
          className="activity-panel__tabs"
          role="tablist"
          aria-label="Раздел журнала"
        >
          {(["activity", "reminders"] as const).map((kind) => (
            <button
              key={kind}
              ref={(node) => {
                tabs.current[kind] = node;
              }}
              type="button"
              role="tab"
              id={`${id}-${kind}-tab`}
              aria-selected={tab === kind}
              aria-controls={`${id}-${kind}-panel`}
              tabIndex={tab === kind ? 0 : -1}
              onKeyDown={onTabKeyDown}
              onClick={() => changeTab(kind)}
            >
              {kind === "activity" ? (
                <FileClock size={17} aria-hidden="true" />
              ) : (
                <Bell size={17} aria-hidden="true" />
              )}
              {kind === "activity" ? "Действия" : "Напоминания"}
            </button>
          ))}
        </div>
        <button
          className="activity-panel__button activity-panel__refresh"
          type="button"
          disabled={loading}
          onClick={current.reload}
        >
          <RefreshCw
            size={16}
            aria-hidden="true"
            className={loading ? "activity-panel__spin" : undefined}
          />
          {loading ? "Загружаем…" : "Обновить"}
        </button>
      </div>
      <p className="activity-panel__scope">
        По 30 записей на страницу · время {timezone}
      </p>

      {(["activity", "reminders"] as const).map((kind) => (
        <div
          key={kind}
          id={`${id}-${kind}-panel`}
          role="tabpanel"
          aria-labelledby={`${id}-${kind}-tab`}
          hidden={tab !== kind}
          aria-busy={tab === kind && loading}
          tabIndex={0}
        >
          {tab === kind && (
            <div className="activity-panel__card">
              <div className="activity-panel__filters">
                <label
                  className="activity-panel__filter-label"
                  htmlFor={`${id}-filter`}
                >
                  <span>
                    {tab === "activity" ? "Тип действий" : "Состояние доставки"}
                  </span>
                  {tab === "activity" ? (
                    <select
                      id={`${id}-filter`}
                      value={category}
                      onChange={(event) => {
                        setCategory(event.target.value as Category);
                        setOffset(0);
                      }}
                    >
                      <option value="all">Все действия</option>
                      <option value="payments">Платежи</option>
                      <option value="users">Пользователи</option>
                      <option value="access">Вход и выход</option>
                    </select>
                  ) : (
                    <select
                      id={`${id}-filter`}
                      value={stateFilter}
                      onChange={(event) => {
                        setStateFilter(event.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="all">Все состояния</option>
                      {Object.entries(reminderStates).map(([state, label]) => (
                        <option key={state} value={state}>
                          {label}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
                <label
                  className="activity-panel__search"
                  htmlFor={`${id}-search`}
                >
                  <span className="activity-panel__sr">
                    {tab === "activity"
                      ? "Поиск по имени, действию или описанию"
                      : "Поиск по названию платежа"}
                  </span>
                  <Search size={17} aria-hidden="true" />
                  <input
                    id={`${id}-search`}
                    type="search"
                    maxLength={160}
                    value={queries[tab]}
                    placeholder={
                      tab === "activity"
                        ? "Имя, платёж или действие"
                        : "Название платежа"
                    }
                    onChange={(event) => {
                      setQueries((previous) => ({
                        ...previous,
                        [tab]: event.target.value,
                      }));
                      setOffset(0);
                    }}
                  />
                </label>
              </div>

              {current.error && (
                <div className="activity-panel__error" role="alert">
                  <CircleAlert size={20} aria-hidden="true" />
                  <div>
                    <strong>
                      Не удалось обновить{" "}
                      {tab === "activity" ? "журнал" : "напоминания"}
                    </strong>
                    <p>{current.error}</p>
                  </div>
                  <button
                    type="button"
                    className="activity-panel__button"
                    disabled={loading}
                    onClick={current.reload}
                  >
                    Повторить
                  </button>
                </div>
              )}
              {loading ? (
                <div className="activity-panel__empty" role="status">
                  <LoaderCircle
                    size={28}
                    aria-hidden="true"
                    className="activity-panel__spin"
                  />
                  <p>
                    Загружаем {tab === "activity" ? "действия" : "напоминания"}…
                  </p>
                </div>
              ) : current.page && rowCount === 0 ? (
                <div className="activity-panel__empty">
                  {tab === "activity" ? (
                    <FileClock size={30} aria-hidden="true" />
                  ) : (
                    <Bell size={30} aria-hidden="true" />
                  )}
                  <h3>
                    {hasFilters
                      ? "Ничего не найдено"
                      : tab === "activity"
                        ? "В журнале пока пусто"
                        : "Напоминаний пока нет"}
                  </h3>
                  <p>
                    {hasFilters
                      ? "Измените запрос или сбросьте фильтры."
                      : tab === "activity"
                        ? "Здесь появятся входы в модуль и изменения платежей и пользователей."
                        : "Задания появляются за три дня до срока неоплаченного периода."}
                  </p>
                  {hasFilters && (
                    <button
                      className="activity-panel__button"
                      type="button"
                      onClick={resetFilters}
                    >
                      Сбросить фильтры
                    </button>
                  )}
                </div>
              ) : (
                current.page && (
                  <>
                    {tab === "activity" ? (
                      <>
                        <div className="activity-panel__desktop">
                          <table className="activity-panel__table activity-panel__table--actions">
                            <caption className="activity-panel__sr">
                              Действия в системе: дата, пользователь, событие и
                              описание
                            </caption>
                            <thead>
                              <tr>
                                <th scope="col">Дата и время</th>
                                <th scope="col">Кто</th>
                                <th scope="col">Действие</th>
                                <th scope="col">Описание</th>
                              </tr>
                            </thead>
                            <tbody>
                              {actionRows.map((row) => (
                                <tr key={row.id}>
                                  <td>
                                    <time dateTime={row.created_at}>
                                      {stamp(row.created_at)}
                                    </time>
                                  </td>
                                  <td className="activity-panel__username">
                                    {who(row.username)}
                                  </td>
                                  <td>
                                    <strong>
                                      {actionInfo(row.action).label}
                                    </strong>
                                  </td>
                                  <td>
                                    {row.description ===
                                    actionInfo(row.action).label
                                      ? "—"
                                      : row.description}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <ol className="activity-panel__mobile">
                          {actionRows.map((row) => (
                            <li key={row.id}>
                              <article>
                                <h3>{actionInfo(row.action).label}</h3>
                                {row.description !==
                                  actionInfo(row.action).label && (
                                  <p>{row.description}</p>
                                )}
                                <dl>
                                  <div>
                                    <dt>Кто</dt>
                                    <dd>{who(row.username)}</dd>
                                  </div>
                                  <div>
                                    <dt>Когда</dt>
                                    <dd>
                                      <time dateTime={row.created_at}>
                                        {stamp(row.created_at)}
                                      </time>
                                    </dd>
                                  </div>
                                </dl>
                              </article>
                            </li>
                          ))}
                        </ol>
                      </>
                    ) : (
                      <>
                        <div className="activity-panel__desktop">
                          <table className="activity-panel__table activity-panel__table--reminders">
                            <caption className="activity-panel__sr">
                              Доставка напоминаний: платёж, срок, состояние,
                              отправка и число попыток
                            </caption>
                            <thead>
                              <tr>
                                <th scope="col">Платёж</th>
                                <th scope="col">Срок оплаты</th>
                                <th scope="col">Состояние</th>
                                <th scope="col">Отправлено</th>
                                <th scope="col">Попытки</th>
                              </tr>
                            </thead>
                            <tbody>
                              {reminderRows.map((row) => (
                                <tr key={row.id}>
                                  <td>
                                    <strong>{row.name}</strong>
                                  </td>
                                  <td>
                                    <time dateTime={row.due_date}>
                                      {dueDate(row.due_date)}
                                    </time>
                                  </td>
                                  <td>
                                    {stateBadge(row)}
                                    <p className="activity-panel__detail">
                                      {reminderDetail(row)}
                                    </p>
                                  </td>
                                  <td>
                                    {row.sent_at ? (
                                      <time dateTime={row.sent_at}>
                                        {stamp(row.sent_at)}
                                      </time>
                                    ) : (
                                      <span className="activity-panel__muted">
                                        Не отправлено
                                      </span>
                                    )}
                                  </td>
                                  <td className="activity-panel__attempts">
                                    {row.attempts}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <ol className="activity-panel__mobile">
                          {reminderRows.map((row) => (
                            <li key={row.id}>
                              <article>
                                <div className="activity-panel__mobile-title">
                                  <h3>{row.name}</h3>
                                  {stateBadge(row)}
                                </div>
                                <p className="activity-panel__detail">
                                  {reminderDetail(row)}
                                </p>
                                <dl>
                                  <div>
                                    <dt>Срок оплаты</dt>
                                    <dd>
                                      <time dateTime={row.due_date}>
                                        {dueDate(row.due_date)}
                                      </time>
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>Отправлено</dt>
                                    <dd>
                                      {row.sent_at ? (
                                        <time dateTime={row.sent_at}>
                                          {stamp(row.sent_at)}
                                        </time>
                                      ) : (
                                        "Не отправлено"
                                      )}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>Попытки</dt>
                                    <dd>{row.attempts}</dd>
                                  </div>
                                </dl>
                              </article>
                            </li>
                          ))}
                        </ol>
                      </>
                    )}
                  </>
                )
              )}
              {(current.page || loading) && (
                <Pagination
                  page={current.page}
                  offset={offsets[tab]}
                  onOffset={setOffset}
                  loading={loading}
                />
              )}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
