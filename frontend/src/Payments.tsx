import { useState } from "react";
import {
  ArrowDownWideNarrow,
  ArrowUpWideNarrow,
  Check,
  ChevronDown,
  Download,
  LoaderCircle,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import BrandIcon from "./BrandIcon";
import type { Occurrence } from "./api";
import {
  currencies,
  dateLabel,
  downloadCSV,
  Empty,
  money,
  plural,
  Status,
} from "./ui";

export default function Payments({
  items,
  today,
  month,
  loading,
  history,
  isAdmin,
  onAdd,
  onRefresh,
  onSelect,
  onPay,
}: {
  items: Occurrence[];
  today: string;
  month: string;
  loading: boolean;
  history: boolean;
  isAdmin: boolean;
  onAdd: () => void;
  onRefresh: () => void;
  onSelect: (i: Occurrence) => void;
  onPay: (i: Occurrence) => void;
}) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [currency, setCurrency] = useState("all");
  const [sortBy, setSortBy] = useState("due_date");
  const [descending, setDescending] = useState(false);
  const hasFilters =
    Boolean(search.trim()) || status !== "all" || currency !== "all";
  const resetFilters = () => {
    setSearch("");
    setStatus("all");
    setCurrency("all");
  };
  const displayItems = items
    .filter(
      (i) =>
        (status === "all" || i.status === status) &&
        (currency === "all" || i.currency === currency) &&
        i.name
          .toLocaleLowerCase("ru")
          .includes(search.trim().toLocaleLowerCase("ru")),
    )
    .sort((a, b) => {
      // Amounts are ordered within each currency; no exchange rate is implied.
      if (sortBy === "amount" && a.currency !== b.currency)
        return a.currency.localeCompare(b.currency);
      const comparison =
        sortBy === "name"
          ? a.name.localeCompare(b.name, "ru")
          : sortBy === "amount"
            ? Number(a.amount) - Number(b.amount)
            : a.due_date.localeCompare(b.due_date);
      return (
        (descending ? -comparison : comparison) ||
        a.name.localeCompare(b.name, "ru")
      );
    });
  return (
    <section className="payments-card">
      <div className="table-card-heading">
        <div>
          <h2>
            {history ? "Продления за период" : "Продления за месяц"}{" "}
            <span>{items.length}</span>
          </h2>
          <p>
            {history
              ? "Сроки продления и фактические даты оплаты."
              : "Стоимость услуг и отметки об оплате продлений."}
          </p>
        </div>
        <div className="table-header-actions">
          <button
            className="icon-button"
            title="Обновить продления"
            aria-label="Обновить продления"
            onClick={onRefresh}
            disabled={loading}
          >
            <RefreshCw size={17} className={loading ? "spin" : ""} />
          </button>
          <button
            className="button secondary small"
            aria-label="Экспорт CSV"
            title="Экспорт CSV"
            onClick={() => downloadCSV(displayItems, month)}
            disabled={!displayItems.length}
          >
            <Download size={15} />
            <span>Экспорт CSV</span>
          </button>
        </div>
      </div>
      <div className="table-filters">
        <div className="status-tabs" role="group" aria-label="Статус продлений">
          {[
            { id: "all", label: "Все продления" },
            { id: "pending", label: "К оплате" },
            { id: "paid", label: "Оплачено" },
            { id: "overdue", label: "Просрочено" },
          ].map((tab) => (
            <button
              key={tab.id}
              className={status === tab.id ? "selected" : ""}
              aria-pressed={status === tab.id}
              onClick={() => setStatus(tab.id)}
            >
              {tab.label}
              {tab.id === "overdue" &&
                items.filter((i) => i.status === "overdue").length > 0 && (
                  <span>
                    {items.filter((i) => i.status === "overdue").length}
                  </span>
                )}
            </button>
          ))}
        </div>
        <div className="search-filters">
          <label className="search-field">
            <Search size={16} />
            <input
              aria-label="Найти услугу"
              type="search"
              placeholder="Название услуги"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && search) {
                  e.preventDefault();
                  setSearch("");
                }
              }}
            />
            {search && (
              <button aria-label="Очистить поиск" onClick={() => setSearch("")}>
                <X size={13} />
              </button>
            )}
          </label>
          <div className="select-wrap">
            <select
              aria-label="Валюта продлений"
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
            >
              <option value="all">Все валюты</option>
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <ChevronDown size={13} />
          </div>
        </div>
      </div>
      <div className="list-controls">
        <div className="sort-controls">
          <label htmlFor="payment-sort">Сортировка</label>
          <div className="select-wrap">
            <select
              id="payment-sort"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
            >
              <option value="due_date">По сроку продления</option>
              <option value="name">По названию</option>
              <option value="amount">По сумме</option>
            </select>
            <ChevronDown size={14} />
          </div>
          <button
            className="icon-button sort-direction"
            onClick={() => setDescending((value) => !value)}
            aria-label={
              descending
                ? "Сортировать по возрастанию"
                : "Сортировать по убыванию"
            }
            title={descending ? "По убыванию" : "По возрастанию"}
          >
            {descending ? (
              <ArrowUpWideNarrow size={17} />
            ) : (
              <ArrowDownWideNarrow size={17} />
            )}
          </button>
          {sortBy === "amount" && currency === "all" && (
            <span className="sort-hint">Внутри каждой валюты</span>
          )}
        </div>
        <button
          className="reset-filters"
          onClick={resetFilters}
          disabled={!hasFilters}
        >
          <X size={14} /> Сбросить фильтры
        </button>
      </div>
      {loading && !items.length ? (
        <div className="loading-block">
          <LoaderCircle className="spin" /> Загружаем продления…
        </div>
      ) : displayItems.length ? (
        <>
          <div className="table-scroll payment-desktop-table">
            <table className="payments-table">
              <caption className="sr-only">
                Продления за выбранный месяц. Найдено: {displayItems.length}.
              </caption>
              <colgroup>
                <col className="col-name" />
                <col className="col-amount" />
                <col className="col-due" />
                <col className="col-status" />
                <col className="col-paid" />
                <col className="col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Название услуги</th>
                  <th scope="col">Стоимость</th>
                  <th scope="col">Срок продления</th>
                  <th scope="col">Статус</th>
                  <th scope="col">Дата оплаты</th>
                  <th scope="col">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {displayItems.map((item, index) => (
                  <tr key={item.id}>
                    <td>
                      <button
                        className="payment-name"
                        onClick={() => onSelect(item)}
                      >
                        <span
                          className={`payment-icon icon-color-${index % 5}`}
                        >
                          <BrandIcon name="services" size={20} />
                        </span>
                        <span>
                          <strong>{item.name}</strong>
                          <small>
                            {item.recurring_active
                              ? "Ежемесячное продление"
                              : "Учёт услуги прекращён"}
                          </small>
                        </span>
                      </button>
                    </td>
                    <td>
                      <strong className="amount">
                        {money(item.amount, item.currency)}
                      </strong>
                      <small className="currency-code">{item.currency}</small>
                    </td>
                    <td className="due-date-cell">
                      <span
                        className={`date-value ${item.status === "overdue" ? "date-overdue" : ""}`}
                      >
                        {dateLabel(
                          item.due_date,
                          !item.due_date.startsWith(today.slice(0, 4)),
                        )}
                      </span>
                      <small
                        className={
                          item.status === "overdue" ? "late-hint" : "date-hint"
                        }
                      >
                        {item.status === "overdue"
                          ? `${item.overdue_days} ${plural(item.overdue_days, ["день", "дня", "дней"])} просрочки`
                          : item.due_date === today && !item.is_paid
                            ? "Сегодня"
                            : "По графику"}
                      </small>
                    </td>
                    <td>
                      <Status item={item} />
                      {item.is_paid && (item.late_days || 0) > 0 && (
                        <small className="date-hint">
                          С задержкой {item.late_days} дн.
                        </small>
                      )}
                    </td>
                    <td>
                      <span
                        className={`date-value ${item.paid_at ? "" : "muted"}`}
                      >
                        {item.paid_at ? dateLabel(item.paid_at) : "—"}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        {item.is_paid ? (
                          <button
                            className="icon-button"
                            onClick={() => onSelect(item)}
                            aria-label={`Изменить оплату: ${item.name}`}
                            title="Изменить оплату"
                          >
                            <Pencil size={16} />
                          </button>
                        ) : (
                          <button
                            className="pay-button"
                            onClick={() => onPay(item)}
                            title={`Отметить оплату: ${item.name}`}
                          >
                            <Check size={15} />
                            <span>Отметить оплату</span>
                          </button>
                        )}
                        <button
                          className="icon-button"
                          onClick={() => onSelect(item)}
                          aria-label={`Подробности: ${item.name}`}
                        >
                          <MoreHorizontal size={19} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul
            className="payment-mobile-list"
            aria-label="Продления за выбранный месяц"
          >
            {displayItems.map((item) => (
              <li key={item.id}>
                <article className={`payment-mobile-card ${item.status}`}>
                  <div className="mobile-payment-heading">
                    <button
                      className="mobile-payment-name"
                      onClick={() => onSelect(item)}
                    >
                      {item.name}
                    </button>
                    <Status item={item} />
                  </div>
                  <div className="mobile-payment-amount">
                    {money(item.amount, item.currency)}{" "}
                    <span>{item.currency}</span>
                  </div>
                  <dl className="mobile-payment-dates">
                    <div>
                      <dt>Срок продления</dt>
                      <dd
                        className={
                          item.status === "overdue" ? "date-overdue" : ""
                        }
                      >
                        {dateLabel(
                          item.due_date,
                          !item.due_date.startsWith(today.slice(0, 4)),
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Дата оплаты</dt>
                      <dd>
                        {item.paid_at ? dateLabel(item.paid_at) : "Не оплачено"}
                      </dd>
                    </div>
                  </dl>
                  {item.status === "overdue" && (
                    <p className="mobile-payment-note overdue">
                      Просрочка: {item.overdue_days}{" "}
                      {plural(item.overdue_days, ["день", "дня", "дней"])}.
                    </p>
                  )}
                  {item.is_paid && (item.late_days || 0) > 0 && (
                    <p className="mobile-payment-note">
                      Оплачено с задержкой {item.late_days} дн.
                    </p>
                  )}
                  {!item.recurring_active && (
                    <p className="mobile-payment-note">
                      Учёт услуги прекращён.
                    </p>
                  )}
                  <div className="mobile-payment-actions">
                    <button
                      className={`button ${item.is_paid ? "secondary" : "primary"}`}
                      onClick={() =>
                        item.is_paid ? onSelect(item) : onPay(item)
                      }
                    >
                      {item.is_paid ? (
                        <Pencil size={16} />
                      ) : (
                        <Check size={16} />
                      )}{" "}
                      {item.is_paid ? "Изменить оплату" : "Отметить оплату"}
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => onSelect(item)}
                    >
                      Подробнее
                    </button>
                  </div>
                </article>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Empty
          title={
            hasFilters ? "Ничего не найдено" : "В этом месяце продлений нет"
          }
          text={
            hasFilters
              ? "Попробуйте изменить фильтры или поисковый запрос."
              : "Выберите другой месяц или добавьте услугу в учёт."
          }
          action={
            hasFilters ? (
              <button className="button secondary" onClick={resetFilters}>
                <X size={15} /> Показать все продления
              </button>
            ) : (
              isAdmin && (
                <button className="button secondary small" onClick={onAdd}>
                  <Plus size={15} /> Добавить услугу
                </button>
              )
            )
          }
        />
      )}
      <div className="table-footer">
        <span aria-live="polite" aria-atomic="true">
          <span className="footer-status-dot" />{" "}
          {displayItems.length
            ? `Показано ${displayItems.length} из ${items.length} продлений`
            : "Данные за выбранный период"}
        </span>
      </div>
    </section>
  );
}
