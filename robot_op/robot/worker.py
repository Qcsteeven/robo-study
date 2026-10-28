"""Фоновый исполнитель запусков: робот работает на одном компьютере, запуски выполняются по очереди."""
from __future__ import annotations

import logging
import queue
import threading

from .pipeline import Robot

log = logging.getLogger("robot_op")


class Worker:
    def __init__(self, robot: Robot):
        self.robot = robot
        self.q: queue.Queue[int] = queue.Queue()
        self.current: int | None = None
        self._thread = threading.Thread(target=self._loop, name="robot-worker", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def submit(self, run_id: int) -> None:
        self.q.put(run_id)

    def stop_current(self) -> None:
        self.robot.stop()

    def _loop(self) -> None:
        while True:
            run_id = self.q.get()
            self.current = run_id
            try:
                self.robot.execute(run_id)
            except Exception:   # noqa: BLE001
                log.exception("worker: run %s crashed", run_id)
            finally:
                self.current = None
                self.q.task_done()
