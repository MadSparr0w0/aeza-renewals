import { useState } from "react";
import { ChevronDown, LoaderCircle } from "lucide-react";
import type { User } from "./api";
import { Pagination, useDebounced, usePage } from "./DataControls";

export default function OwnerPicker({ initial }: { initial: User }) {
  const [selected, setSelected] = useState(initial);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim());
  const [paging, setPaging] = useState({ key: "", offset: 0 });
  const offset = paging.key === query ? paging.offset : 0;
  const { page, loading, error, reload } = usePage<User>(
    open
      ? `/users/page?${new URLSearchParams({ search: query, limit: "30", offset: String(offset) })}`
      : null,
  );
  return (
    <div className="owner-picker">
      <span id="owner-picker-label">Ответственный</span>
      <input type="hidden" name="owner_id" value={selected.id} />
      <button
        type="button"
        className="button secondary owner-picker__selected"
        aria-labelledby="owner-picker-label"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {selected.full_name || selected.username}
        <ChevronDown size={16} />
      </button>
      {open && (
        <div className="owner-picker__panel">
          <label>
            Найти участника
            <input
              maxLength={100}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Имя или логин"
              autoComplete="off"
            />
          </label>
          {loading && <LoaderCircle size={17} className="spin" />}
          {error && (
            <div className="form-error" role="alert">
              {error}
              <button type="button" className="text-button" onClick={reload}>
                Повторить
              </button>
            </div>
          )}
          <ul className="owner-picker__list">
            {page?.items.map((owner) => (
              <li key={owner.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelected(owner);
                    setOpen(false);
                  }}
                >
                  <strong>{owner.full_name || owner.username}</strong>
                  <small>
                    {owner.username}
                    {owner.id === initial.id ? " · Это вы" : ""}
                  </small>
                </button>
              </li>
            ))}
          </ul>
          {page?.total === 0 && (
            <p className="field-help">Участники не найдены.</p>
          )}
          <Pagination
            page={page}
            offset={offset}
            onOffset={(value) => setPaging({ key: query, offset: value })}
            loading={loading}
          />
        </div>
      )}
    </div>
  );
}
