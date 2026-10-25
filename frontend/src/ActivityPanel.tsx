import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type { KeyboardEvent } from "react";
import {
  Bell,
  CircleAlert,
  FileClock,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";
import { api, ApiError } from "./api";
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
type LoadStatus = {
  loading: boolean;
  loaded: boolean;
  error: string;
  updatedAt: string | null;
};
const PAGE_SIZE = 20;
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
const emptyStatus = (): LoadStatus => ({
  loading: true,
  loaded: false,
  error: "",
  updatedAt: null,
});
const actionInfo = (value: string) =>
  Object.hasOwn(actionNames, value)
    ? actionNames[value]
    : { label: "Действие системы", category: "other" };
const stateName = (value: string) =>
  Object.hasOwn(reminderStates, value)
    ? reminderStates[value]
    : "Неизвестный статус";
const who = (value: string) => (value === "system" ? "Система" : value);
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;
const isInteger = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value);
function isAction(value: unknown): value is ActionRecord {
  return (
    isObject(value) &&
    isInteger(value.id) &&
    typeof value.username === "string" &&
    typeof value.action === "string" &&
    typeof value.description === "string" &&
    typeof value.created_at === "string"
  );
}
function isReminder(value: unknown): value is ReminderRecord {
  return (
    isObject(value) &&
    isInteger(value.id) &&
    isInteger(value.occurrence_id) &&
    typeof value.name === "string" &&
    typeof value.due_date === "string" &&
    typeof value.state === "string" &&
    isInteger(value.attempts) &&
    value.attempts >= 0 &&
    typeof value.next_attempt_at === "number" &&
    (value.sent_at === null || typeof value.sent_at === "string") &&
    (value.last_error === null || typeof value.last_error === "string")
  );
}
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
  const [activities, setActivities] = useState<ActionRecord[]>([]);
  const [reminders, setReminders] = useState<ReminderRecord[]>([]);
  const [statuses, setStatuses] = useState<Record<Tab, LoadStatus>>({
    activity: emptyStatus(),
    reminders: emptyStatus(),
  });
  const [category, setCategory] = useState<Category>("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [queries, setQueries] = useState<Record<Tab, string>>({
    activity: "",
    reminders: "",
  });
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const controllers = useRef<Partial<Record<Tab, AbortController>>>({});
  const versions = useRef<Record<Tab, number>>({ activity: 0, reminders: 0 });
  const tabs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});

  const load = useCallback(async (kind: Tab) => {
    controllers.current[kind]?.abort();
    const controller = new AbortController();
    controllers.current[kind] = controller;
    const version = ++versions.current[kind];
    setStatuses((previous) => ({
      ...previous,
      [kind]: { ...previous[kind], loading: true, error: "" },
    }));
    try {
      const data = await api<unknown>(
        kind === "activity" ? "/activity?limit=100" : "/reminders",
        { signal: controller.signal },
      );
      if (controller.signal.aborted || versions.current[kind] !== version)
        return;
      if (
        !Array.isArray(data) ||
        (kind === "activity" ? !data.every(isAction) : !data.every(isReminder))
      ) {
        throw new Error("invalid_activity_response");
      }
      if (kind === "activity")
        setActivities((data as ActionRecord[]).slice(0, 100));
      else setReminders((data as ReminderRecord[]).slice(0, 100));
      setStatuses((previous) => ({
        ...previous,
        [kind]: {
          loading: false,
          loaded: true,
          error: "",
          updatedAt: new Date().toISOString(),
        },
      }));
    } catch (error) {
      if (controller.signal.aborted || versions.current[kind] !== version)
        return;
      const message =
        error instanceof ApiError
          ? error.message
          : "Сервер вернул неполные данные. Повторите загрузку.";
      setStatuses((previous) => ({
        ...previous,
        [kind]: { ...previous[kind], loading: false, error: message },
      }));
    }
  }, []);

  useEffect(() => {
    void load("activity");
    void load("reminders");
    return () => {
      controllers.current.activity?.abort();
      controllers.current.reminders?.abort();
    };
  }, [load]);

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
  const filteredActivities = useMemo(() => {
    const query = queries.activity.trim().toLocaleLowerCase("ru-RU");
    return activities.filter(
      (row) =>
        (category === "all" || actionInfo(row.action).category === category) &&
        (!query ||
          [who(row.username), actionInfo(row.action).label, row.description]
            .join(" ")
            .toLocaleLowerCase("ru-RU")
            .includes(query)),
    );
  }, [activities, category, queries.activity]);
  const filteredReminders = useMemo(() => {
    const query = queries.reminders.trim().toLocaleLowerCase("ru-RU");
    return reminders.filter(
      (row) =>
        (stateFilter === "all" || row.state === stateFilter) &&
        (!query ||
          [row.name, stateName(row.state), reminderDetail(row)]
            .join(" ")
            .toLocaleLowerCase("ru-RU")
            .includes(query)),
    );
  }, [reminders, queries.reminders, stateFilter]);
  const status = statuses[tab];
  const sourceCount = tab === "activity" ? activities.length : reminders.length;
  const filteredCount =
    tab === "activity" ? filteredActivities.length : filteredReminders.length;
  const actionRows = filteredActivities.slice(0, visibleCount);
  const reminderRows = filteredReminders.slice(0, visibleCount);
  const metrics =
    tab === "activity"
      ? [
          ["Загружено записей", activities.length],
          [
            "Платежи",
            activities.filter(
              (row) => actionInfo(row.action).category === "payments",
            ).length,
          ],
          [
            "Пользователи",
            activities.filter(
              (row) => actionInfo(row.action).category === "users",
            ).length,
          ],
          [
            "Вход и выход",
            activities.filter(
              (row) => actionInfo(row.action).category === "access",
            ).length,
          ],
        ]
      : [
          ["Загружено записей", reminders.length],
          [
            "Отправлено",
            reminders.filter((row) => row.state === "sent").length,
          ],
          [
            "В обработке",
            reminders.filter((row) =>
              ["pending", "sending", "retry"].includes(row.state),
            ).length,
          ],
          [
            "Требуют проверки",
            reminders.filter((row) => row.state === "unknown").length,
          ],
        ];

  const changeTab = (next: Tab) => {
    setTab(next);
    setVisibleCount(PAGE_SIZE);
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
    setVisibleCount(PAGE_SIZE);
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
          disabled={status.loading}
          onClick={() => {
            void load(tab);
          }}
        >
          <RefreshCw
            size={16}
            aria-hidden="true"
            className={status.loading ? "activity-panel__spin" : undefined}
          />
          {status.loading && status.loaded ? "Обновляем…" : "Обновить"}
        </button>
      </div>
      <p className="activity-panel__scope">
        Последние 100 записей · время {timezone}
      </p>

      {status.loaded && (
        <dl
          className="activity-panel__metrics"
          aria-label="Показатели загруженной выборки"
        >
          {metrics.map(([label, count]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{count}</dd>
            </div>
          ))}
        </dl>
      )}

      {(["activity", "reminders"] as const).map((kind) => (
        <div
          key={kind}
          id={`${id}-${kind}-panel`}
          role="tabpanel"
          aria-labelledby={`${id}-${kind}-tab`}
          hidden={tab !== kind}
          aria-busy={statuses[kind].loading}
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
                        setVisibleCount(PAGE_SIZE);
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
                        setVisibleCount(PAGE_SIZE);
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
                      : "Поиск по платежу или состоянию напоминания"}
                  </span>
                  <Search size={17} aria-hidden="true" />
                  <input
                    id={`${id}-search`}
                    type="search"
                    maxLength={256}
                    value={queries[tab]}
                    placeholder={
                      tab === "activity"
                        ? "Имя, платёж или действие"
                        : "Название платежа или состояние"
                    }
                    onChange={(event) => {
                      setQueries((previous) => ({
                        ...previous,
                        [tab]: event.target.value,
                      }));
                      setVisibleCount(PAGE_SIZE);
                    }}
                  />
                </label>
              </div>

              {status.error && (
                <div className="activity-panel__error" role="alert">
                  <CircleAlert size={20} aria-hidden="true" />
                  <div>
                    <strong>
                      Не удалось обновить{" "}
                      {tab === "activity" ? "журнал" : "напоминания"}
                    </strong>
                    <p>{status.error}</p>
                    {status.loaded && (
                      <p>Показаны данные последней успешной загрузки.</p>
                    )}
                  </div>
                  <button
                    type="button"
                    className="activity-panel__button"
                    disabled={status.loading}
                    onClick={() => {
                      void load(tab);
                    }}
                  >
                    Повторить
                  </button>
                </div>
              )}
              {status.loading && !status.loaded ? (
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
              ) : status.loaded && filteredCount === 0 ? (
                <div className="activity-panel__empty">
                  {tab === "activity" ? (
                    <FileClock size={30} aria-hidden="true" />
                  ) : (
                    <Bell size={30} aria-hidden="true" />
                  )}
                  <h3>
                    {sourceCount
                      ? "Ничего не найдено"
                      : tab === "activity"
                        ? "В журнале пока пусто"
                        : "Напоминаний пока нет"}
                  </h3>
                  <p>
                    {sourceCount
                      ? "Измените запрос или сбросьте фильтры."
                      : tab === "activity"
                        ? "Здесь появятся входы в модуль и изменения платежей и пользователей."
                        : "Задания появляются за три дня до срока неоплаченного периода."}
                  </p>
                  {sourceCount > 0 && (
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
                status.loaded && (
                  <>
                    <div
                      className="activity-panel__results"
                      role="status"
                      aria-live="polite"
                    >
                      {filteredCount} из {sourceCount} записей
                    </div>
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
                    <div className="activity-panel__footer">
                      <span>
                        Показано {Math.min(visibleCount, filteredCount)} из{" "}
                        {filteredCount}
                      </span>
                      {visibleCount < filteredCount && (
                        <button
                          type="button"
                          className="activity-panel__button"
                          onClick={() =>
                            setVisibleCount((previous) => previous + PAGE_SIZE)
                          }
                        >
                          Показать ещё
                        </button>
                      )}
                    </div>
                  </>
                )
              )}
              {status.updatedAt && (
                <p className="activity-panel__updated">
                  Загружено {stamp(status.updatedAt)}
                </p>
              )}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
