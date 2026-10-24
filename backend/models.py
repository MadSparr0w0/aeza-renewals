from datetime import date
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import amount_minor, money


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_display_fields(cls, value, info):
        if info.field_name in {"username", "full_name", "name"} and isinstance(value, str):
            return value.strip()
        return value


class Login(StrictModel):
    model_config = ConfigDict(title="Данные для входа")
    username: str = Field(min_length=1, max_length=64, title="Логин", description="Логин администратора из окружения или обычного пользователя.")
    password: str = Field(min_length=1, max_length=256, title="Пароль", description="Пароль учётной записи. Не сохраняется в документации и не возвращается в ответе.")


class RecurringCreate(StrictModel):
    model_config = ConfigDict(title="Новый регулярный платёж")
    name: str = Field(min_length=1, max_length=160, title="Название платежа", description="Понятное команде название расхода, например аренда офиса.")
    amount: str | int | float = Field(title="Сумма", description="От 0.01 до 999999999.99, не более двух знаков после запятой. Для точного ввода рекомендуется строка, например 1250.50.")
    currency: Literal["RUB", "USD", "EUR"] = Field(title="Валюта", description="RUB — рубли, USD — доллары, EUR — евро. Валюты учитываются отдельно.")
    next_due_date: date = Field(title="Ближайший срок оплаты", description="Календарная дата в формате YYYY-MM-DD в общем часовом поясе приложения; от 2000 года до пяти лет вперёд.")
    frequency: Literal["monthly"] = Field(default="monthly", title="Периодичность", description="monthly — ежемесячно. Исходное число месяца сохраняется; в коротком месяце выбирается последний день.")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        return money(amount_minor(value))

    @field_validator("next_due_date", mode="before")
    @classmethod
    def date_only(cls, value):
        if not isinstance(value, (str, date)) or isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Дата должна иметь формат YYYY-MM-DD.")
        return value


class RecurringUpdate(StrictModel):
    model_config = ConfigDict(title="Новые условия регулярного платежа")
    name: str = Field(min_length=1, max_length=160, title="Название платежа", description="Новое название серии и её неоплаченных периодов начиная со следующего месяца. История сохраняется.")
    amount: str | int | float = Field(title="Сумма", description="Новая сумма от 0.01 до 999999999.99, не более двух знаков после запятой. Для точного ввода рекомендуется строка. Применяется с первого числа следующего месяца.")
    currency: Literal["RUB", "USD", "EUR"] = Field(title="Валюта", description="Новая валюта RUB, USD или EUR. Текущий месяц, прошлые и уже оплаченные периоды сохраняют прежние условия.")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        return money(amount_minor(value))


class OccurrencePatch(StrictModel):
    model_config = ConfigDict(title="Изменение оплаты периода")
    is_paid: bool | None = Field(default=None, title="Отметка об оплате", description="true — оплачено, false — не оплачено. Отмена оплаты очищает фактическую дату только этого периода.")
    paid_at: date | None = Field(default=None, title="Фактическая дата оплаты", description="Дата в формате YYYY-MM-DD, не позднее сегодняшней. Если при подтверждении дата не указана, используется сегодня. Исправление даты пересчитывает просрочку.")

    @field_validator("paid_at", mode="before")
    @classmethod
    def date_only(cls, value):
        if value is not None and (not isinstance(value, (str, date)) or isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Дата должна иметь формат YYYY-MM-DD.")
        return value


class UserCreate(StrictModel):
    model_config = ConfigDict(title="Новый пользователь")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$", title="Логин", description="Уникальный логин: латинские буквы, цифры и символы _, -, ., @.")
    full_name: str = Field(min_length=1, max_length=100, title="Имя пользователя", description="Имя, отображаемое в панели и журнале действий.")
    password: str = Field(min_length=12, max_length=256, title="Пароль", description="От 12 до 256 символов. Сохраняется только защищённый хеш; пароль в ответ не попадает.")


class UserPatch(StrictModel):
    model_config = ConfigDict(title="Изменение пользователя")
    username: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$", title="Новый логин", description="Латинские буквы, цифры и символы _, -, ., @. Если поле не передано, логин сохраняется.")
    full_name: str | None = Field(default=None, min_length=1, max_length=100, title="Новое имя пользователя", description="Отображаемое имя. Если поле не передано, имя сохраняется.")
    password: str | None = Field(default=None, min_length=12, max_length=256, title="Новый пароль", description="От 12 до 256 символов. Изменение пользователя завершает все его активные сессии.")
