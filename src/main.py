"""Module entrypoint: registers all models and serves them to viam-server."""

import asyncio

from viam.module.module import Module

from .models.chess_coach import ChessCoachService  # noqa: F401
from .models.fakes import FakeBuzzer, FakeKgoal  # noqa: F401
from .models.hush_buzzer import HushBuzzer  # noqa: F401
from .models.kgoal_sensor import KgoalBoost  # noqa: F401

if __name__ == "__main__":
    asyncio.run(Module.run_from_registry())
