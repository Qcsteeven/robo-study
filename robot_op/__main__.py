"""Командная строка робота (для отладки; штатный запуск — из веб-интерфейса).

  python -m robot_op serve [--port 8000] [--demo]
  python -m robot_op levels
  python -m robot_op programs bachelor 2023
  python -m robot_op run bachelor 2023 [09.03.01-isoiu-2023-och ...] [--force]
  python -m robot_op resume <номер запуска>
  python -m robot_op pages
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m robot_op", description="Робот ОП ИРНИТУ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="веб-интерфейс")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--demo", action="store_true", help="демо-режим со встроенной копией сайта")
    sub.add_parser("levels", help="уровни образования и годы набора с сайта")
    p = sub.add_parser("programs", help="перечень ОП уровня и года")
    p.add_argument("level")
    p.add_argument("year", type=int)
    r = sub.add_parser("run", help="запустить робота и дождаться завершения")
    r.add_argument("level")
    r.add_argument("year", type=int)
    r.add_argument("programs", nargs="*", help="ключи ОП; без ключей — все ОП уровня и года")
    r.add_argument("--force", action="store_true")
    rs = sub.add_parser("resume", help="продолжить прерванный запуск")
    rs.add_argument("run_id", type=int)
    sub.add_parser("pages", help="пересобрать веб-страницы ОП по итоговым JSON-файлам, без скачивания")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        if a.demo:
            os.environ["ROBOT_APP_DEMO"] = "1"
        uvicorn.run("robot_op.web.app:get_app", factory=True, host=a.host, port=a.port)
        return 0

    from .config import get_settings
    from .models import Database
    from .robot.pipeline import Robot

    settings = get_settings()
    transport = None
    if settings.app.demo:
        from .web.app import _setup_demo
        transport = _setup_demo(settings)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    db = Database(settings.app.database_url)
    db.create_all()
    robot = Robot(settings, db, transport=transport)
    if a.cmd == "levels":
        for lv in robot.levels():
            print(f"{lv.level:13} {lv.title:13} {', '.join(map(str, sorted(lv.years)))}")
    elif a.cmd == "pages":
        from sqlalchemy import select
        from .models import Program, now
        from .robot import page as pagebuilder
        with db.Session() as ses:
            progs = ses.scalars(select(Program).where(Program.page.is_not(None))).all()
            for prog in progs:
                prog.page = pagebuilder.build(ses, prog)
                prog.page_updated_at = now()
            ses.commit()
        print(f"Страниц ОП пересобрано: {len(progs)}")
    elif a.cmd == "programs":
        for b in robot.programs(a.level, a.year):
            print(f"{b.key:40} {b.code} {b.title} — документов: {len(b.docs)}")
    else:
        run_id = a.run_id if a.cmd == "resume" else robot.create_run(a.level, a.year, a.programs or ["*"], a.force)
        robot.execute(run_id)
        with db.Session() as ses:
            print(json.dumps(Robot.counts(ses, run_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
