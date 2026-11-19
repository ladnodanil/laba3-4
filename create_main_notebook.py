from pathlib import Path
import nbformat as nbf

cells = []
def md(s): cells.append(nbf.v4.new_markdown_cell(s))
def py(s): cells.append(nbf.v4.new_code_cell(s))

md('''# Анализ данных IT-проектов: KDD и Монте-Карло

**Цель:** исследовать возможность и ограничения имитационного моделирования Монте-Карло для вероятностного прогноза сроков завершения работ в Agile-командах на основе исторического потока задач.

Анализ охватывает отдельные задачи обоих проектов.''')

md('''## 1. Отбор данных

Единица анализа — задача. Сопоставляем `issues.csv` с `projects.csv` по `project_id`. Справочник команд и история статусов нужны для расчёта метрик потока.''')

md('''## 2. Предобработка

Проверяем идентификаторы, даты, пропуски и дубликаты. Пустая дата завершения ожидаема для открытой задачи.''')
py('''from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display, Markdown

# Ищем исходные CSV при запуске из корня проекта либо из папки src.
candidates = [Path('raw/dataset0304'), Path('src/raw/dataset0304'), Path('../raw/dataset0304')]
DATA_DIR = next((p for p in candidates if (p / 'issues.csv').exists()), None)
if DATA_DIR is None:
    raise FileNotFoundError('Не найдена папка raw/dataset0304 с issues.csv')
issues = pd.read_csv(DATA_DIR / 'issues.csv')
teams = pd.read_csv(DATA_DIR / 'teams.csv')
history = pd.read_csv(DATA_DIR / 'issue_status_history.csv')
projects = pd.read_csv(DATA_DIR / 'projects.csv')
issues['created_at'] = pd.to_datetime(issues.created_at, errors='coerce')
issues['resolved_at'] = pd.to_datetime(issues.resolved_at, errors='coerce')
history['changed_at'] = pd.to_datetime(history.changed_at, errors='coerce')

quality = pd.DataFrame([
 ['Строк задач', len(issues)], ['Дубли issue_id', issues.issue_id.duplicated().sum()],
 ['Пропущен/неверен created_at', issues.created_at.isna().sum()],
 ['Пропущен resolved_at', issues.resolved_at.isna().sum()],
 ['resolved_at раньше created_at', (issues.resolved_at < issues.created_at).sum()],
 ['Задач Done без resolved_at', (issues.current_status.eq('Done') & issues.resolved_at.isna()).sum()],
 ['Строк истории с неверной датой', history.changed_at.isna().sum()],
 ['Дубли событий истории', history.duplicated().sum()],
], columns=['Проверка качества', 'Количество'])
display(quality)
history = history.drop_duplicates().copy()
task_columns = ['issue_id', 'project_id', 'team_id', 'created_at', 'resolved_at', 'current_status', 'project_name']
task_table = issues.merge(projects[['project_id', 'project_name']], on='project_id',
                          how='left', validate='many_to_one')[task_columns]
display(task_table.head(10).reset_index(drop=True).style.hide(axis='index'))''')

md('''## 3. Преобразование (Transformation)

Для каждого проекта формируем ежедневный ряд throughput с нулевыми днями, Cycle Time и WIP. Граница потока начинается при первом переходе в `Selected for Development` (если его нет — в `In Progress`; при отсутствии обоих берётся `created_at`). WIP включает задачи от этого момента до Done. Проверяем допущения закона Литтла: входящий и исходящий потоки должны быть близки, WIP не должен устойчиво расти, начатые задачи должны завершаться. Сравнение `WIP ≈ throughput × Cycle Time` используем как диагностику, а не как самостоятельное доказательство стабильности.''')
py('''valid = issues.loc[
    issues.created_at.notna() & issues.resolved_at.notna()
    & issues.resolved_at.ge(issues.created_at)
].drop_duplicates('issue_id').copy()
status_order = ['Backlog','Selected for Development','In Progress','Blocked','Code Review','QA','Reopened','Done']
active_statuses = ['Selected for Development','In Progress','Blocked','Code Review','QA','Reopened']

def daily_status_counts(issue_ids, dates):
    ev = history.loc[history.issue_id.isin(issue_ids)].drop_duplicates().sort_values('changed_at')
    ev = list(ev[['issue_id','status','changed_at']].dropna(subset=['changed_at']).itertuples(index=False, name=None))
    state = {}; pos = 0; rows = []
    for day in dates:
        cutoff = day + pd.Timedelta(days=1)
        while pos < len(ev) and ev[pos][2] < cutoff:
            state[ev[pos][0]] = ev[pos][1]; pos += 1
        counts = pd.Series(state.values()).value_counts()
        rows.append([int(counts.get(s,0)) for s in status_order])
    return pd.DataFrame(rows, index=dates, columns=status_order)

project_data = {}
metric_rows = []
for p in projects.itertuples(index=False):
    all_p = issues.loc[issues.project_id.eq(p.project_id)].copy()
    done = valid.loc[valid.project_id.eq(p.project_id)].copy()
    starts = history.loc[history.issue_id.isin(all_p.issue_id) & history.status.isin(['Selected for Development','In Progress'])].sort_values('changed_at').drop_duplicates('issue_id', keep='first').set_index('issue_id')['changed_at']
    start_at = all_p.set_index('issue_id').join(starts.rename('flow_start'))['flow_start'].fillna(all_p.set_index('issue_id').created_at)
    done = done.join(start_at.rename('flow_start'), on='issue_id')
    done['resolved_day'] = done.resolved_at.dt.floor('D')
    done['cycle_days'] = (done.resolved_at - done.flow_start).dt.total_seconds() / 86400
    dates = pd.date_range(done.resolved_day.min(), done.resolved_day.max(), freq='D')
    tp = done.groupby('resolved_day').size().reindex(dates, fill_value=0).astype(int)
    cfd = daily_status_counts(all_p.issue_id, dates)
    wip = cfd[active_statuses].sum(axis=1)
    started_day = start_at.dt.floor('D')
    arrivals = started_day.value_counts().reindex(dates, fill_value=0)
    little = tp.mean() * done.cycle_days.mean()
    metric_rows.append([p.project_key, len(all_p), len(done), len(dates), int(tp.eq(0).sum()), round(tp.mean(),3), round(done.cycle_days.median(),2), round(done.cycle_days.mean(),2), round(wip.mean(),2), round(little,2), round(arrivals.mean(),3), round(tp.mean(),3), round(np.polyfit(np.arange(len(wip)),wip,1)[0],4)])
    project_data[p.project_id] = {'name':p.project_key, 'all':all_p, 'done':done, 'dates':dates, 'throughput':tp, 'wip':wip, 'cfd':cfd, 'history':history.loc[history.issue_id.isin(all_p.issue_id)].dropna(subset=['changed_at'])}
project_metrics = pd.DataFrame(metric_rows, columns=['Проект','Задач','Завершено с валидными датами','Дней наблюдений','Дней с нулевым throughput','Средний throughput/день','Медианный Cycle Time, дни','Средний Cycle Time, дни','Средний WIP','Throughput × Cycle Time','Средний входящий поток/день','Средний исходящий поток/день','Наклон WIP/день'])
display(project_metrics)

little_check = project_metrics[['Проект', 'Средний WIP', 'Throughput × Cycle Time',
                                'Средний входящий поток/день', 'Средний исходящий поток/день',
                                'Наклон WIP/день']].copy()
little_check['Разница, задач'] = little_check['Средний WIP'] - little_check['Throughput × Cycle Time']
display(Markdown('### Проверка по закону Литтла'))
display(little_check.round(3))
display(Markdown(
    'Сопоставляем средний WIP с произведением среднего throughput на средний Cycle Time. '
    'Расхождение проверяем вместе с балансом входа и выхода и трендом WIP. '
    'На сравнение также влияют незавершённые задачи на границе периода и расхождения '
    'между датой закрытия и историей статусов.'
))

daily_rows = []
for data in project_data.values():
    project_days = pd.DataFrame({
        'date': data['dates'], 'project': data['name'],
        'throughput': data['throughput'].to_numpy(), 'wip': data['wip'].to_numpy(),
    })
    daily_rows.append(project_days)
daily_metrics = pd.concat(daily_rows, ignore_index=True)
cycle_times = pd.concat([
    data['done'][['issue_id', 'project_id', 'team_id', 'flow_start', 'resolved_at', 'cycle_days']]
    for data in project_data.values()
], ignore_index=True)
display(Markdown('### Cycle Time отдельных задач и ежедневные метрики проектов'))
display(cycle_times.groupby('project_id').head(3))
display(daily_metrics.groupby('project').head(7))

# team_id сохраняем как разрез, чтобы проверить, какие команды формируют поток проекта.
team_rows = []
team_daily_rows = []
for p in projects.itertuples(index=False):
    for team_id, part in issues.loc[issues.project_id.eq(p.project_id)].groupby('team_id'):
        td = valid.loc[valid.project_id.eq(p.project_id) & valid.team_id.eq(team_id)].copy()
        team_name = teams.loc[teams.team_id.eq(team_id), 'team_name'].iloc[0]
        dates = project_data[p.project_id]['dates']
        team_tp = td.groupby(td.resolved_at.dt.floor('D')).size().reindex(dates, fill_value=0)
        team_daily_rows.append(pd.DataFrame({
            'date': dates, 'project': p.project_key, 'team_id': team_id,
            'throughput': team_tp.to_numpy(),
        }))
        if td.empty:
            team_rows.append([p.project_key, team_name, len(part), 0, np.nan, np.nan])
            continue
        td['resolved_day'] = td.resolved_at.dt.floor('D')
        team_starts = history.loc[history.issue_id.isin(td.issue_id) & history.status.isin(['Selected for Development','In Progress'])].sort_values('changed_at').drop_duplicates('issue_id', keep='first').set_index('issue_id')['changed_at']
        team_start_at = td.set_index('issue_id').join(team_starts.rename('flow_start'))['flow_start'].fillna(td.set_index('issue_id').created_at)
        ctime = (td.set_index('issue_id').resolved_at - team_start_at).dt.total_seconds() / 86400
        team_rows.append([p.project_key, team_name, len(part), len(td), round(team_tp.mean(),3), round(ctime.median(),2)])
team_metrics = pd.DataFrame(team_rows, columns=['Проект','Команда (team_id)','Задач','Завершено','Средний throughput/день','Медианный Cycle Time, дни'])
display(team_metrics)
team_daily_metrics = pd.concat(team_daily_rows, ignore_index=True)
display(team_daily_metrics.groupby(['project', 'team_id']).head(2))''')

md('''### Графики потока: throughput и CFD''')
py('''fig, axes = plt.subplots(len(project_data), 2, figsize=(15, 5 * len(project_data)), squeeze=False, constrained_layout=True)
for row, (project_id, d) in enumerate(project_data.items()):
    d['throughput'].plot(ax=axes[row,0], color='#2864a5', linewidth=1)
    axes[row,0].set(title=f"{d['name']}: throughput по дням", xlabel='Дата', ylabel='Завершено задач')
    axes[row,0].grid(alpha=.2)
    d['cfd'].plot.area(ax=axes[row,1], stacked=True, linewidth=0)
    axes[row,1].set(title=f"{d['name']}: CFD, задачи по статусам", xlabel='Дата', ylabel='Задач')
    axes[row,1].legend(ncol=2, fontsize=7)
plt.show()''')

md('''## 4. Монте-Карло для отдельной задачи

Прогнозируем календарное время от создания задачи до её завершения. Для каждого проекта выбираем дату отсечения по 80-му процентилю дат создания. История включает только задачи, завершённые до отсечения. Из их длительностей делаем 10 000 случайных выборок с возвращением и вычисляем P50, P85 и P95. Эти оценки описывают сроки при условии, что новые задачи сопоставимы с историческими.''')

py('''rows = []
fig, axes = plt.subplots(len(project_data), figsize=(10, 4 * len(project_data)), squeeze=False)
for ax, (project_id, data) in zip(axes.flat, project_data.items()):
    cutoff = data['all'].created_at.dropna().quantile(0.8).normalize()
    completed = data['done']
    training = completed.loc[completed.resolved_at < cutoff]
    durations = (training.resolved_at - training.created_at).dt.total_seconds() / 86400
    rng = np.random.default_rng(42 + int(project_id))
    simulated = rng.choice(durations.to_numpy(), size=10_000, replace=True)
    p50, p85, p95 = np.quantile(simulated, [0.50, 0.85, 0.95])
    recent = completed.loc[completed.created_at >= cutoff]
    actual = (recent.resolved_at - recent.created_at).dt.total_seconds() / 86400
    open_count = data['all'].loc[data['all'].created_at >= cutoff, 'resolved_at'].isna().sum()
    coverage = (actual <= p85).mean() * 100
    rows.append([data['name'], cutoff.date(), len(training), len(recent), open_count,
                 p50, p85, p95, coverage])
    ax.hist(simulated, bins=25, color='#2864a5')
    ax.axvline(p85, color='#b2532b', linestyle='--', label=f'P85 = {p85:.1f} дн.')
    ax.set(title=data['name'], xlabel='Календарных дней от создания до завершения', ylabel='Симуляций')
    ax.legend()
plt.tight_layout()
plt.show()
forecast = pd.DataFrame(rows, columns=[
    'Проект', 'Дата отсечения', 'Задач в истории', 'Завершено после отсечения (новые задачи)',
    'Открытых новых задач', 'P50, дни', 'P85, дни', 'P95, дни', 'Завершённые в пределах P85, %'
])
display(forecast.round(2))''')

md('''## 5. Интерпретация

P85 — ориентир, в который укладываются около 85% симулированных длительностей. Последний столбец показывает, какая доля завершённых задач из отложенной выборки уложилась в этот срок. Открытые задачи показаны отдельно. Их исключение из проверки может завысить наблюдаемую долю, особенно если они остаются открытыми дольше обычного. Прогноз следует пересчитывать при изменении состава задач и темпа работы.''')

md('''## Аналитический обзор публикаций

### Исследовательские вопросы

1. **RQ1:** Какие принципы и метрики Kanban поддерживают управляемый поток разработки?
2. **RQ2:** Как исторические данные и симуляция Монте-Карло применяются для вероятностного прогноза сроков Agile-работ?
3. **RQ3:** Какие условия и факторы ограничивают надёжность таких прогнозов?
4. **RQ4:** Как перцентили и вероятность дедлайна помогают принимать решения о сроках задач?

### Выбранные публикации и краткий синтез

1. Ahmad, M. O., Dennehy, D., Conboy, K., Oivo, M. *Kanban in software engineering: A systematic mapping study*. Journal of Systems and Software, 2018. DOI: [10.1016/j.jss.2017.11.045](https://doi.org/10.1016/j.jss.2017.11.045). Обзор показывает рост интереса к Kanban, но отмечает ограниченность строгих эмпирических доказательств.
2. dos Santos, P. S. M. et al. *On the benefits and challenges of using kanban in software engineering: a structured synthesis study*. Journal of Software Engineering Research and Development, 2018. DOI: [10.1186/s40411-018-0057-1](https://doi.org/10.1186/s40411-018-0057-1). Синтез исследований выделяет видимость работы, контроль задач и поток среди наиболее подтверждённых преимуществ; культура организации остаётся значимым препятствием.
3. Sjøberg, D. *An empirical study of WIP in kanban teams*. Proceedings of ESEM, 2018. DOI: [10.1145/3239235.3239238](https://doi.org/10.1145/3239235.3239238). Исследование более 8 000 рабочих элементов обнаруживает связь WIP и lead time, но предупреждает, что выбор оптимального WIP и влияние на производительность зависят от контекста.
4. Lunesu, M. I. et al. *Assessing the Risk of Software Development in Agile Methodologies Using Simulation*. IEEE Access, 2021. DOI: [10.1109/ACCESS.2021.3115941](https://doi.org/10.1109/ACCESS.2021.3115941). Авторы используют Jira-данные и симуляцию Agile-процесса для анализа распределений длительности и рисков; качество вывода зависит от адекватности модели процесса и исходных данных.
5. Middleton, P., Joyce, D. *Lean Software Management: BBC Worldwide Case Study*. IEEE Transactions on Engineering Management, 2012. DOI: [10.1109/TEM.2010.2081675](https://doi.org/10.1109/TEM.2010.2081675). Кейс описывает улучшение lead time и стабильности поставки после внедрения визуализации, меньших партий и статистического контроля потока; результаты одного кейса не следует автоматически обобщать.
6. Denisov, N. et al. *Developing an Agile Project Management System with an Intellectual Module for Project Duration Prediction*. Sustainable Development and Engineering Economics, 2024. DOI: [10.48554/SDEE.2024.3.1](https://doi.org/10.48554/SDEE.2024.3.1). Работа описывает интеграцию оценки и Монте-Карло в систему управления Agile-проектом и подчёркивает динамическое обновление прогноза.

### Ответы на RQ

- **RQ1:** визуализация потока, ограничение WIP, явные правила, измерение throughput/cycle time и CFD дают команде основу для наблюдения и улучшения процесса; эффект зависит от контекста.
- **RQ2:** эмпирический throughput можно многократно выбирать с возвращением, суммировать до достижения объёма работ и получать распределение сроков, перцентили и шанс дедлайна.
- **RQ3:** прогнозу нужны корректные даты и статусы, достаточная история, стабильный процесс и сопоставимая работа. Тренды, изменение состава команды, блокировки и малые выборки снижают надёжность.
- **RQ4:** P50/P85/P95 задают разные уровни риска для внутреннего плана, обещания заинтересованным сторонам и осторожного обязательства; перцентили позволяют выбрать ориентир для отдельной задачи.

Обзор является целевым, а не исчерпывающим SLR: отобраны шесть работ по Kanban, WIP и вероятностному прогнозированию; систематический поиск по нескольким базам и формальная оценка качества не проводились.''')

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata.kernelspec = {'display_name':'Python 3 (.venv)','language':'python','name':'python3'}
nb.metadata.language_info = {'name':'python','version':'3.13'}
nbf.write(nb, Path(__file__).parent / 'main.ipynb')
