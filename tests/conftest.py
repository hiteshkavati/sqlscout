from pathlib import Path

import pytest

from sqlscout.agent import Agent
from sqlscout.bootstrap import build_toolbox
from sqlscout.config import Settings
from sqlscout.datagen import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return str(build(tmp_path_factory.mktemp("db") / "shop.db"))


@pytest.fixture(scope="session")
def settings(db_path):
    return Settings(db_path=db_path, rate_limit_per_min=1000)


@pytest.fixture(scope="session")
def toolbox(settings):
    return build_toolbox(settings)


@pytest.fixture()
def policy(toolbox):
    return toolbox.policy


@pytest.fixture()
def make_agent(toolbox):
    def factory(llm, **kw):
        return Agent(llm, toolbox, **kw)

    return factory
