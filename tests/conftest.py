"""Shared fixtures for pycodegraph integration tests."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from pycodegraph import CodeGraph

# ---------------------------------------------------------------------------
# Synthetic project source code
# ---------------------------------------------------------------------------

PYTHON_MODELS = """\
\"\"\"Data models for the application.\"\"\"


class User:
    \"\"\"Represents a user.\"\"\"

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def greet(self) -> str:
        return f"Hello, {self.name}!"


class Admin(User):
    \"\"\"Admin user with elevated privileges.\"\"\"

    def greet(self) -> str:
        return f"Hello, admin {self.name}!"
"""

PYTHON_SERVICES = """\
from models import User


def create_user(name: str, email: str) -> User:
    return User(name, email)


def notify_user(user: User) -> str:
    return user.greet()
"""

PYTHON_UTILS = """\
def format_date(timestamp: int) -> str:
    \"\"\"Format a Unix timestamp as a date string.\"\"\"
    return str(timestamp)


def parse_config(path: str) -> dict:
    \"\"\"Parse a configuration file.\"\"\"
    return {}
"""

PYTHON_MAIN = """\
from services import create_user
from utils import format_date


def run():
    user = create_user("Alice", "alice@example.com")
    date = format_date(1234567890)
    return user, date
"""

TS_TYPES = """\
export interface User {
  name: string;
  email: string;
}

export interface Admin extends User {
  role: string;
}
"""

TS_SERVICE = """\
import { User } from "./types";

export function createUser(name: string, email: string): User {
  return { name, email };
}
"""

TS_INDEX = """\
import { createUser } from "./service";

const user = createUser("Alice", "alice@example.com");
"""

GO_MODELS = """\
package main

type User struct {
    Name  string
    Email string
}

func (u *User) Greet() string {
    return "Hello, " + u.Name
}

type Admin struct {
    User
    Role string
}
"""

GO_SERVICE = """\
package main

func CreateUser(name string, email string) *User {
    u := &User{Name: name, Email: email}
    return u
}

func NotifyUser(u *User) string {
    return u.Greet()
}
"""

RUST_MAIN = """\
pub struct Config {
    pub timeout: u64,
}

impl Config {
    pub fn new(timeout: u64) -> Self {
        Self { timeout }
    }
}

pub enum Color {
    Red,
    Green,
    Blue,
}

pub trait Drawable {
    fn draw(&self);
}

pub fn run() {
    let config = Config::new(30);
}
"""

JAVA_APP = """\
import java.util.List;

public class Application {
    private String name;

    public void run() {
        System.out.println(name);
    }
}

interface Handler {
    void handle();
}
"""

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def write_file(root: str | Path, rel_path: str, content: str) -> str:
    """Write *content* to *root/rel_path*, creating parent dirs. Returns the relative path."""
    full = Path(root) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return rel_path


@pytest.fixture()
def create_python_project(tmp_path):
    """Factory that creates a synthetic Python project under *tmp_path*."""

    def _create():
        root = str(tmp_path)
        write_file(root, "models.py", PYTHON_MODELS)
        write_file(root, "services.py", PYTHON_SERVICES)
        write_file(root, "utils.py", PYTHON_UTILS)
        write_file(root, "main.py", PYTHON_MAIN)
        return root

    return _create


@pytest.fixture()
def create_typescript_project(tmp_path):
    """Factory that creates a synthetic TypeScript project under *tmp_path*."""

    def _create():
        root = str(tmp_path)
        write_file(root, "src/types.ts", TS_TYPES)
        write_file(root, "src/service.ts", TS_SERVICE)
        write_file(root, "src/index.ts", TS_INDEX)
        return root

    return _create


@pytest.fixture()
def create_go_project(tmp_path):
    """Factory that creates a synthetic Go project under *tmp_path*."""

    def _create():
        root = str(tmp_path)
        write_file(root, "models.go", GO_MODELS)
        write_file(root, "service.go", GO_SERVICE)
        return root

    return _create


@pytest.fixture()
def codegraph_from_project(tmp_path):
    """Factory that initialises a CodeGraph for a given project root, indexes, and yields.

    The CodeGraph is closed automatically in teardown.
    """

    instances: list[CodeGraph] = []

    def _create(project_root: str) -> CodeGraph:
        cg = CodeGraph.init(project_root)
        cg.index_all()
        instances.append(cg)
        return cg

    yield _create

    for cg in instances:
        with contextlib.suppress(Exception):
            cg.close()


@pytest.fixture()
def empty_codegraph(tmp_path):
    """A CodeGraph initialised on an empty directory (no source files)."""
    root = str(tmp_path)
    cg = CodeGraph.init(root)
    yield cg
    cg.close()
