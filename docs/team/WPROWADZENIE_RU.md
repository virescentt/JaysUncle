# Введение в проект: над чем мы работаем (RU)

> Для тех, кто только подключается. Читать сверху вниз, ~15 минут. Польская версия: [WPROWADZENIE_PL.md](WPROWADZENIE_PL.md)
>
> Дальше: варианты решения — [WARIANTY_RU.md](WARIANTY_RU.md), кто что делает — [ZADANIA_RU.md](ZADANIA_RU.md).

---

## Шаг 1. Откуда задача (контекст за 1 минуту)

В Польше госучреждения покупают товары и услуги через **тендеры** (*zamówienia publiczne*). Если фирма считает, что заказчик провёл тендер нечестно (например, незаконно отклонил её заявку), она подаёт **жалобу** (*odwołanie*) в специальный орган — **KIO** (*Krajowa Izba Odwoławcza*).

Жалоба — это не один документ, а пачка PDF:
- **odwołanie** — сама жалоба: кто жалуется, на кого, по какому тендеру, на что именно;
- **pełnomocnictwo** — доверенность: фирма разрешает юристу действовать от её имени;
- **potwierdzenie przelewu** — подтверждение банковского перевода (оплата сбора).

Сейчас сотрудники **UZP** (*Urząd Zamówień Publicznych*, ведомство госзакупок, организатор задачи) читают это глазами. Они хотят, чтобы **программа сама доставала из PDF нужные данные** и выдавала их в машиночитаемом виде, JSON.

**Наша задача:** PDF на входе → правильный JSON на выходе, для каждого документа.

---

## Шаг 2. Как выглядит одно дело — живой пример

Возьмём дело №1 из обучающих данных организаторов. Все три документа — про одну историю: фирма **Belmoria Rozwiązania S.A.** жалуется на заказчика **Aurelink Automatyka S.A.** по тендеру **ZS.791.2026**.

Файлы: [на GitHub организаторов](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/tree/main/dane/przyklad_01) · локально (после `python scripts/fetch_data.py`): `data/uzp/dane/przyklad_01/`

### 2.1. Подтверждение перевода (самый простой)

Что видит человек в [PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/przelew.pdf):
```
POLECENIE PRZELEWU
Potwierdzenie realizacji polecenia przelewu · Ekobank
Płatnik             Belmoria Rozwiązania S.A.
Rachunek obciążany  PL05470680287581539721455024
Adresat przelewu    Aurelink Automatyka S.A.
Rachunek odbiorcy   PL68438223049992295262834736
Kwota operacji      200,00 PLN
Data transakcji     2026-03-21
Opis                opłata skarbowa od pełnomocnictwa dla Aleksandra Marciniak i Jolanta Zajkowski
                    w postępowaniu przed KIO w przetargu ZS.791.2026 CEN2604016344389
Nr ref.             CEN2604016344389
```
Что должна выдать программа ([эталон организаторов](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/przelew.json)):
```json
{
  "przelew_bankowy": {
    "data_operacji": "2026-03-21",
    "kwota": 200.0,
    "na_rachunek": "68438223049992295262834736",
    "numer_transakcji": "CEN2604016344389",
    "tytul": "opłata skarbowa od pełnomocnictwa dla Aleksandra Marciniak i Jolanta Zajkowski w postępowaniu przed KIO w przetargu ZS.791.2026 CEN2604016344389",
    "z_rachunku": "05470680287581539721455024"
  }
}
```
Что тут видно:
- берём **не всё**: адреса и название банка не нужны;
- значения **приводятся к единому виду**: `200,00 PLN` → `200.0`, `PL05…` → только цифры;
- надо понимать, **какой счёт чей**: «obciążany» = с какого счёта ушли деньги (`z_rachunku`), «odbiorcy» = на какой пришли (`na_rachunek`).

### 2.2. Доверенность

[PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/pelnomocnictwo.pdf), фрагмент:
```
PEŁNOMOCNICTWO
Łódź, 1 kwietnia 2026
§ 1. Mocodawca
Mocodawcą jest Belmoria Rozwiązania S.A., ul. Zaczarowana 78, 90-803 Łódź, KRS 9999405188, NIP 9999284065 ...
§ 2. Pełnomocnik
Mocodawca ustanawiam pełnomocnikiem r. pr. Aleksandra Marciniak (...), r. pr. Jolanta Zajkowski (...)
...
Mocodawca:                      Pełnomocnictwo przyjmuję:
.............................   .............................
Oliwia Kaczmarek                r. pr. Aleksandra Marciniak
Prezes Zarządu                  .............................
                                r. pr. Jolanta Zajkowski
```
→ [JSON](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/pelnomocnictwo.json):
```json
{
  "mocodawca": [
    {
      "data": { "imie_nazwisko": null, "krs": "9999405188", "nazwa": "Belmoria Rozwiązania S.A.",
                "nip": "9999284065", "typ": "osoba_prawna" },
      "podpisujacy": [ { "imie_nazwisko": "Oliwia Kaczmarek" } ]
    }
  ],
  "pelnomocnicy": [
    { "imie_nazwisko": "Aleksandra Marciniak", "pesel": null },
    { "imie_nazwisko": "Jolanta Zajkowski", "pesel": null }
  ]
}
```
- **mocodawca** — кто даёт доверенность (фирма); **podpisujacy** — кто подписал за фирму (левая колонка подписей, должность «Prezes Zarządu» не нужна); **pelnomocnicy** — кому дали (юристы; справа они только «принимают» доверенность, в `podpisujacy` они не идут);
- титул `r. pr.` (радца правный) **отбрасываем**;
- `"pesel": null` — PESEL в документе не указан, значит **пусто, а не выдумываем**;
- `typ`: фирма → `osoba_prawna`; если доверенность даёт частный предприниматель → `osoba_fizyczna`, и тогда заполняется `imie_nazwisko`.

### 2.3. Сама жалоба (самый сложный)

[PDF](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/odwolanie.pdf), начало первой страницы:
```
Prezes Krajowej Izby Odwoławczej, ul. Postępu 17A, 02-676 Warszawa
Odwołujący:
  Belmoria Rozwiązania S.A., ul. Zaczarowana 78, 90-803 Łódź
  KRS: 9999405188, NIP: 9999284065, REGON: 999642785
  reprezentowana przez: r. pr. Aleksandra Marciniak, r. pr. Jolanta Zajkowski
  adres do korespondencji: Kancelaria Adwokacka Spółka z ograniczoną odpowiedzialnością, ...
  tel. 000 000 000, biuro@poczta.example
Zamawiający:
  Aurelink Automatyka S.A., ul. Rybacka 79, 50-580 Wrocław
  KRS: 9999716254, NIP: 9997518659, REGON: 999184816
ODWOŁANIE
dotyczy: postępowania ... pn. „Zakup i dostawa sprzętu komputerowego wraz z oprogramowaniem oraz usługą
wdrożenia” nr referencyjny sprawy: ZS.791.2026, ogłoszenie o zamówieniu nr 2026/BZP00999778,
numer ogłoszenia TED: 999137-2026
...
Zamawiający w dniu 20 marca 2026 roku unieważnił postępowanie.
Zarzut nr 1 naruszenia obowiązku sporządzenia dokumentacji postępowania w sposób przejrzysty: ...
```
Откуда берётся каждое поле [эталонного JSON](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/dane/przyklad_01/odwolanie.json):

| Поле JSON | Значение | Откуда |
|---|---|---|
| `Odwolujacy[0].nazwa` | `Belmoria Rozwiązania S.A.` | блок «Odwołujący» |
| `Odwolujacy[0].krs` / `nip` / `regon` | `9999405188` / … | там же |
| `Odwolujacy[0].pelnomocnik.imie_nazwisko` | `Aleksandra Marciniak` | **только первый** из двух юристов |
| `Odwolujacy[0].pelnomocnik.nazwa` | `Kancelaria Adwokacka Spółka z o.o.` | «adres do korespondencji» |
| `Odwolujacy[0].pelnomocnik.mail` | `biuro@poczta.example` | e-mail в блоке |
| `Zamawiajacy[0].nazwa` / `krs` / … | `Aurelink Automatyka S.A.` / … | блок «Zamawiający» (у него **тоже есть** KRS и NIP — не перепутать!) |
| `ZamowieniePubliczne.tytul` | `Zakup i dostawa sprzętu…` | текст в кавычках после «pn.» |
| `ZamowieniePubliczne.numer_postepowania` | `ZS.791.2026` | «nr referencyjny» |
| `ZamowieniePubliczne.numer_bzp` | `2026/BZP00999778` | номер объявления в BZP |
| `ZamowieniePubliczne.numer_ted` | `999137-2026` | номер в TED (европейский реестр) |
| `OdwolanieKIO.czynnosc_dzien` | `2026-03-20` | «w dniu 20 marca 2026» → формат ISO |
| `OdwolanieKIO.czynnosc_powod_id` | `9` | **по смыслу**: «unieważnił postępowanie» = «отменил тендер» = категория 9 из справочника |
| `OdwolanieKIO.czynnosc_opis` | `Decyzja o unieważnieniu postępowania` | текст категории 9 из справочника |
| `OdwolanieKIO.data` / `miejscowosc` | `null` / `null` | в шапке **нет** строки «Город, дата» → пусто |

Справочник категорий (1–12): [config/dictionaries/czynnosc_powod.yaml](../../config/dictionaries/czynnosc_powod.yaml). Описание всех полей от организаторов: [oczekiwane-json/odwolanie.json](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/oczekiwane-json/odwolanie.json).

---

## Шаг 3. Что делает наша программа (вход → выход)

Жюри кладёт дела в папку `input/`, запускает `start.bat` (или `./start.sh`), а программа пишет JSON в `output/` **с теми же именами**:
```
input/                                  output/
├── case1/                              ├── case1/
│   ├── odwolanie_nr_1.pdf       ──►    │   ├── odwolanie_nr_1.json
│   ├── pelnomocnictwo_nr_1.pdf  ──►    │   ├── pelnomocnictwo_nr_1.json
│   ├── przelew_nr_1.pdf         ──►    │   ├── przelew_nr_1.json
│   └── ogloszenie_TED.pdf       ──►    │   └── ogloszenie_TED.json   (пустой файл — «чужой» документ)
```
- **Тип документа по имени файла не определить**: в тестах имена могут быть любыми. Программа сама читает содержимое и решает: «это доверенность».
- **Чужие документы пропускаем.** У организаторов есть [«негативный пример»](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/szablon-aplikacja/input/case1/negatywny%20przyklad.pdf) — объявление о тендере из TED. Для таких документов программа пишет **пустой файл `.json`** — так требуют организаторы. Ещё 12 таких документов (решения KIO, объявления TED/BZP, брошюры) лежат в [dane/negatywne](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/tree/main/dane/negatywne).

---

## Шаг 4. Почему это сложно

**1. Каждый документ выглядит по-своему.** В 60 примерах больше 20 разных «банков», и одно и то же поле подписано по-разному:

| Поле | Как подписано в разных документах |
|---|---|
| счёт получателя | `Rachunek odbiorcy` · `Rachunek beneficjenta` · `Rachunek uznawany` |
| номер операции | `Nr ref.` · `Numer referencyjny` · `Identyfikator operacji` · `Numer transakcji` |
| дата | `2026-03-21` · `21.03.2026` · `4 marca 2026` · `3 lipca 2026 roku` |
| заголовок | `PEŁNOMOCNICTWO` · `P E Ł N O M O C N I C T W O` · `Pełnomocnictwo` |

**2. Настоящие документы — сканы.** Единственный настоящий пример у организаторов — [скан жалобы](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/szablon-aplikacja/input/case1/przyklad%20odwolanie.pdf): картинки страниц без текста, часть данных закрашена чёрным (анонимизация). Чтобы прочитать такое, нужен **OCR** (распознавание текста с картинки), а он ошибается: `Odwołujący` → `Odwolujacy`, `Spółka` → `Spóka`.

**3. Часть полей требует понимания смысла.**
- `czynnosc_powod_id`: «unieważnił postępowanie» (отменил тендер) = **9**, а «zaniechanie unieważnienia» (НЕ отменил) = **10**. Слова почти одинаковые, смысл противоположный.
- При **консорциуме** (несколько фирм жалуются вместе) берём только **лидера**.
- В документе бывает «**Wykonawca wybrany**» (победитель тендера). Это третья фирма, её нельзя путать с заявителем.
- В настоящих документах имена **склоняются**: «przez radcę prawnego **Jana Kowalskiego**», а в JSON нужно «**Jan Kowalski**».

**4. Лучше пусто, чем неправильно.** Это следует из того, как нас оценивают (шаг 5).

---

## Шаг 5. Как нас оценивают — метрика F1

Жюри прогонит программу на **≥10 настоящих делах, которых мы не видели**, и сравнит **каждое поле** нашего JSON с правильным ответом. На первом этапе вся оценка — одна цифра F1 (от 0 до 1). В финал проходят 3 лучшие команды.

Мини-пример на 4 полях:

| Поле | Правильно | Мы выдали | Итог |
|---|---|---|---|
| `kwota` | `200.0` | `200.0` | ✅ попали (TP) |
| `numer_transakcji` | `CEN2604…` | `CEN9999…` | ❌ ошибка: минус точность **и** полнота (FP + FN) |
| `tytul` | `opłata skarbowa…` | `null` | ⚪ пропустили: минус полнота (FN) |
| `pesel` | `null` | `85120133547` | ❌ выдумали: минус точность (FP) |

- **Точность (precision)** — из того, что мы заполнили, сколько правильно.
- **Полнота (recall)** — из того, что нужно было заполнить, сколько мы нашли.
- **F1** — их среднее. Высокое, только если хороши оба.

Отсюда правило: **неправильное значение стоит дороже, чем пустое.** Если не уверены, лучше `null`.

Наш скрипт `python -m evaluation.run_eval` считает F1 на 60 обучающих делах за ~10 секунд и показывает, какие поля хуже всего. Это наш главный инструмент. Как именно считают организаторы (точное совпадение или с поблажками), мы ещё уточняем у менторов.

---

## Шаг 6. Как наша программа обрабатывает один документ

Тот же перевод из шага 2.1 проходит пять этапов:

```
przelew.pdf
   │
   ▼ 1. INGEST — достаём текст
   │    Есть текстовый слой → берём его. Скан → OCR.
   │
   ▼ 2. CLASSIFY — что это за документ?
   │    Ищем «приметы» каждого типа (они описаны в YAML) и считаем баллы:
   │      przelew: +10 (есть «polecenie przelewu», «rachunek odbiorcy», сумма, IBAN)
   │      pelnomocnictwo: −4    odwolanie: −4
   │    → это przelew. Если у всех мало баллов → «чужой документ», пишем пустой JSON.
   │
   ▼ 3. EXTRACT — ищем значения полей
   │    Правила из YAML: поле na_rachunek ищется после меток
   │      [Rachunek odbiorcy, Rachunek beneficjenta, Rachunek uznawany] → "PL68438223…"
   │    + LLM (нейросеть) для смысловых полей — получает описание полей из того же YAML.
   │
   ▼ 4. MERGE — выбираем лучшее и проверяем
   │    Значение реально есть в тексте? Контрольная сумма счёта сходится?
   │    Не уверены → null.
   │
   ▼ 5. NORMALIZE — приводим к формату
   │    "PL68 4382 2304 …" → "68438223049992295262834736",  "200,00 PLN" → 200.0
   │
   ▼
output/.../przelew.json                   +   traces/.../przelew.trace.json
                                               (откуда взято каждое поле: страница, кусок текста,
                                                метод, уверенность — показываем в панели)
```

**Главная идея архитектуры:** всё, что относится к конкретному типу документа (приметы, поля, метки, форматы), лежит в **одном YAML-файле** — [config/doc_types/przelew.yaml](../../config/doc_types/przelew.yaml). Код общий для всех типов. Чтобы научить программу новому типу документа, достаточно **добавить YAML, не трогая код**. За это дают половину баллов в финале.

---

## Шаг 7. Что уже есть и что делаем

**Готово (скелет):**
- весь конвейер из шага 6 запускается (`./start.sh`), CLI, API для панели, временная страница-заглушка;
- классификация: **180 из 180** обучающих PDF определяются верно, «чужой» документ отбрасывается, скан распознаётся;
- метрика F1 и прогон по 60 делам; задачи VS Code («Przed PR»);
- конфиги трёх типов документов с описаниями всех полей.

**Текущий F1 ≈ 0.18** — это точка отсчёта: большинство функций ещё заглушки. Делаем ([кто что — ZADANIA_RU.md](ZADANIA_RU.md)):
- нормализаторы (даты, суммы, счета, номера BZP/TED) — **Артём**;
- поиск по меткам и блокам документа (правила) — **Лёша**;
- OCR для сканов, синтетические сканы для проверки — **Сева**;
- подключение нейросети, слияние результатов, сложные поля — **Полина**;
- панель с подсветкой источников, документация, презентация — **Артём** (после нормализаторов).

---

## Шаг 8. Словарь

| Термин | Что это |
|---|---|
| **UZP** | Urząd Zamówień Publicznych — ведомство госзакупок, организатор задачи |
| **KIO** | Krajowa Izba Odwoławcza — орган, который рассматривает жалобы по тендерам |
| **odwołanie** | жалоба фирмы на действия заказчика |
| **Odwołujący** | тот, кто жалуется (фирма-участник тендера) |
| **Zamawiający** | заказчик (госучреждение, проводящее тендер) |
| **pełnomocnictwo / pełnomocnik / mocodawca** | доверенность / тот, кому её дали (юрист) / тот, кто её дал (фирма) |
| **konsorcjum, lider** | несколько фирм подают заявку вместе; лидер — главная из них |
| **zarzut** | пункт обвинения в жалобе («заказчик нарушил то-то») |
| **czynność** | действие (или бездействие) заказчика, на которое жалуются |
| **SWZ** | Specyfikacja Warunków Zamówienia — условия тендера |
| **OPZ** | Opis Przedmiotu Zamówienia — описание того, что закупают |
| **BZP** | Biuletyn Zamówień Publicznych — польский реестр объявлений (номер вида `2026/BZP00999778`) |
| **TED** | европейский реестр объявлений (номер вида `999137-2026`) |
| **KRS / NIP / REGON** | номера фирмы: в реестре компаний / налоговый / статистический |
| **PESEL** | личный номер гражданина (11 цифр) |
| **IBAN / NRB** | номер банковского счёта (26 цифр, в Польше с префиксом PL) |
| **JSON** | текстовый формат «поле: значение», который читают программы |
| **YAML** | простой текстовый формат настроек — у нас в нём описаны типы документов |
| **OCR** | распознавание текста с картинки (для сканов) |
| **LLM** | большая языковая модель, нейросеть (у нас только open-source) |
| **regex** | шаблон поиска текста («после слова NIP — 10 цифр») |
| **F1** | оценка качества от 0 до 1: точность + полнота (шаг 5) |
| **venv (.venv)** | изолированная папка с Python-библиотеками проекта |

---

## Шаг 9. С чего начать (первые 30 минут)

1. Установить окружение: `./setup.sh` (Windows: `.\setup.bat`) — подробно в [ZADANIA_RU.md](ZADANIA_RU.md), раздел «Старт».
2. Скачать данные: `python scripts/fetch_data.py --sample-input 3`.
3. Открыть рядом PDF и JSON из `data/uzp/dane/przyklad_01/` и сверить с таблицей из шага 2.3.
4. Запустить программу: `./start.sh` → посмотреть, что появилось в `output/` и `traces/`.
5. Посмотреть, как программа «видит» документ: `python -m odwolania2json inspect data/uzp/dane/przyklad_01/odwolanie.pdf`.
6. Запустить метрику: `python -m evaluation.run_eval` → таблица «Najgorsze pola» показывает, что сейчас не работает.
7. Открыть свой YAML или файл из [ZADANIA_RU.md](ZADANIA_RU.md) и начать.
