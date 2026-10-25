import { useState } from "react";
import {
  LoaderCircle,
  LockKeyhole,
  Pencil,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import type { Invitation, Session, User } from "./api";
import { invitationDate } from "./AccountForms";
import { initials } from "./ui";
import { Pagination, useDebounced, usePage } from "./DataControls";
import AdminList from "./AdminList";

export default function Team({
  session,
  onEdit,
  onDelete,
  invitations,
  onRevokeInvitation,
  revision,
}: {
  session: Session;
  onEdit: (user: User) => void;
  onDelete: (user: User) => void;
  invitations: Invitation[];
  onRevokeInvitation: (invitation: Invitation) => void;
  revision: number;
}) {
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim());
  const [paging, setPaging] = useState({ key: "", offset: 0 });
  const offset = paging.key === query ? paging.offset : 0;
  const { page, loading, error, reload } = usePage<User>(
    `/users/page?${new URLSearchParams({ search: query, limit: "30", offset: String(offset) })}`,
    revision,
  );
  return (
    <section className="team-card">
      <div className="team-search">
        <label>
          Найти участника
          <input
            type="search"
            maxLength={100}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Имя или логин"
          />
        </label>
      </div>
      {loading ? (
        <div className="loading-block">
          <LoaderCircle size={20} className="spin" />
          Загрузка участников…
        </div>
      ) : error ? (
        <div className="data-list-error" role="alert">
          {error}
          <button className="text-button" onClick={reload}>
            Повторить
          </button>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="team-table">
            <thead>
              <tr>
                <th>Участник</th>
                <th>Логин</th>
                <th>Роль</th>
                <th>Доступ</th>
                <th>
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {page?.items.map((user) => (
                <tr key={user.id}>
                  <td>
                    <div className="team-person">
                      <span className="avatar">
                        {initials(user.full_name || user.username)}
                      </span>
                      <span>
                        <strong>{user.full_name || user.username}</strong>
                        {user.id === session.user.id && <small>Это вы</small>}
                      </span>
                    </div>
                  </td>
                  <td>{user.username}</td>
                  <td>
                    <span className="role-badge">
                      {user.role === "admin" ? (
                        <>
                          <ShieldCheck size={13} />
                          Администратор
                        </>
                      ) : (
                        "Пользователь"
                      )}
                    </span>
                  </td>
                  <td>
                    <span className="access-label">
                      {user.role === "admin"
                        ? "Платежи и участники своей команды"
                        : "Свои платежи"}
                    </span>
                  </td>
                  <td>
                    {user.role === "admin" ? (
                      <LockKeyhole size={15} className="muted" />
                    ) : (
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
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {page?.total === 0 && (
            <p className="data-list-error">Участники не найдены.</p>
          )}
        </div>
      )}
      <Pagination
        page={page}
        offset={offset}
        onOffset={(value) => setPaging({ key: query, offset: value })}
        loading={loading}
      />
      {session.user.env_managed && (
        <AdminList revision={revision} currentUserId={session.user.id} />
      )}
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
