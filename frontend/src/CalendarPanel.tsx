import { useEffect, useState } from "react";
import { ChevronRight, LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "./api";
import type { Currency, Occurrence } from "./api";
import {
  dateLabel,
  Empty,
  Modal,
  money,
  monthLabel,
  plural,
  Status,
} from "./ui";
import { Pagination, usePage } from "./DataControls";

type CalendarDay = {
  date: string;
  count: number;
  paid_count: number;
  currencies: Partial<Record<Currency, string>>;
};
function DayPayments({
  date,
  month,
  isAdmin,
  onSelect,
  onClose,
}: {
  date: string;
  month: string;
  isAdmin: boolean;
  onSelect: (item: Occurrence) => void;
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const { page, loading, error, reload } = usePage<Occurrence>(
    `/payment-occurrences/page?${new URLSearchParams({ month, day: date, status: "all", currency: "all", limit: "30", offset: String(offset), sort: "name", descending: "false" })}`,
  );
  return (
    <Modal title={dateLabel(date, true)} onClose={onClose}>
      {loading ? (
        <div className="loading-block">
          <LoaderCircle size={20} className="spin" />
          Загрузка платежей…
        </div>
      ) : error ? (
        <div className="form-error" role="alert">
          {error}
          <button className="text-button" onClick={reload}>
            Повторить
          </button>
        </div>
      ) : (
        <ul className="calendar-day-list">
          {page?.items.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => {
                  onClose();
                  onSelect(item);
                }}
              >
                <span>
                  <strong>{item.name}</strong>
                  {isAdmin && <small>{item.owner_name}</small>}
                  <Status item={item} />
                </span>
                <strong>{money(item.amount, item.currency)}</strong>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Pagination
        page={page}
        offset={offset}
        onOffset={setOffset}
        loading={loading}
      />
    </Modal>
  );
}
export default function Calendar({
  month,
  today,
  revision,
  isAdmin,
  onSelect,
}: {
  month: string;
  today: string;
  revision: number;
  isAdmin: boolean;
  onSelect: (item: Occurrence) => void;
}) {
  const [days, setDays] = useState<CalendarDay[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0),
    [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setDays([]);
    setSelected(null);
    api<CalendarDay[]>(`/payments/calendar?month=${month}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setDays(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError((reason as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [month, revision, retry]);
  const first = new Date(`${month}-01T12:00:00`),
    offset = (first.getDay() + 6) % 7,
    numberOfDays = new Date(
      first.getFullYear(),
      first.getMonth() + 1,
      0,
    ).getDate(),
    cells = Math.ceil((offset + numberOfDays) / 7) * 7;
  const totals = (day: CalendarDay) =>
    Object.entries(day.currencies)
      .map(([currency, amount]) => money(amount!, currency as Currency))
      .join(" · ");
  return (
    <section className="calendar-card">
      <div className="simple-card-heading">
        <h2>{monthLabel(month)}</h2>
        <button
          className="icon-button"
          aria-label="Обновить календарь"
          onClick={() => setRetry((value) => value + 1)}
          disabled={loading}
        >
          <RefreshCw size={17} className={loading ? "spin" : ""} />
        </button>
      </div>
      {loading ? (
        <div className="loading-block">
          <LoaderCircle size={20} className="spin" />
          Загрузка календаря…
        </div>
      ) : error ? (
        <div className="data-list-error" role="alert">
          {error}
        </div>
      ) : (
        <>
          <div className="calendar-desktop">
            <div className="calendar-weekdays">
              {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day) => (
                <span key={day}>{day}</span>
              ))}
            </div>
            <div className="calendar-grid">
              {Array.from({ length: cells }, (_, index) => {
                const day = index - offset + 1,
                  date = `${month}-${String(day).padStart(2, "0")}`,
                  valid = day > 0 && day <= numberOfDays,
                  summary = days.find((value) => value.date === date);
                return (
                  <div
                    key={index}
                    className={`calendar-day ${valid ? "" : "outside"} ${date === today ? "is-today" : ""}`}
                  >
                    <span className="calendar-number">{valid ? day : ""}</span>
                    {summary && (
                      <button
                        className="calendar-day-summary"
                        onClick={() => setSelected(date)}
                        aria-label={`${dateLabel(date)}: ${summary.count} платежей`}
                      >
                        <strong>
                          {summary.count}{" "}
                          {plural(summary.count, [
                            "платёж",
                            "платежа",
                            "платежей",
                          ])}
                        </strong>
                        <span>{totals(summary)}</span>
                        <small>
                          Оплачено {summary.paid_count} из {summary.count}
                        </small>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="calendar-agenda">
            {days.length ? (
              days.map((day) => (
                <section className="agenda-day" key={day.date}>
                  <header className="agenda-date">
                    <time className="agenda-day-number" dateTime={day.date}>
                      {Number(day.date.slice(8))}
                    </time>
                    <div>
                      <h3>{dateLabel(day.date)}</h3>
                      <span>
                        {new Intl.DateTimeFormat("ru-RU", {
                          weekday: "long",
                        }).format(new Date(`${day.date}T12:00:00`))}
                      </span>
                    </div>
                  </header>
                  <button
                    className="agenda-payment"
                    onClick={() => setSelected(day.date)}
                  >
                    <span className="agenda-payment-info">
                      <strong>
                        {day.count}{" "}
                        {plural(day.count, ["платёж", "платежа", "платежей"])}
                      </strong>
                      <small>
                        Оплачено {day.paid_count} из {day.count}
                      </small>
                    </span>
                    <span className="agenda-payment-totals">
                      <strong>{totals(day)}</strong>
                      <ChevronRight size={17} />
                    </span>
                  </button>
                </section>
              ))
            ) : (
              <Empty
                title="В этом месяце платежей нет"
                text="Выберите другой месяц."
              />
            )}
          </div>
        </>
      )}
      {selected && (
        <DayPayments
          key={selected}
          date={selected}
          month={month}
          isAdmin={isAdmin}
          onSelect={onSelect}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  );
}
