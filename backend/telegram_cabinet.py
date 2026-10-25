"""Private, identity-bound Telegram reads and explicit group selection.

No financial writes live here. Every financial callback resolves the current
Telegram identity again; group messages never call this module's cabinet.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
import re
import time

from fastapi import HTTPException

from . import queries
from .db import transaction
from .domain import materialize, month_end
from .telegram_identity import identity_user
from .telegram_connections import (GROUP_TYPES, MAX_DESTINATIONS, active_connections,
    claim_chat_selection, disable_destination, enable_chat_selection, group_id,
    record_chat_selection, recover_expired_deliveries, request_chat_selection,
    require_private_admin, selection_row, token_scope)

PAGE_SIZE = 6
MAX_PAGE = 999
MAX_TEXT = 3800
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
VIEWS = frozenset({"menu", "payments", "history", "summary", "help"})


@dataclass(frozen=True)
class CabinetReply:
    text: str
    markup: dict
    edit: bool = True


def safe(value, maximum=42):
    value = " ".join(str(value or "").split())
    return escape(value if len(value) <= maximum else value[:maximum-1] + "…", quote=True)


def amount(value):
    whole, cents = value.split(".")
    return f"{int(whole):,}".replace(",", " ") + "," + cents


def month_value(key, today):
    if not isinstance(key, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", key):
        raise HTTPException(422, "Некорректный месяц.")
    first = date.fromisoformat(key + "-01")
    if not 2000 <= first.year <= today.year + 5:
        raise HTTPException(422, "Месяц вне доступного диапазона.")
    return first


def callback(view, key, page=0):
    return f"cab:{view}:{key}:{page}"


def parse_callback(data, today):
    if not isinstance(data, str) or len(data.encode()) > 64:
        return None
    match = re.fullmatch(r"cab:(menu|payments|history|summary|help):(\d{4}-\d{2}|all):(\d{1,3})", data)
    if not match:
        return None
    view, key, page = match.groups()
    if key == "all" and view != "history":
        return None
    if key != "all":
        try:
            month_value(key, today)
        except (HTTPException, ValueError):
            return None
    return view, key, int(page)


def navigation(cfg, user, view, key, page=0, total=0, panel_url=None):
    today_key = cfg.today.strftime("%Y-%m")
    button = lambda text, target, month=today_key, number=0: {"text": text, "callback_data": callback(target, month, number)}
    rows = [[button("🧾 Платежи", "payments"), button("🗂 История", "history", "all")],
            [button("📊 Сводка", "summary"), button("ℹ️ Помощь", "help")]]
    if view in {"payments", "summary"} or view == "history" and key != "all":
        first = month_value(key, cfg.today)
        previous, following = first - timedelta(days=1), month_end(first) + timedelta(days=1)
        months = []
        if previous.year >= 2000:
            months.append(button("‹ " + MONTHS[previous.month-1], view, previous.strftime("%Y-%m")))
        if following.year <= cfg.today.year + 5:
            months.append(button(MONTHS[following.month-1] + " ›", view, following.strftime("%Y-%m")))
        if months:
            rows.append(months)
    if view == "history":
        rows.append([button("За текущий месяц" if key == "all" else "Вся история", "history", today_key if key == "all" else "all")])
    pages = max(1, min(MAX_PAGE+1, (total+PAGE_SIZE-1)//PAGE_SIZE))
    if pages > 1:
        movement = []
        if page:
            movement.append(button("‹ Назад", view, key, page-1))
        if page+1 < pages:
            movement.append(button("Далее ›", view, key, page+1))
        if movement:
            rows.append(movement)
    if user["role"] == "admin":
        rows.append([{"text": "👥 Группы уведомлений", "callback_data": "chat:list"}])
    if panel_url:
        rows.append([{"text": "🌐 Веб-панель", "url": panel_url}])
    rows.append([button("🔄 Обновить", view, key, page), button("🏠 Меню", "menu")])
    return {"inline_keyboard": rows}


def build_view(db, cfg, telegram_id, view="menu", key=None, page=0, panel_url=None):
    if view not in VIEWS or isinstance(page, bool) or not isinstance(page, int) or not 0 <= page <= MAX_PAGE:
        raise HTTPException(422, "Некорректный раздел или страница.")
    key = key or ("all" if view == "history" else cfg.today.strftime("%Y-%m"))
    if key != "all":
        first = month_value(key, cfg.today)
    elif view != "history":
        raise HTTPException(422, "Некорректный месяц.")
    with transaction(db):
        user = identity_user(db, telegram_id)
        if user is None:
            raise HTTPException(403, "Telegram больше не связан с кабинетом. Откройте /login.")
        who = "Все платежи команды" if user["role"] == "admin" else "Только ваши платежи"
        if view == "menu":
            text = "📋 <b>Aéza · Регулярные платежи</b>\n\n" + safe(user["full_name"],70) + "\n" + who + ".\n\nСоздание, изменение и отметка оплаты доступны в веб-панели. Автоуведомления отправляются только в выбранные корпоративные группы."
        elif view == "help":
            text = "ℹ️ <b>Личный кабинет</b>\n\n/payments — платежи по месяцам\n/history — отмеченные оплаты\n/summary — итоги по валютам\n/menu — меню\n/login — привязка сайта\n/chats — группы уведомлений администратора\n\n" + who + ". RUB, USD и EUR считаются отдельно. Даты — " + safe(cfg.timezone,50) + ". В день срока просрочки нет.\n\nБот не создаёт платежи и не отмечает оплату. Эти действия выполняются на сайте. Привязку Telegram можно отключить в настройках сайта."
        else:
            owner_id = None if user["role"] == "admin" else user["id"]
            materialize(db,cfg.today,month_end(first) if key != "all" else None,owner_id=owner_id,team_id=user['team_id'])
            if view == "summary":
                result = queries.summary(db,cfg,user,key)
                lines = ["📊 <b>Сводка · " + MONTHS[first.month-1] + " " + str(first.year) + "</b>", who]
                for currency, values in result["currencies"].items():
                    lines.extend(["", "<b>"+currency+"</b>", "План: "+amount(values["planned"]),
                        "Оплачено: "+amount(values["paid"]), "К оплате: "+amount(values["unpaid"]), "Просрочено: "+amount(values["overdue"])])
                lines.extend(["", "Периодов: "+str(result["total_count"]), "Валюты не конвертируются. Отметки оплаты ручные."])
                text = "\n".join(lines)
            else:
                filters = {"month": None if key == "all" else key, "status": "paid" if view == "history" else None}
                result = queries.page(db,cfg,user,limit=PAGE_SIZE,offset=page*PAGE_SIZE,descending=view=="history",**filters)
                pages = max(1,min(MAX_PAGE+1,(result["total"]+PAGE_SIZE-1)//PAGE_SIZE))
                if page >= pages:
                    page = pages-1
                    result = queries.page(db,cfg,user,limit=PAGE_SIZE,offset=page*PAGE_SIZE,descending=view=="history",**filters)
                period = "все месяцы" if key == "all" else MONTHS[first.month-1]+" "+str(first.year)
                lines = [("🗂 <b>История оплат · " if view=="history" else "🧾 <b>Платежи · ")+period+"</b>",who,""]
                for row in result["items"]:
                    lines.append("<b>"+safe(row["name"])+"</b>"+(" · архив" if not row["recurring_active"] else ""))
                    if user["role"] == "admin":
                        lines.append("👤 "+safe(row["owner_name"],26))
                    lines.append(amount(row["amount"])+" "+row["currency"]+" · "+date.fromisoformat(row["due_date"]).strftime("%d.%m.%Y"))
                    if row["is_paid"]:
                        lines.append("Оплата: "+date.fromisoformat(row["paid_at"]).strftime("%d.%m.%Y"))
                    else:
                        lines.append("Просрочка: "+str(row["overdue_days"])+" дн." if row["status"]=="overdue" else "Оплата не отмечена")
                    lines.append("")
                lines.append("Записей нет." if not result["items"] else f"Страница {page+1} из {pages} · записей: {result['total']}")
                text = "\n".join(lines)
                return CabinetReply(text,navigation(cfg,user,view,key,page,result["total"],panel_url)) if len(text)<=MAX_TEXT else CabinetReply("Слишком длинный список. Откройте веб-панель.",navigation(cfg,user,"menu",cfg.today.strftime("%Y-%m"),panel_url=panel_url))
        return CabinetReply(text,navigation(cfg,user,view,key,panel_url=panel_url))


def verify_group(client, cid, sender_id, bot_id):
    if group_id(cid) is None or isinstance(bot_id,bool) or not isinstance(bot_id,int) or bot_id<=0:
        raise HTTPException(422,"Выберите доступную группу.")
    details = client.call("getChat",{"chat_id":cid})
    if not isinstance(details,dict) or group_id(details.get("id"))!=cid or details.get("type") not in GROUP_TYPES:
        raise HTTPException(422,"Telegram не подтвердил группу. Добавьте бота и выберите её снова.")
    administrators = client.call("getChatAdministrators",{"chat_id":cid})
    if not isinstance(administrators,list) or not any(isinstance(item,dict) and item.get("status") in {"creator","administrator"} and isinstance(item.get("user"),dict) and item["user"].get("id")==sender_id for item in administrators):
        raise HTTPException(403,"Выбирать группу может только её администратор.")
    membership = client.call("getChatMember",{"chat_id":cid,"user_id":bot_id})
    status = membership.get("status") if isinstance(membership,dict) else None
    permitted = status in {"creator","administrator"} or status=="restricted" and membership.get("is_member") is True and membership.get("can_send_messages") is True
    if status == "member":
        permissions = details.get("permissions")
        permitted = isinstance(permissions,dict) and permissions.get("can_send_messages") is True
    if not permitted:
        raise HTTPException(403,"Разрешите боту отправлять сообщения в этой группе.")
    return details


def chat_menu(db,cfg,user,telegram_id):
    require_private_admin(db,user["id"],telegram_id)
    destinations = db.execute("SELECT * FROM telegram_destinations WHERE bot_scope=? AND team_id=? ORDER BY active DESC,connected_at DESC LIMIT 20",(token_scope(cfg.telegram_bot_token),user['team_id'])).fetchall()
    lines = ["👥 <b>Группы уведомлений</b>","Включено: "+str(len(active_connections(db,cfg,user['team_id'])))+" из "+str(MAX_DESTINATIONS),"", "В выбранные группы поступают уведомления обо всех платежах команды: владелец, название, сумма, валюта и срок."]
    rows = []
    for row in destinations:
        lines.extend(["",("✅ " if row["active"] else "⏸ ")+safe(row["chat_title"],22)])
        rows.append([{"text":("Выключить · " if row["active"] else "Включить · ")+" ".join(row["chat_title"].split())[:30],"callback_data":("chat:off:" if row["active"] else "chat:review:")+row["chat_id"]}])
    rows.extend([[{"text":"➕ Выбрать группу","callback_data":"chat:select"}],
                 [{"text":"🔄 Обновить","callback_data":"chat:list"},{"text":"🏠 Меню","callback_data":callback("menu",cfg.today.strftime("%Y-%m"))}]])
    return CabinetReply("\n".join(lines),{"inline_keyboard":rows})


def selector(db,cfg,user,telegram_id,now):
    request_id = request_chat_selection(db,cfg,user["id"],telegram_id,now)
    rights = {key:False for key in ("is_anonymous","can_delete_messages","can_manage_video_chats","can_restrict_members","can_promote_members","can_change_info","can_invite_users","can_post_stories","can_edit_stories","can_delete_stories")}
    rights["can_manage_chat"] = True
    markup = {"keyboard":[[{"text":"👥 Выбрать корпоративную группу","request_chat":{"request_id":request_id,"chat_is_channel":False,"bot_is_member":True,"user_administrator_rights":rights,"request_title":True}}],[{"text":"🏠 Кабинет"}]],"resize_keyboard":True,"one_time_keyboard":True,"input_field_placeholder":"Выберите группу кнопкой"}
    return CabinetReply("Нажмите кнопку выбора группы. Бот должен быть её участником, а вы — администратором. Выбор действует 10 минут. После проверки будет отдельное подтверждение публикации уведомлений.",markup,edit=False)


def candidate_reply(row):
    return CabinetReply("👥 <b>Подтвердите группу</b>\n\n"+safe(row["chat_title"],100)+"\n\nВ неё будут отправляться уведомления <b>обо всех платежах команды</b>: имя владельца, название, сумма, валюта и срок. Участники группы смогут видеть и пересылать эти сообщения. Включить уведомления?",{"inline_keyboard":[[{"text":"✅ Включить уведомления","callback_data":"chat:on:"+str(row["request_id"])}],[{"text":"Отмена","callback_data":"chat:list"}]]})


def handle_chat_action(db,cfg,user,sender,data,client,bot_id,now,shared=None):
    telegram_id = sender["id"]
    require_private_admin(db,user["id"],telegram_id)
    if shared is not None:
        identifier = shared.get("request_id")
        cid = claim_chat_selection(db,cfg,user["id"],telegram_id,identifier,shared.get("chat_id"),now)
        details = verify_group(client,cid,telegram_id,bot_id)
        record_chat_selection(db,cfg,user["id"],telegram_id,identifier,details,now)
        return candidate_reply(selection_row(db,cfg,user["id"],telegram_id,identifier,'candidate',now))
    if data == "chat:list":
        return chat_menu(db,cfg,user,telegram_id)
    if data == "chat:select":
        return selector(db,cfg,user,telegram_id,now)
    match = re.fullmatch(r"chat:(off|review):(-[1-9]\d{0,18})",data)
    if match:
        action,cid = match.groups()
        destination = db.execute("SELECT * FROM telegram_destinations WHERE chat_id=? AND bot_scope=? AND team_id=?",(cid,token_scope(cfg.telegram_bot_token),user['team_id'])).fetchone()
        if destination is None:
            raise HTTPException(404,"Группа не найдена. Обновите список.")
        if action == "off":
            with transaction(db):
                require_private_admin(db,user["id"],telegram_id)
                recover_expired_deliveries(db,now)
                if db.execute("SELECT 1 FROM reminder_outbox WHERE recipient_chat_id=? AND state='sending'",(cid,)).fetchone():
                    raise HTTPException(409,"Дождитесь завершения отправки в эту группу.")
                disable_destination(db,cid,user)
            return chat_menu(db,cfg,user,telegram_id)
        identifier = request_chat_selection(db,cfg,user["id"],telegram_id,now)
        claim_chat_selection(db,cfg,user["id"],telegram_id,identifier,cid,now)
        details = verify_group(client,cid,telegram_id,bot_id)
        record_chat_selection(db,cfg,user["id"],telegram_id,identifier,details,now)
        return candidate_reply(selection_row(db,cfg,user["id"],telegram_id,identifier,'candidate',now))
    match = re.fullmatch(r"chat:on:([1-9]\d{0,9})",data)
    if match:
        identifier = int(match[1])
        row = selection_row(db,cfg,user["id"],telegram_id,identifier,'candidate',now)
        verify_group(client,row["chat_id"],telegram_id,bot_id)
        enable_chat_selection(db,cfg,user["id"],telegram_id,identifier,now)
        return chat_menu(db,cfg,user,telegram_id)
    raise HTTPException(404,"Кнопка устарела. Откройте /chats заново.")
