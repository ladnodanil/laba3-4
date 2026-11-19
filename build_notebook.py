"""Создаёт ноутбук с воспроизводимым обзором данных лабораторной."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "raw" / "dataset0304"
NOTEBOOK = Path(__file__).resolve().parent / "Лабораторная_3-4_практика.ipynb"


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def tables() -> tuple[str, str, str, str]:
    conn = sqlite3.connect(DATA / "bd.db")
    conn.row_factory = sqlite3.Row
    teams = {row["team_id"]: row["team_name"] for row in conn.execute("SELECT team_id, team_name FROM teams")}
    issues = list(conn.execute("SELECT team_id, current_status, created_at, resolved_at, story_points FROM issues"))
    status_counts = Counter(row["current_status"] for row in issues)
    team_rows = []
    for team_id, team_name in teams.items():
        items = [row for row in issues if row["team_id"] == team_id]
        done = [row for row in items if row["resolved_at"]]
        earliest = min((row["resolved_at"][:10] for row in done), default="—")
        latest = max((row["resolved_at"][:10] for row in done), default="—")
        team_rows.append([team_name, len(items), len(done), len(items) - len(done), earliest, latest])

    status_table = md_table(["Текущий статус", "Задач"], [[k, v] for k, v in status_counts.most_common()])
    team_table = md_table(["Команда", "Всего задач", "Завершено", "Не завершено", "Первая дата Done", "Последняя дата Done"], team_rows)

    history_rows = list(conn.execute("SELECT issue_id, status, changed_at FROM issue_status_history ORDER BY issue_id, changed_at"))
    history_status = Counter(row["status"] for row in history_rows)
    history_table = md_table(["Статус в истории", "Переходов"], [[k, v] for k, v in history_status.most_common()])

    issue_csv = read_csv("issues.csv")
    csv_table = md_table(["CSV-файл", "Строк", "Поля"], [["issues.csv", len(issue_csv), ", ".join(issue_csv[0].keys())]])
    conn.close()
    return team_table, status_table, history_table, csv_table


def cell(cell_type: str, source: str, outputs: list[dict] | None = None) -> dict:
    base = {"cell_type": cell_type, "metadata": {}, "source": source.splitlines(keepends=True)}
    if cell_type == "code":
        base.update({"execution_count": 1 if outputs else None, "outputs": outputs or []})
    return base


team_table, status_table, history_table, csv_table = tables()
intro = """# Лабораторная работа 3–4: практическая часть

Ноутбук читает исходные файлы из `../raw/dataset0304/`. Начинаем с проверки структуры и качества данных, затем строим метрики потока и симуляцию Монте-Карло.

## 1. Обзор набора данных

В таблицах ниже показаны реальные записи из `bd.db` и CSV. Для прогноза необходимо отдельно выбрать одну команду и проверить её историю завершения.
"""
setup = """from pathlib import Path
import sqlite3
import csv
from collections import Counter
from datetime import datetime
import random
import math

ROOT = Path.cwd()
DATA = ROOT.parent / "raw" / "dataset0304"
if not (DATA / "bd.db").exists():
    # Позволяет запускать ноутбук из корня проекта или из папки src.
    DATA = ROOT / "raw" / "dataset0304"
conn = sqlite3.connect(DATA / "bd.db")
conn.row_factory = sqlite3.Row
"""
overview_code = """# Состав набора и число задач по командам
teams = list(conn.execute("SELECT team_id, team_name FROM teams ORDER BY team_id"))
issues = list(conn.execute("SELECT * FROM issues"))
for team in teams:
    team_issues = [i for i in issues if i["team_id"] == team["team_id"]]
    done = [i for i in team_issues if i["resolved_at"]]
    print(f'{team["team_name"]:28} всего: {len(team_issues):3} | завершено: {len(done):3} | не завершено: {len(team_issues)-len(done):3}')

print("\\nТекущие статусы:")
for status, count in Counter(i["current_status"] for i in issues).most_common():
    print(f"{status:28} {count:3}")
"""
steps = """## 2. Выбор команды и подготовка throughput

На первом проходе выбрана **Backend API & DB Team**. Throughput — число задач, перешедших в Done в каждый календарный день. Дни без завершённых задач оставляем с нулём: иначе скорость будет завышена.
"""
throughput_code = """TEAM_NAME = "Backend API & DB Team"
TEAM_ID = next(t["team_id"] for t in teams if t["team_name"] == TEAM_NAME)
done_issues = [i for i in issues if i["team_id"] == TEAM_ID and i["resolved_at"]]

# Для первой версии используем дату resolved_at; ниже проверим её по истории статусов.
daily = Counter(i["resolved_at"][:10] for i in done_issues)
first_day = min(daily)
last_day = max(daily)
start = datetime.strptime(first_day, "%Y-%m-%d").date()
end = datetime.strptime(last_day, "%Y-%m-%d").date()
throughput = []
day = start
while day <= end:
    throughput.append(daily.get(day.isoformat(), 0))
    day = day.fromordinal(day.toordinal() + 1)

print(f"Команда: {TEAM_NAME}")
print(f"Завершено задач: {len(done_issues)}")
print(f"Период: {first_day} — {last_day} ({len(throughput)} календарных дней)")
print(f"Средний throughput: {sum(throughput)/len(throughput):.3f} задач/день")
print(f"Медианный throughput: {sorted(throughput)[len(throughput)//2]:.0f} задач/день")
"""
mc = """## 3. Монте-Карло для завершения бэклога

Следующая ячейка моделирует число календарных дней до закрытия 30 задач, случайно выбирая дневной throughput из истории команды с возвращением. Цель можно изменить в `REMAINING_ISSUES`.
"""
mc_code = """REMAINING_ISSUES = 30
N_SIMULATIONS = 10_000
rng = random.Random(42)
durations = []
for _ in range(N_SIMULATIONS):
    remaining = REMAINING_ISSUES
    days = 0
    while remaining > 0:
        remaining -= rng.choice(throughput)
        days += 1
    durations.append(days)

def percentile(values, p):
    values = sorted(values)
    index = (len(values) - 1) * p / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)

print(f"Прогноз для {REMAINING_ISSUES} задач (календарные дни), {N_SIMULATIONS:,} симуляций")
for p in (50, 85, 95):
    print(f"P{p}: {percentile(durations, p):.1f} дней")
"""
notes = """## Примечание к интерпретации

Это первичный прогноз по одной команде. В следующем этапе следует сверить `resolved_at` с переходами в статус Done из `issue_status_history`, проверить пропуски/дубликаты, построить график ежедневного throughput и оценить стабильность процесса. Если команда или правило включения задач меняются, прогноз необходимо пересчитать.
"""

cells = [cell("markdown", intro), cell("code", setup), cell("markdown", "### Таблица: задачи по командам\n\n" + team_table + "\n\n### Таблица: текущие статусы\n\n" + status_table + "\n\n### Таблица: история переходов\n\n" + history_table + "\n\n### Таблица: файл задач\n\n" + csv_table), cell("code", overview_code), cell("markdown", steps), cell("code", throughput_code), cell("markdown", mc), cell("code", mc_code), cell("markdown", notes)]
notebook = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python", "version": "3.13"}}, "nbformat": 4, "nbformat_minor": 5}
NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")
print(NOTEBOOK)
