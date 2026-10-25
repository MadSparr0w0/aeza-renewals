import { useId, useState } from "react";
import { LoaderCircle } from "lucide-react";
import type { User } from "./api";
import { Pagination, useDebounced, usePage } from "./DataControls";

export default function AdminList({
  revision,
  currentUserId,
}: {
  revision: number;
  currentUserId: number;
}) {
  const id = useId();
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim(), 300);
  const [paging, setPaging] = useState({ key: "", offset: 0 });
  const offset = paging.key === query ? paging.offset : 0;
  const waiting = query !== search.trim();
  const result = usePage<User>(
    waiting
      ? null
      : "/admins/page?" +
          new URLSearchParams({
            search: query,
            limit: "30",
            offset: String(offset),
          }),
    revision,
  );
  const loading = result.loading || waiting;

  return (
    <section
      className="invitations-section"
      aria-labelledby={id + "-heading"}
      aria-busy={loading}
    >
      <h2 id={id + "-heading"}>
        Отдельные администраторы
        {result.page && <span>{result.page.total}</span>}
      </h2>
      <div className="team-search">
        <label htmlFor={id + "-search"}>
          Найти администратора
          <input
            id={id + "-search"}
            type="search"
            value={search}
            maxLength={100}
            placeholder="Имя или логин"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      {loading ? (
        <div className="loading-block" role="status">
          <LoaderCircle size={20} className="spin" />
          Загрузка администраторов…
        </div>
      ) : result.error ? (
        <div className="data-list-error" role="alert">
          {result.error}
          <button type="button" className="text-button" onClick={result.reload}>
            Повторить
          </button>
        </div>
      ) : result.page?.items.length ? (
        <div className="table-scroll">
          <table className="invitations-table">
            <caption className="sr-only">Администраторы и их команды</caption>
            <thead>
              <tr>
                <th scope="col">Имя</th>
                <th scope="col">Логин</th>
                <th scope="col">Команда</th>
              </tr>
            </thead>
            <tbody>
              {result.page.items.map((user) => (
                <tr key={user.id}>
                  <td>
                    <strong>{user.full_name || user.username}</strong>
                  </td>
                  <td>{user.username}</td>
                  <td>
                    <span className="role-badge">
                      {user.id === currentUserId ? "Своя" : "Отдельная"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>Администраторы не найдены.</p>
      )}
      {(result.page || loading) && (
        <Pagination
          page={result.page}
          offset={offset}
          onOffset={(value) => setPaging({ key: query, offset: value })}
          loading={loading}
        />
      )}
    </section>
  );
}
