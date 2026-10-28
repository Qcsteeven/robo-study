# Шаблон веб-страницы ОП — Робот ОП ИРНИТУ

Jinja2-шаблоны для FastAPI в стиле дизайн-системы «Робот ОП ИРНИТУ» (сайт www.istu.edu: Golos Text, градиентная полоса, тёмная рейка, тонкие рамки).

## Запуск примера

```bash
pip install fastapi uvicorn jinja2
uvicorn app_example:app --reload
# http://127.0.0.1:8000/op/09.03.01-isoiu-2023-och
# тёмная тема: ...?theme=dark
```

## Состав

```
templates/
  base.html      каркас: рейка с меню (кнопка-«гамбургер», Esc закрывает), подвал; блоки title, header, content, footer_note, scripts
  ui.html        макросы компонентов: status, tag, page_header, info_block, section_nav, alert,
                 curriculum_matrix, schedule_grid, doc_row, pairs
  op_page.html   страница ОП: сведения и характеристика, учебный план, КУГ, дисциплины, практики, ГИА, исходные документы;
                 справа — меню разделов (подсвечивается при прокрутке) и сводка последнего запуска
static/
  css/tokens.css  переменные дизайн-системы (светлая и тёмная тема) и @font-face
  css/irnitu.css  компоненты ir-* (копия components/bundle.css из дизайн-системы)
  css/app.css     раскладка страницы, подвал, сетка КУГ
  fonts/          Golos Text 400 и 700 (OFL), латиница + кириллица
  img/            логотип «Иркутский политех» (белый, синий), иконка поиска
sample/op_09.03.01_2023.json   пример контекста страницы
app_example.py                 маршрут /op/{op_id}
```

## Что ждёт шаблон

Контекст — объект как в `sample/op_09.03.01_2023.json`:

- `op` — код, направление, профиль, уровень, форма, год набора, срок, квалификация, `summary` (LLM), `source_url`, `updated_at`;
- `curriculum` — `semesters` и `groups[].rows[]` с `cells["<семестр>"] = {lec, lab, pr, control}` — пары в неделю (часы аудиторных занятий за семестр ÷ 2 ÷ число учебных недель) и форма контроля;
- `schedule` — `academic_year`, `months` [название, недель], `legend`, `courses[].weeks` — строка из 52 кодов (Т, Э, У, П, Д, К);
- `disciplines`, `practices` — название, семестры, контроль/недели, `summary` (LLM), ссылка на страницу РПД/программы практики;
- `gia` — `items` (подпись — значение) и `summary`;
- `documents` — тип, название, `status` (`processed`, `needs_review`, `error`, `in_progress`, `queued`, `unknown_template`, `skipped`), примечание робота, ссылки на PDF и JSON;
- `last_run` — номер, дата, число документов, требуют проверки, ошибок, длительность, ссылка на отчёт;
- `active_nav` (`run`, `programs`, `reports`, `log`) и `theme` (`light`/`dark`) — для каркаса.

Если документы требуют проверки, вверху страницы сам появляется алерт со списком.

Данные в примере условные — для проверки вёрстки, не сведения реальной ОП.
