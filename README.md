# PISTOL GENESIS — core v1

Архивный HTTP-код официально заменён опубликованным прототипом `level1_core`.
Новый этап: [ADR-0002](docs/adr/0002-transfer-roles-ci.md) — resume/rollback, роли, CI и наблюдаемость.
[ADR-0001](docs/adr/0001-transport-boundaries-versioning.md) закрепляет транспорт,
границы уровней и несовместимость со старым API.

| Компонент | Статус |
|---|---|
| HTTPS JSON /v1, mTLS, CA/hostname/pins и allowlist | Реализовано |
| Discovery разрешённых peers с backoff/jitter | Реализовано отдельным процессом уровня 1 |
| Snapshot в памяти: SHA256, staged activation, чанки/resume, откат до release | Реализовано |
| Роли control/data, correlation ID, JSON-логи, health/readiness | Реализовано |
| Сквозной e2e с внешним HTTP-источником и адаптером | Реализовано |
| Кросс-репозиторный CI и frozen v1 compatibility в обе стороны | Workflow и сценарии добавлены; результаты в Actions |
| Один stateless mock | Реализовано |
| JSON Schema, ограниченный validator, тесты версий | Реализовано |
| Числовые агрегаты и MAE/RMSE | В отдельном [уровне 2](https://github.com/kirillpistol/genesis-level-2) |
| Адаптер данных | В отдельном [уровне 3](https://github.com/kirillpistol/genesis-level-3) |
| Обучение и обмен между алгоритмами | Отложено, endpoint отсутствует |
| Автозапуск на узлах, GPU, sandbox, универсальный автономный бинарник | Не реализовано |

## Быстрый локальный запуск

Python 3.10+, внешних Python-библиотек нет. Из папки репозитория:

```sh
python -m level1_core.server --dev-local
```

http://127.0.0.1:8080/v1/status показывает режим, ожидание, число выполнений/ошибок,
время обработки и revision. Запуск без --dev-local требует --tls-config.
Mock доступен в реестре, но подключается явно:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/v1/algorithms/attach -Method Post -ContentType 'application/json' -Body '{"api_version":"1.0","name":"mock"}'
Invoke-RestMethod http://127.0.0.1:8080/v1/tasks -Method Post -ContentType 'application/json' -Body '{"api_version":"1.0","algorithm":"mock","data":"hello"}'
```

## mTLS, discovery и перенос

Только для локального теста, openssl должен быть в PATH:

```sh
python -m examples.make_test_certs .local-certs
python -m level1_core.server --port 8443 --tls-config .local-certs/server.json
```

В другом терминале запустите второй сервер:

```sh
python -m level1_core.server --port 8444 --tls-config .local-certs/server.json
```

Discovery и перенос запускаются отдельно:

```sh
python -m level1_core.discovery --config .local-certs/client.json
python -m level1_core.handoff --config .local-certs/client.json --source https://localhost:8443 --destination https://localhost:8444
```

Discovery регулярно проверяет только endpoints из заданной конфигурации; не сканирует
сеть. peers отображает разрешённый HTTPS origin на список SHA256 сертификатов.
Сервер требует сертификат разрешённого клиента. Никаких redirects или env proxies.
Генератор выдаёт локальные сертификаты на 2 дня, не production PKI; не коммитьте ключи.
Для разных машин нужны сертификаты с соответствующими DNS SAN и собственные peers.

Перед передачей алгоритмов приёмник должен иметь тот же доверенный реестр/версии.
После подтверждения restore переключите источник данных на приёмник и завершите
исходный процесс. Source остаётся paused после release. Resume поддержан в RAM; откат возможен до release через --rollback-transfer-id ID.
Повтор handoff продолжает передачу с подтверждённого offset на том же destination_instance.
Если процесс назначения перезапущен, старый snapshot адресован другому instance:
автоматического восстановления после этой ситуации пока нет.

## API и схемы

| Запрос | Вход | Результат |
|---|---|---|
| GET /v1/status | — | status |
| GET /v1/health | — | alive |
| GET /v1/readiness | — | ready; 503 если paused или без алгоритма |
| POST /v1/transfers/* | prepare/begin/download/chunk/progress/finish/release/activate/cancel/abort | Новый staged протокол, см. ADR-0002 |
| POST /v1/algorithms/attach | api_version, name | status |
| POST /v1/tasks | api_version, algorithm, data | result, revision |
| POST /v1/snapshot | api_version, destination_instance | snapshot; source paused |
| POST /v1/restore | snapshot целиком | status; только свежий destination |

Схемы в contracts/v1. Validator поддерживает только используемое подмножество
JSON Schema и отклоняет неизвестные keywords. Не является универсальным JSON Schema engine.
Размер тела, результата и snapshot — до 65536 байт, вложенность до 16, до 16 алгоритмов.
Несовместимая версия/неизвестное управляющее поле/NaN/повторный JSON-ключ отклоняются.
Полный ответ /tasks также ограничен 65536 байтами с учётом envelope.

## Проверка с уровнем 2

Клонируйте genesis-level-2 рядом с этим репозиторием. Linux/macOS:

```sh
export PYTHONPATH=../genesis-level-2:../genesis-level-3
export GENESIS_REQUIRE_E2E=1
python -m unittest discover -s tests -v
python -m examples.local_demo
python -m level1_core.server --dev-local --algorithm-module level2_algorithms.numeric
```

PowerShell: `$env:PYTHONPATH = '../genesis-level-2;../genesis-level-3'` и `$env:GENESIS_REQUIRE_E2E='1'`, далее те же команды python.
Демо использует реальный локальный HTTP: 10,20,30 → snapshot → 40 → mean=25;
фиксированный прогноз 2 на targets 1,3 даёт MAE=1, RMSE=1.
Тесты mTLS проверяют тот же числовой сценарий, сертификаты, pins, версии и перенос.
Для тестов сертификатов нужен openssl; без соседнего уровня 2 интеграционные
числовые тесты явно пропускаются. Базовое ядро запускается без уровня 2.

## Ограничения

Алгоритмы пока исполняются как доверенный локальный код в процессе ядра;
import не выполняется по сетевому запросу. Нет sandbox и отмены зависшего process().
Сетевой сервер ограничивает рабочие соединения, но это не production DoS-защита:
TLS handshake обрабатывается последовательно с timeout 3 секунды.
client_roles разделяет control/data. Legacy client_pins даёт обе роли для совместимости; новый генератор создаёт отдельные client.json (control) и data.json (data).
Никакой гарантии отсутствия уязвимостей не заявляется.

Нет журнала задач, автоматически сохраняемых записей/результатов и секретов в snapshot.
raw_records_retained=0 описывает журнал ядра, не аудит алгоритма/swap/прокси.
После потери процессов RAM-состояние исчезает. /tasks не имеет exactly-once:
повтор после timeout может повторно обработать запись. Handoff не запускает код
на новом сервере и не переносит стек/GPU. Метрики реальной RAM/CPU пока отсутствуют.

Исходный `genesis_core.py` и `inventory.py` сохранены; планировщик ресурсов не
подключён к HTTP runtime. `config.example.json` относится только к старому inventory.

## Новый e2e и CI

Сквозной сценарий в tests/test_e2e.py использует отдельный внешний HTTP-источник,
Adapter уровня 3, mTLS, Discovery.choose, частичный snapshot и потерянный ACK,
числовой алгоритм и evaluator. Проверяется отсутствие повторной обработки задач
в этом сценарии; общего task deduplication нет. Отдельный тест обрывает TLS request body.

CI .github/workflows/integration.yml существует во всех трёх репозиториях.
GENESIS_REQUIRE_E2E=1 превращает отсутствие модулей уровней 2/3 в ошибку, а не skip.
Матрица old-client/new-server и наоборот использует фиксированные commit-ы v1;
ci/compatibility.py запускает участников в отдельных процессах и проверяет старый
wire-контракт с numeric, адаптером, snapshot и evaluator.

Пример ролей в server TLS configuration:
```json
{"tls":{"ca":"ca.crt","cert":"server.crt","key":"server.key"},"client_roles":{"CONTROL_CERT_SHA256":["control"],"DATA_CERT_SHA256":["data"]}}
```
Замените placeholders на SHA256 DER сертификатов (64 lowercase hex).
Клиент источника данных использует .local-certs/data.json. Управляющий клиент — client.json.
Логи по умолчанию выводятся в stderr в CLI; тела/секреты в них не включаются.
Чтение /v1/status показывает алгоритмы и счётчики; health/readiness требуют mTLS в сетевом режиме.

Откат выполняйте только до source release. При ошибке после release повторите handoff
для активации приёмника. Новая передача не подписана цифровой подписью, конфигурация
сертификатов не обновляется без рестарта, процесс алгоритма пока не изолирован.
