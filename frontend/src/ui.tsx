import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import aezaMark from "./assets/aeza/mark.svg";
import BrandIcon from "./BrandIcon";
import type { Currency, Occurrence } from "./api";

export const currencies: Currency[] = ["RUB", "USD", "EUR"];
export const symbols: Record<Currency, string> = {
  RUB: "₽",
  USD: "$",
  EUR: "€",
};
export const dateLabel = (date: string, year = false) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    ...(year ? { year: "numeric" } : {}),
  }).format(new Date(`${date}T12:00:00`));
export const money = (amount: string | number, currency: Currency) =>
  `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: Number.isInteger(Number(amount)) ? 0 : 2, maximumFractionDigits: 2 }).format(Number(amount))} ${symbols[currency]}`;
export const monthLabel = (month: string) =>
  new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" })
    .format(new Date(`${month}-15T12:00:00`))
    .replace(" г.", "");
export const moveMonth = (month: string, change: number) => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + change, 15);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
export const initials = (name: string) =>
  name
    .trim()
    .split(/\s+/)
    .map((v) => v[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
export const plural = (count: number, words: [string, string, string]) =>
  words[
    count % 10 === 1 && count % 100 !== 11
      ? 0
      : count % 10 >= 2 &&
          count % 10 <= 4 &&
          !(count % 100 >= 12 && count % 100 <= 14)
        ? 1
        : 2
  ];
export function Logo() {
  return (
    <div className="brand" role="img" aria-label="Aéza">
      <img className="brand-mark" src={aezaMark} alt="" aria-hidden="true" />
      <span className="brand-copy">
        <strong>Aéza</strong>
      </span>
    </div>
  );
}
export function Modal({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    dialog
      ?.querySelector<HTMLElement>(
        "input:not([type='hidden']), select, textarea",
      )
      ?.focus();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby={titleId}
    >
      <div className="modal-inner">
        <button
          type="button"
          className="icon-button modal-close"
          onClick={onClose}
          aria-label="Закрыть"
        >
          <X size={20} />
        </button>
        <h2 id={titleId}>{title}</h2>
        {subtitle && <p className="modal-subtitle">{subtitle}</p>}
        {children}
      </div>
    </dialog>
  );
}
export function Empty({
  title = "Платежей пока нет",
  text = "Добавьте первый регулярный платёж.",
  action,
}: {
  title?: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span>
        <BrandIcon name="payments" size={28} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
export function Status({ item }: { item: Occurrence }) {
  return (
    <span className={`status ${item.status}`}>
      <i />
      {item.status === "paid"
        ? "Оплачено"
        : item.status === "overdue"
          ? "Просрочено"
          : "К оплате"}
    </span>
  );
}
export function Metric({
  label,
  value,
  caption,
  icon,
  variant,
  warning = false,
}: {
  label: string;
  value: string;
  caption: string;
  icon: ReactNode;
  variant: string;
  warning?: boolean;
}) {
  return (
    <article
      className={`metric metric-${variant} ${warning ? "has-warning" : ""}`}
    >
      <div className="metric-label">
        <span>{label}</span>
        <span className="metric-icon">{icon}</span>
      </div>
      <strong>{value}</strong>
      <p>
        <i />
        {caption}
      </p>
    </article>
  );
}
export function downloadCSV(items: Occurrence[], month: string) {
  const headers = [
    "Название платежа",
    "Сумма",
    "Валюта",
    "Срок оплаты",
    "Статус",
    "Фактическая дата",
    "Просрочка (дни)",
  ];
  const escape = (value: unknown) => {
    let text = String(value ?? "");
    if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  };
  const rows = items.map((i) => [
    i.name,
    i.amount,
    i.currency,
    i.due_date,
    i.is_paid
      ? "Оплачено"
      : i.status === "overdue"
        ? "Просрочено"
        : "Не оплачено",
    i.paid_at,
    i.overdue_days,
  ]);
  const blob = new Blob(
    [
      "\uFEFF" +
        [headers, ...rows].map((row) => row.map(escape).join(";")).join("\r\n"),
    ],
    { type: "text/csv;charset=utf-8;" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `regular-payments-${month}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
