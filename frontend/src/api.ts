export type Currency = "RUB" | "USD" | "EUR";
export type User = {
  id: number;
  username: string;
  full_name: string;
  role: "admin" | "user";
  env_managed: boolean;
};
export type Session = { user: User; csrf_token: string };
export type Occurrence = {
  id: number;
  recurring_payment_id: number;
  name: string;
  amount: string;
  currency: Currency;
  due_date: string;
  paid_at: string | null;
  is_paid: boolean;
  status: "paid" | "overdue" | "pending";
  overdue_days: number;
  late_days?: number;
  recurring_active: boolean;
  owner_id: number;
  owner_name: string;
  can_manage: boolean;
};
export type Recurring = {
  id: number;
  name: string;
  amount: string;
  currency: Currency;
  next_due_date: string;
  frequency: string;
  active: boolean;
  effective_from?: string;
  owner_id: number;
  owner_name: string;
  can_manage: boolean;
};
export type Invitation = {
  id: number;
  username: string;
  full_name: string;
  created_at: string;
  expires_at: string;
  status: "active" | "accepted" | "revoked" | "expired";
};
export type CurrencySummary = {
  planned: string;
  paid: string;
  unpaid: string;
  overdue: string;
};
export type Summary = {
  currencies: Record<Currency, CurrencySummary>;
  total_count: number;
  paid_count: number;
  unpaid_count: number;
  overdue_count: number;
};
export type Settings = {
  timezone: string;
  today: string;
  telegram_configured: boolean;
  reminder_days: number;
};
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
let csrf = "";
export const setCsrf = (value: string) => {
  csrf = value;
};
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = options.method || "GET";
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      credentials: "include",
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError(
      "Не удалось связаться с сервером. Проверьте соединение и повторите попытку.",
      0,
    );
  }
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    let message =
      typeof error?.detail === "string"
        ? error.detail
        : `Не удалось выполнить запрос (${response.status}).`;
    if (response.status === 422 && typeof error?.detail !== "string")
      message =
        "Проверьте данные. Сумма должна быть положительной, а все обязательные поля — заполнены.";
    if (response.status === 401) {
      message =
        path === "/auth/login"
          ? "Неверный логин или пароль."
          : "Сессия завершена. Войдите снова.";
      if (path !== "/auth/login")
        window.dispatchEvent(new Event("invoice-session-expired"));
    }
    if (response.status === 403 && typeof error?.detail !== "string")
      message = "Недостаточно прав или сессия устарела. Обновите страницу.";
    if (response.status === 429)
      message = "Слишком много попыток. Подождите и попробуйте снова.";
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      "Сервер вернул некорректный ответ. Повторите попытку.",
      response.status,
    );
  }
}
