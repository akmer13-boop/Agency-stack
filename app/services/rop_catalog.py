from __future__ import annotations

CATEGORY_NAMES: dict[str, str] = {
    "7": "Продажи B2C",
    "2": "Консьерж-сервис",
    "8": "Продажи B2B",
    "12": "Реактивация",
    "0": "Черновик",
    "11": "Новая воронка",
    "5": "Командировки",
}

STAGE_NAMES: dict[str, str] = {
    # Продажи B2C
    "C7:NEW": "Новая",
    "C7:PREPARATION": "Выявление потребностей",
    "C7:PREPAYMENT_INVOICE": "Подбор пакетного тура",
    "C7:UC_IAVLST": "Запрос отправлен партнеру",
    "C7:EXECUTING": "КП отправлено",
    "C7:UC_BNE980": "Счет отправлен клиенту",
    "C7:UC_9XH4U7": "Предоплата получена",
    "C7:UC_ZZ9VZ0": "Бронирование",
    "C7:UC_TJNUX1": "Бронирование подтверждено",
    "C7:UC_CMGBK2": "Скоро вылет",
    "C7:UC_VS9U11": "Оказание услуги",
    "C7:UC_I5HWA8": "На проверку",
    "C7:FINAL_INVOICE": "Потенциальный клиент",
    "C7:WON": "Сделка состоялась",
    "C7:LOSE": "Не могли предоставить сервис",
    "C7:APOLOGY": "Долгий ответ клиенту",
    "C7:UC_O89RHD": "Высокая цена",
    "C7:UC_YWA3CM": "Другое",
    # Консьерж-сервис
    "C2:NEW": "Новая",
    "C2:PREPARATION": "Взято в работу",
    "C2:PREPAYMENT_INVOICE": "Поиск партнера",
    "C2:EXECUTING": "Предложение клиенту",
    "C2:FINAL_INVOICE": "Оплата от клиента",
    "C2:UC_KD4NYJ": "Подтверждение партнеру",
    "C2:UC_0FVJG1": "Контроль рисков",
    "C2:UC_3OFXC1": "Фактический контроль",
    "C2:UC_TFZRYD": "Потенциальный клиент",
    "C2:1": "На проверку",
    "C2:WON": "Сделка успешна",
    "C2:LOSE": "Не прошли по цене",
    "C2:APOLOGY": "Услугу невозможно осуществить",
    "C2:UC_4UGCIB": "Клиент пропал, нет ответа",
    "C2:UC_4MIH4L": "Клиент обратился к конкуренту / сделал сам",
    "C2:UC_RTH475": "Неверный номер / почта",
    "C2:UC_KNNRDP": "Услуга больше неактуальна",
    "C2:2": "Криптовалюта",
    "C2:3": "Недостаточный бюджет клиента",
    "C2:4": "Изменились планы",
    # Продажи B2B
    "C8:NEW": "Новая",
    "C8:PREPARATION": "Взято в работу",
    "C8:PREPAYMENT_INVOICE": "КП отправлено",
    "C8:UC_DH7ST0": "Потенциальная бронь",
    "C8:EXECUTING": "Бронирование услуг",
    "C8:FINAL_INVOICE": "Бронь подтверждена",
    "C8:UC_N36GSX": "Double Check",
    "C8:UC_GN5IB4": "Оказание услуг",
    "C8:UC_5BZY74": "На проверку",
    "C8:WON": "Сделка состоялась",
    "C8:LOSE": "Не смогли предложить сервис",
    "C8:APOLOGY": "Долгий ответ клиенту",
    "C8:UC_X6DWZX": "Высокая цена",
    "C8:UC_ECZ026": "Другое",
    "C8:UC_31WMNQ": "Агент не отвечает",
    # Реактивация
    "C12:NEW": "Новая",
    "C12:PREPARATION": "В работе",
    "C12:WON": "Сделка успешна",
    "C12:LOSE": "ЧС",
    # Черновик
    "NEW": "Новая",
    "UC_144SIG": "Выявление потребностей",
    "PREPARATION": "Подготовка КП",
    "PREPAYMENT_INVOICE": "КП отправлено",
    "1": "Потенциальный клиент",
    "UC_BVC2WR": "Отправлен счет на оплату",
    "FINAL_INVOICE": "Предоплата",
    "UC_0FP61A": "Оплата получена",
    "UC_J1VQ4U": "Бронирование услуг",
    "UC_EPM3SY": "Бронь подтверждена",
    "UC_HCCI40": "Оказание услуги",
    "UC_SAR1ES": "Услуга оказана",
    "5": "На проверку",
    "WON": "Сделка успешна",
    "LOSE": "Сделка провалена",
    "UC_6STS4Y": "Нет ответа",
    "UC_YISV3E": "Недостаточный бюджет",
    "3": "Изменились планы",
    "UC_Y8ZQBA": "Не смогли предложить сервис",
    "UC_YP27KM": "Аннулировано",
    "UC_8Z483K": "Забронировал у конкурента",
    "2": "Криптовалюта",
    "4": "Высокая цена",
    "APOLOGY": "Другое",
    # Новая воронка
    "C11:NEW": "Новая",
    "C11:PREPARATION": "Взято в работу",
    "C11:PREPAYMENT_INVOIC": "Поиск партнера",
    "C11:UC_38DAXX": "Предложение клиенту",
    "C11:FINAL_INVOICE": "Оплата от клиента",
    "C11:UC_4EMOX6": "Подтверждение партнеру",
    "C11:UC_BKFJHJ": "Контроль рисков",
    "C11:UC_RDCUD1": "Фактический контроль",
    "C11:UC_YK0CXJ": "Потенциальный клиент",
    "C11:EXECUTING": "На проверку",
    "C11:WON": "Сделка успешна",
    "C11:LOSE": "Сделка провалена",
    "C11:APOLOGY": "Анализ причины провала",
    # Командировки
    "C5:NEW": "Новая",
    "C5:PREPAYMENT_INVOICE": "В работе",
    "C5:EXECUTING": "Подтверждена",
    "C5:FINAL_INVOICE": "Оформлена",
    "C5:WON": "Сделка успешна",
    "C5:LOSE": "Сделка провалена",
}


def category_name(category_id: str) -> str:
    return CATEGORY_NAMES.get(str(category_id), f"Воронка ID {category_id}")


def stage_name(stage_id: str) -> str:
    return STAGE_NAMES.get(str(stage_id), str(stage_id))


def category_label(category_id: str) -> str:
    category_id = str(category_id)
    return f"{category_name(category_id)} (ID {category_id})"


def stage_label(stage_id: str) -> str:
    stage_id = str(stage_id)
    name = stage_name(stage_id)
    return name if name == stage_id else f"{name} ({stage_id})"
