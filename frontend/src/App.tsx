import { useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { api, ApiError, setCsrf } from "./api";
import type {
  Currency,
  Invitation,
  Occurrence,
  Recurring,
  Session,
  Settings,
  Summary,
  User,
} from "./api";
import {
  currencies,
  dateLabel,
  initials,
  Logo,
  Metric,
  money,
  monthLabel,
  moveMonth,
  plural,
  symbols,
} from "./ui";
import {
  AddPayment,
  Confirm,
  EditRecurringModal,
  Login,
  PaymentModal,
  UserModal,
} from "./forms";
import {
  Calendar,
  OverviewBottom,
  OverviewMiddle,
  SettingsPanel,
  Team,
} from "./panels";
import Payments from "./Payments";
import ActivityPanel from "./ActivityPanel";
import {
  ChangePasswordModal,
  InvitationAccept,
  InvitationModal,
} from "./AccountForms";
import BrandIcon from "./BrandIcon";
import type { BrandIconName } from "./BrandIcon";

type View =
  | "overview"
  | "payments"
  | "calendar"
  | "history"
  | "team"
  | "activity"
  | "settings";
const viewNames: Record<View, string> = {
  overview: "Обзор",
  payments: "Регулярные платежи",
  calendar: "Календарь",
  history: "История оплат",
  team: "Пользователи",
  activity: "Журнал",
  settings: "Настройки",
};
const pageTitles: Record<View, string> = {
  overview: "Регулярные платежи",
  payments: "Регулярные платежи",
  calendar: "Календарь платежей",
  history: "История оплат",
  team: "Участники команды",
  activity: "Журнал действий",
  settings: "Настройки",
};
const descriptions: Record<View, string> = {
  overview: "Плановые суммы, сроки и отметки об оплате.",
  payments: "Платежи за выбранный месяц.",
  calendar: "Сроки оплаты по дням месяца.",
  history: "Оплаты за предыдущие месяцы.",
  team: "Учётные записи и права доступа участников команды.",
  activity: "Действия пользователей и статусы отправки напоминаний.",
  settings: "Напоминания и параметры учёта платежей.",
};
const navigationKey = "invoice-keeper-navigation";
function restoreNavigation(): { view: View; month: string } {
  try {
    const saved = JSON.parse(
      sessionStorage.getItem(navigationKey) || "null",
    ) as { view?: unknown; month?: unknown } | null;
    const view =
      typeof saved?.view === "string" && Object.hasOwn(viewNames, saved.view)
        ? (saved.view as View)
        : "overview";
    const month =
      typeof saved?.month === "string" &&
      /^\d{4}-(0[1-9]|1[0-2])$/.test(saved.month) &&
      Number(saved.month.slice(0, 4)) >= 2000 &&
      Number(saved.month.slice(0, 4)) <= new Date().getFullYear() + 5
        ? saved.month
        : "";
    return { view, month };
  } catch {
    return { view: "overview", month: "" };
  }
}
function clearNavigation() {
  try {
    sessionStorage.removeItem(navigationKey);
  } catch {
    /* Navigation remains usable when browser storage is unavailable. */
  }
}
function readInvitationToken() {
  const value = new URLSearchParams(window.location.hash.slice(1)).get(
    "invite",
  );
  return value ? (value.length <= 512 ? value : "invalid") : null;
}
function removeInvitationFragment() {
  window.history.replaceState(
    null,
    "",
    window.location.pathname + window.location.search,
  );
}
export default function App() {
  const [savedNavigation] = useState(restoreNavigation);
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState<View>(savedNavigation.view);
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [occurrences, setOccurrences] = useState<Occurrence[]>([]);
  const [recurring, setRecurring] = useState<Recurring[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [admins, setAdmins] = useState<User[]>([]);
  const [month, setMonth] = useState(savedNavigation.month);
  const [currency, setCurrency] = useState<Currency>("RUB");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [revision, setRevision] = useState(0);
  const [addPreset, setAddPreset] = useState<string | null>(null);
  const [payment, setPayment] = useState<Occurrence | null>(null);
  const [deletePayment, setDeletePayment] = useState<Occurrence | null>(null);
  const [editSeries, setEditSeries] = useState<Recurring | null>(null);
  const [userModal, setUserModal] = useState<User | "new" | null>(null);
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  const [deleteUser, setDeleteUser] = useState<User | null>(null);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [invitationOpen, setInvitationOpen] = useState(false);
  const [revokeInvitation, setRevokeInvitation] = useState<Invitation | null>(
    null,
  );
  const [invitationToken, setInvitationToken] = useState(readInvitationToken);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [authNotice, setAuthNotice] = useState("");
  const today = settings?.today || new Date().toLocaleDateString("en-CA");
  const activeMonth = month || today.slice(0, 7);
  const isAdmin = session?.user.role === "admin";
  function resetSession() {
    clearNavigation();
    setCsrf("");
    setSession(null);
    setOccurrences([]);
    setRecurring([]);
    setUsers([]);
    setAdmins([]);
    setInvitations([]);
    setSettings(null);
    setSummary(null);
    setMonth("");
    setCurrency("RUB");
    setAddPreset(null);
    setPayment(null);
    setDeletePayment(null);
    setEditSeries(null);
    setUserModal(null);
    setAdminModalOpen(false);
    setDeleteUser(null);
    setInvitationOpen(false);
    setRevokeInvitation(null);
    setPasswordOpen(false);
    setMobileOpen(false);
    setToast("");
    setError("");
    setAuthNotice("");
    setView("overview");
  }
  useEffect(() => {
    const changed = () => setInvitationToken(readInvitationToken());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const changeView = (next: View) => {
    setView(next);
    setMobileOpen(false);
    if (next === "history") setMonth(moveMonth(today.slice(0, 7), -1));
    else if (view === "history") setMonth(today.slice(0, 7));
  };
  useEffect(() => {
    if (!session) return;
    if (["team", "activity"].includes(view) && !isAdmin) {
      setView("overview");
      return;
    }
    try {
      sessionStorage.setItem(navigationKey, JSON.stringify({ view, month }));
    } catch {
      /* Optional browser storage. */
    }
  }, [session, view, month, isAdmin]);
  useEffect(() => {
    if (!mobileOpen) return;
    const focusable = () =>
      Array.from(
        sidebarRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), a[href]",
        ) || [],
      );
    (
      sidebarRef.current?.querySelector<HTMLButtonElement>(
        ".nav-item.active",
      ) || focusable()[0]
    )?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileOpen(false);
      }
      if (event.key === "Tab") {
        const buttons = focusable();
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      menuButtonRef.current?.focus();
    };
  }, [mobileOpen]);
  useEffect(() => {
    const expire = () => resetSession();
    window.addEventListener("invoice-session-expired", expire);
    return () => window.removeEventListener("invoice-session-expired", expire);
  }, []);
  useEffect(() => {
    let mounted = true;
    if (readInvitationToken()) {
      setChecking(false);
      return;
    }
    api<Session>("/auth/me")
      .then((result) => {
        if (mounted) {
          setCsrf(result.csrf_token);
          setSession(result);
        }
      })
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401) && mounted)
          setError("Сервер недоступен. Проверьте, что приложение запущено.");
      })
      .finally(() => {
        if (mounted) setChecking(false);
      });
    return () => {
      mounted = false;
    };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    if (!session) return;
    let mounted = true;
    setLoading(true);
    setError("");
    Promise.all([
      api<Recurring[]>("/recurring-payments"),
      api<Settings>("/settings"),
    ])
      .then(([definitions, config]) => {
        if (mounted) {
          setRecurring(definitions);
          setSettings(config);
        }
      })
      .catch((e) => {
        if (mounted) {
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить данные.",
          );
          if (e instanceof ApiError && e.status === 401) setSession(null);
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [session, revision]);
  useEffect(() => {
    if (!session) return;
    let mounted = true;
    setSummary(null);
    setLoading(true);
    api<Occurrence[]>(`/payment-occurrences?month=${activeMonth}`)
      .then(async (selected) => {
        const [result, allItems] = await Promise.all([
          api<Summary>(`/payments/summary?month=${activeMonth}`),
          api<Occurrence[]>("/payment-occurrences"),
        ]);
        if (mounted) {
          setSummary(result);
          setOccurrences([
            ...allItems.filter((i) => !i.due_date.startsWith(activeMonth)),
            ...selected,
          ]);
        }
      })
      .catch((e) => {
        if (mounted) setError(e.message);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [session, activeMonth, revision]);
  useEffect(() => {
    if (!isAdmin) return;
    let mounted = true;
    setLoading(true);
    api<User[]>("/users")
      .then((result) => {
        if (mounted) setUsers(result);
      })
      .catch((e) => {
        if (mounted) setError(e.message);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [isAdmin, revision]);
  useEffect(() => {
    if (!session?.user.env_managed || view !== "team") return;
    let mounted = true;
    api<User[]>("/admins")
      .then((result) => { if (mounted) setAdmins(result); })
      .catch((reason) => { if (mounted) setError((reason as Error).message); });
    return () => { mounted = false; };
  }, [session?.user.env_managed, view, revision]);
  useEffect(() => {
    if (!isAdmin || view !== "team") return;
    let mounted = true;
    api<Invitation[]>("/invitations")
      .then((result) => {
        if (mounted) setInvitations(result);
      })
      .catch((reason) => {
        if (mounted) setError((reason as Error).message);
      });
    return () => {
      mounted = false;
    };
  }, [isAdmin, view, revision]);
  const currentItems = occurrences.filter((i) =>
    i.due_date.startsWith(activeMonth),
  );
  const currencyItems = currentItems.filter((i) => i.currency === currency);
  const nextItems = occurrences
    .filter((i) => !i.is_paid && i.recurring_active && i.due_date >= today)
    .sort((a, b) => a.due_date.localeCompare(b.due_date))
    .slice(0, 3);
  const sums = summary?.currencies[currency];
  const counts = {
    paid: currencyItems.filter((i) => i.is_paid).length,
    overdue: currencyItems.filter((i) => i.status === "overdue").length,
    unpaid: currencyItems.filter((i) => !i.is_paid).length,
  };
  const refresh = (message: string) => {
    setRevision((v) => v + 1);
    setToast(message);
  };
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      resetSession();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function stopRecurring() {
    if (!deletePayment?.can_manage) return;
    await api(`/recurring-payments/${deletePayment.recurring_payment_id}`, {
      method: "DELETE",
    });
    setDeletePayment(null);
    setPayment(null);
    refresh("Регулярный платёж прекращён. История оплат сохранена.");
  }
  async function removeUser() {
    if (!deleteUser) return;
    await api(`/users/${deleteUser.id}`, { method: "DELETE" });
    setDeleteUser(null);
    refresh("Пользователь удалён.");
  }
  if (invitationToken)
    return (
      <InvitationAccept
        token={invitationToken}
        onBack={() => {
          removeInvitationFragment();
          setInvitationToken(null);
        }}
        onAccepted={(result) => {
          resetSession();
          removeInvitationFragment();
          setInvitationToken(null);
          setCsrf(result.csrf_token);
          setSession(result);
          setChecking(false);
        }}
      />
    );
  if (checking)
    return (
      <div className="boot-screen">
        <Logo />
        <LoaderCircle size={24} className="spin" />
        <span>Загрузка…</span>
      </div>
    );
  if (!session)
    return (
      <>
        <Login
          notice={authNotice}
          onLogin={(s) => {
            setError("");
            setAuthNotice("");
            setSession(s);
          }}
        />
        {error && (
          <div role="alert" className="connection-alert">
            {error}
            <button onClick={() => setError("")} aria-label="Закрыть сообщение">
              <X size={16} />
            </button>
          </div>
        )}
      </>
    );
  const nav: { id: View; icon: BrandIconName }[] = [
    { id: "overview", icon: "overview" },
    { id: "payments", icon: "payments" },
    { id: "calendar", icon: "calendar" },
    { id: "history", icon: "history" },
  ];
  const seriesForPayment = payment
    ? recurring.find((series) => series.id === payment.recurring_payment_id)
    : undefined;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      {mobileOpen && (
        <button
          className="sidebar-backdrop"
          onClick={() => setMobileOpen(false)}
          aria-label="Закрыть меню"
        />
      )}
      <aside
        ref={sidebarRef}
        id="main-sidebar"
        className={`sidebar ${mobileOpen ? "is-open" : ""}`}
        role={mobileOpen ? "dialog" : undefined}
        aria-modal={mobileOpen ? true : undefined}
        aria-label="Навигация"
      >
        <Logo />
        <p className="nav-label">ПЛАТЕЖИ</p>
        <nav aria-label="Основная навигация">
          {nav.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${view === item.id ? "active" : ""}`}
              onClick={() => changeView(item.id)}
            >
              <BrandIcon name={item.icon} size={19} />
              <span>{viewNames[item.id]}</span>
              {item.id === "payments" &&
                currentItems.filter((i) => i.status === "overdue").length >
                  0 && (
                  <em>
                    {currentItems.filter((i) => i.status === "overdue").length}
                  </em>
                )}
            </button>
          ))}
        </nav>
        <p className="nav-label second">УПРАВЛЕНИЕ</p>
        <nav aria-label="Управление">
          {isAdmin && (
            <button
              className={`nav-item ${view === "team" ? "active" : ""}`}
              onClick={() => changeView("team")}
            >
              <BrandIcon name="users" size={19} />
              <span>Пользователи</span>
            </button>
          )}
          {isAdmin && (
            <button
              className={`nav-item ${view === "activity" ? "active" : ""}`}
              onClick={() => changeView("activity")}
            >
              <BrandIcon name="activity" size={19} />
              <span>Журнал</span>
            </button>
          )}
          <button
            className={`nav-item ${view === "settings" ? "active" : ""}`}
            onClick={() => changeView("settings")}
          >
            <BrandIcon name="settings" size={19} />
            <span>Настройки</span>
          </button>
        </nav>
        <div className="sidebar-links">
          <a
            href="https://my.aeza.ru"
            target="_blank"
            rel="noopener noreferrer"
          >
            Личный кабинет Aéza <span aria-hidden="true">↗</span>
          </a>
          <a
            href="https://my.aeza.ru/?support"
            target="_blank"
            rel="noopener noreferrer"
          >
            Поддержка Aéza <span aria-hidden="true">↗</span>
          </a>
        </div>
      </aside>
      <div className="app-body">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button menu-toggle"
              ref={menuButtonRef}
              aria-label="Открыть меню"
              aria-expanded={mobileOpen}
              aria-controls="main-sidebar"
              onClick={() => setMobileOpen(true)}
            >
              <Menu size={21} />
            </button>
            <span>Aéza</span>
            <ChevronRight size={14} />
            <strong>{viewNames[view]}</strong>
          </div>
          <div className="topbar-right">
            <span className="today-label">
              <CalendarDays size={15} />
              {dateLabel(today, true)}
            </span>
            <span className="header-divider" />
            <div className="user-info">
              <span className="avatar">
                {initials(session.user.full_name || session.user.username)}
              </span>
              <div>
                <strong>
                  {session.user.full_name || session.user.username}
                </strong>
                <span>{isAdmin ? "Администратор" : "Пользователь"}</span>
              </div>
            </div>
            <button
              className="icon-button logout"
              onClick={logout}
              aria-label="Выйти из аккаунта"
              title="Выйти"
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main
          key={view}
          id="main-content"
          className="main-content"
          tabIndex={-1}
        >
          <div className="page-heading">
            <div>
              <h1>{pageTitles[view]}</h1>
              <p>{descriptions[view]}</p>
            </div>
            {["overview", "payments", "calendar"].includes(view) && (
              <button
                className="button primary"
                onClick={() => setAddPreset("")}
              >
                <Plus size={18} /> Добавить платёж
              </button>
            )}
            {isAdmin && view === "team" && (
              <div className="page-heading-actions">
                {session.user.env_managed && (
                  <button className="button secondary" onClick={() => setAdminModalOpen(true)}>
                    <Plus size={17} /> Создать администратора
                  </button>
                )}
                <button
                  className="button secondary"
                  onClick={() => setUserModal("new")}
                >
                  <Plus size={17} />
                  Создать аккаунт
                </button>
                <button
                  className="button primary"
                  onClick={() => setInvitationOpen(true)}
                >
                  <Plus size={17} />
                  Пригласить
                </button>
              </div>
            )}
          </div>
          {error && (
            <div role="alert" className="page-error">
              <span>{error}</span>
              <button
                className="text-button"
                onClick={() => setRevision((v) => v + 1)}
              >
                <RefreshCw size={14} /> Повторить
              </button>
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="Скрыть ошибку"
              >
                <X size={17} />
              </button>
            </div>
          )}
          {["overview", "payments", "history", "calendar"].includes(view) && (
            <div className="period-toolbar">
              <div className="month-switcher">
                <button
                  className="icon-button"
                  aria-label="Предыдущий месяц"
                  onClick={() => setMonth(moveMonth(activeMonth, -1))}
                >
                  <ChevronLeft size={17} />
                </button>
                <span>
                  <CalendarDays size={17} />
                  {monthLabel(activeMonth)}
                </span>
                <button
                  className="icon-button"
                  aria-label="Следующий месяц"
                  onClick={() => setMonth(moveMonth(activeMonth, 1))}
                >
                  <ChevronRight size={17} />
                </button>
              </div>
              <div className="period-right">
                {activeMonth !== today.slice(0, 7) && (
                  <button
                    className="text-button"
                    onClick={() => setMonth(today.slice(0, 7))}
                  >
                    Текущий месяц
                  </button>
                )}
                {view === "overview" && (
                  <>
                    <span className="currency-hint">Итоги в</span>
                    <div className="currency-tabs" aria-label="Валюта итогов">
                      {currencies.map((c) => (
                        <button
                          key={c}
                          className={c === currency ? "selected" : ""}
                          onClick={() => setCurrency(c)}
                          aria-pressed={c === currency}
                        >
                          {symbols[c]} <span>{c}</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
          {view === "overview" && (
            <>
              <section className="metrics" aria-label="Итоги за месяц">
                <Metric
                  label="План на месяц"
                  value={sums ? money(sums.planned, currency) : "—"}
                  caption={`${currencyItems.length} ${plural(currencyItems.length, ["платёж", "платежа", "платежей"])} за месяц`}
                  icon={<BrandIcon name="payments" size={19} />}
                  variant="plan"
                />
                <Metric
                  label="Оплачено"
                  value={sums ? money(sums.paid, currency) : "—"}
                  caption={`${counts.paid} ${plural(counts.paid, ["платёж оплачен", "платежа оплачено", "платежей оплачено"])}`}
                  icon={<BrandIcon name="check" size={19} />}
                  variant="paid"
                />
                <Metric
                  label="Ожидают оплаты"
                  value={sums ? money(sums.unpaid, currency) : "—"}
                  caption={`${counts.unpaid} ${plural(counts.unpaid, ["платёж", "платежа", "платежей"])} без отметки`}
                  icon={<BrandIcon name="clock" size={19} />}
                  variant="unpaid"
                />
                <Metric
                  label="Просрочено"
                  value={sums ? money(sums.overdue, currency) : "—"}
                  caption={
                    counts.overdue
                      ? `${counts.overdue} ${plural(counts.overdue, ["платёж требует", "платежа требуют", "платежей требуют"])} внимания`
                      : "Нет просроченных платежей"
                  }
                  icon={<BrandIcon name="warning" size={19} />}
                  variant="overdue"
                  warning={counts.overdue > 0}
                />
              </section>
              <OverviewMiddle
                amounts={sums}
                currency={currency}
                paidCount={counts.paid}
                totalCount={currencyItems.length}
                settings={settings}
                onSettings={() => changeView("settings")}
              />
            </>
          )}
          {["overview", "payments", "history"].includes(view) && (
            <Payments
              key={view}
              items={currentItems}
              today={today}
              month={activeMonth}
              loading={loading}
              history={view === "history"}
              isAdmin={!!isAdmin}
              onAdd={() => setAddPreset("")}
              onRefresh={() => setRevision((v) => v + 1)}
              onSelect={setPayment}
              onPay={(item) =>
                item.can_manage &&
                setPayment({ ...item, is_paid: true, paid_at: today })
              }
            />
          )}
          {view === "overview" && (
            <OverviewBottom
              items={nextItems}
              onSelect={setPayment}
              onCalendar={() => changeView("calendar")}
            />
          )}
          {view === "calendar" && (
            <Calendar
              month={activeMonth}
              today={today}
              items={currentItems}
              onSelect={setPayment}
            />
          )}
          {view === "team" && isAdmin && (
            <Team
              users={users}
              session={session}
              onEdit={setUserModal}
              onDelete={setDeleteUser}
              invitations={invitations}
              admins={session.user.env_managed ? admins : undefined}
              onRevokeInvitation={setRevokeInvitation}
            />
          )}
          {view === "settings" && (
            <SettingsPanel
              settings={settings}
              activeCount={recurring.filter((p) => p.active !== false).length}
              onActivity={isAdmin ? () => changeView("activity") : undefined}
              onChangePassword={
                !session.user.env_managed ? () => setPasswordOpen(true) : undefined
              }
              canConfigureTelegram={!!isAdmin}
              onTelegramChanged={() => setRevision((value) => value + 1)}
            />
          )}
          {view === "activity" && isAdmin && (
            <ActivityPanel timezone={settings?.timezone || "Europe/Moscow"} />
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={19} />
          <span>{toast}</span>
          <button onClick={() => setToast("")} aria-label="Скрыть уведомление">
            <X size={15} />
          </button>
        </div>
      )}
      {addPreset !== null && (
        <AddPayment
          today={today}
          initialName={addPreset}
          owners={
            isAdmin
              ? [
                  ...new Map(
                    [session.user, ...users].map((owner) => [owner.id, owner]),
                  ).values(),
                ]
              : undefined
          }
          defaultOwnerId={session.user.id}
          onClose={() => setAddPreset(null)}
          onSuccess={() => {
            setAddPreset(null);
            refresh("Регулярный платёж добавлен.");
          }}
        />
      )}
      {payment && (
        <PaymentModal
          item={payment}
          today={today}
          canManage={payment.can_manage}
          onClose={() => setPayment(null)}
          onSuccess={() => {
            setPayment(null);
            refresh("Данные оплаты сохранены.");
          }}
          onDelete={() => setDeletePayment(payment)}
          onEditSeries={
            seriesForPayment?.active && seriesForPayment.can_manage
              ? () => {
                  setEditSeries(seriesForPayment);
                  setPayment(null);
                }
              : undefined
          }
        />
      )}
      {editSeries?.can_manage && (
        <EditRecurringModal
          series={editSeries}
          today={today}
          onClose={() => setEditSeries(null)}
          onSuccess={(effectiveFrom) => {
            setEditSeries(null);
            refresh(`Условия обновлены с ${dateLabel(effectiveFrom, true)}`);
          }}
        />
      )}
      {deletePayment && (
        <Confirm
          title="Прекратить регулярный платёж?"
          text={`«${deletePayment.name}»: будущие периоды и напоминания будут остановлены. История оплат сохранится.`}
          confirm="Прекратить учёт"
          onConfirm={stopRecurring}
          onClose={() => setDeletePayment(null)}
        />
      )}
      {userModal && (
        <UserModal
          user={userModal}
          onClose={() => setUserModal(null)}
          onSuccess={() => {
            setUserModal(null);
            refresh(
              userModal === "new"
                ? "Пользователь добавлен."
                : "Данные пользователя сохранены.",
            );
          }}
        />
      )}
      {adminModalOpen && (
        <UserModal
          user="new"
          newAdmin
          onClose={() => setAdminModalOpen(false)}
          onSuccess={() => {
            setAdminModalOpen(false);
            refresh("Отдельный администратор создан.");
          }}
        />
      )}
      {deleteUser && (
        <Confirm
          title="Удалить пользователя?"
          text={`Пользователь ${deleteUser.full_name || deleteUser.username} потеряет доступ к приложению. Его платежи будут переданы администратору с сохранением истории. Активные сессии будут завершены.`}
          confirm="Удалить пользователя"
          onConfirm={removeUser}
          onClose={() => setDeleteUser(null)}
        />
      )}
      {invitationOpen && isAdmin && (
        <InvitationModal
          onClose={() => setInvitationOpen(false)}
          onCreated={() => setRevision((value) => value + 1)}
        />
      )}
      {revokeInvitation && isAdmin && (
        <Confirm
          title="Отозвать приглашение?"
          text={`Ссылка для ${revokeInvitation.full_name || revokeInvitation.username} перестанет действовать.`}
          confirm="Отозвать"
          onClose={() => setRevokeInvitation(null)}
          onConfirm={async () => {
            await api(`/invitations/${revokeInvitation.id}`, {
              method: "DELETE",
            });
            setRevokeInvitation(null);
            refresh("Приглашение отозвано.");
          }}
        />
      )}
      {passwordOpen && !isAdmin && (
        <ChangePasswordModal
          onClose={() => setPasswordOpen(false)}
          onChanged={() => {
            resetSession();
            setAuthNotice("Пароль изменён. Войдите снова.");
          }}
        />
      )}
    </div>
  );
}
