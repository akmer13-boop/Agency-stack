# Stage 3.3B — локальная синхронизация Bitrix24

Статус: Stage 3.3B.1 и Stage 3.3B.2 приняты локально; Full + Incremental sync работают на реальной CRM.

## Сетевой фундамент

- единый SOCKS5-прокси для Telegram, OpenAI и Bitrix24;
- поддержка IP-whitelist без логина и пароля;
- секреты прокси не выводятся в логи;
- локально на Windows подтверждены Telegram, OpenAI и Bitrix24 через выделенный IP;
- запись в CRM запрещена: `ALLOW_CRM_WRITE=false`.

## Stage 3.3B.1 — Full CRM Sync

Full sync Run #3 успешно завершён до реального конца данных по ID-cursor.

Получено:

- сделки — 8 114;
- лиды — 18 289;
- контакты — 39 279;
- компании — 1 805;
- CRM-активности — 117 439;
- история стадий сделок — 34 781;
- история стадий лидов — 63 210.

Итого ядро raw CRM содержит 282 917 записей по этим семи наборам на момент Run #3.

Для больших выборок используется ID-cursor:

1. `ID ASC`;
2. `start=-1`;
3. следующая страница с `>ID` последней записи;
4. до реального конца выборки;
5. каждая страница сразу сохраняется в SQLite через upsert.

## Stage 3.3B.2 — Incremental CRM Sync

Команда:

```text
/bitrix_sync_incremental
```

Incremental Run #4 успешно завершён.

Окно изменений:

```text
2026-08-07T10:49:00+00:00
```

Получено в окне:

- сделки — 74;
- лиды — 34;
- контакты — 37;
- компании — 1;
- активности — 195;
- история стадий сделок — 46;
- история стадий лидов — 51.

Checkpoint берётся от последнего успешно завершённого sync с защитным overlap по умолчанию 5 минут.

Поля изменений:

- сделки / лиды / контакты / компании — `DATE_MODIFY`;
- активности — `LAST_UPDATED`;
- история стадий — `CREATED_TIME`.

Настройки:

```env
BITRIX24_SYNC_MAX_PAGES=20000
BITRIX24_SYNC_MAX_ITEMS_PER_ENTITY=0
BITRIX24_SYNC_TIMEOUT_SECONDS=60
BITRIX24_SYNC_RETRY_ATTEMPTS=4
BITRIX24_SYNC_RETRY_BACKOFF_SECONDS=2
BITRIX24_SYNC_PAGE_DELAY_SECONDS=0.25
BITRIX24_SYNC_OVERLAP_MINUTES=5
```

## Ограничения, которые не блокируют MVP

- удалённые сущности пока требуют отдельного reconciliation/tombstone механизма;
- timeline comments ещё не входят в массовый sync;
- товарные позиции и smart processes ещё не входят в raw-sync;
- задачи, Open Lines, телефония, сотрудники и Disk будут запрашиваться по мере конкретных блокеров MVP;
- CRM-write остаётся выключенным.

## Следующий этап

Stage 3.3C — Analytics Core MVP.

Первая версия 0.3.9 добавляет локальные команды:

```text
/rop_today
/rop_funnel
/rop_risks
```

Подробности: `docs/stage-3.3C-mvp.md`.
