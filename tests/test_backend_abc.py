"""Tests for the Backend ABC and registry."""

from __future__ import annotations

import pytest

from pycodegraph.db.backend import (
    Backend,
    get_backend,
    get_registered_backend_names,
    register_backend,
)
from pycodegraph.db.backends.inferdb import InferDBBackend
from pycodegraph.db.backends.postgresql import PostgreSQLBackend
from pycodegraph.db.backends.sqlite import SQLiteBackend


class TestRegistry:
    def test_get_backend_sqlite(self):
        backend = get_backend("sqlite")
        assert isinstance(backend, SQLiteBackend)

    def test_get_backend_postgresql(self):
        backend = get_backend("postgresql")
        assert isinstance(backend, PostgreSQLBackend)

    def test_get_backend_inferdb(self):
        backend = get_backend("inferdb")
        assert isinstance(backend, InferDBBackend)

    def test_get_backend_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("unknown")

    def test_get_registered_backend_names(self):
        names = get_registered_backend_names()
        assert "sqlite" in names
        assert "postgresql" in names
        assert "inferdb" in names


class TestABCCcontract:
    def test_cannot_instantiate_base_class(self):
        with pytest.raises(TypeError):
            Backend()

    def test_incomplete_subclass_raises_type_error(self):
        """A Backend subclass that doesn't implement all abstractmethods cannot be instantiated."""

        class _Incomplete(Backend):
            name = "incomplete"

        with pytest.raises(TypeError):
            _Incomplete()


class TestCustomBackendRegistration:
    def test_register_and_retrieve_custom_backend(self):
        """A custom Backend can be registered and retrieved via get_backend."""

        @register_backend
        class _TestBackend(Backend):
            name = "test_custom"

            @classmethod
            def configure_engine(cls, engine):
                pass

            @classmethod
            def initialize_schema(cls, engine):
                pass

            def insert_nodes_ignore(self):
                pass

            def upsert_file(self, row):
                pass

            def find_edges_between_nodes(self, conn, node_ids, kinds=None):
                return []

            def search_nodes_fts(
                self, conn, query_text, kinds, languages, limit, offset
            ):
                return []

            def after_nodes_changed(self, conn):
                pass

            def prepare_node_rows(self, rows):
                return rows

        backend = get_backend("test_custom")
        assert isinstance(backend, _TestBackend)
        assert backend.name == "test_custom"


class TestBackendInstancesAreStateless:
    def test_sqlite_backend_prepare_node_rows(self):
        backend = get_backend("sqlite")
        rows = [{"id": "1", "name": "foo", "fts_text": "foo bar"}]
        prepared = backend.prepare_node_rows(rows)
        assert prepared == [{"id": "1", "name": "foo"}]

    def test_postgresql_backend_prepare_node_rows(self):
        backend = get_backend("postgresql")
        rows = [{"id": "1", "name": "foo", "fts_text": "foo bar"}]
        prepared = backend.prepare_node_rows(rows)
        assert prepared == [{"id": "1", "name": "foo"}]

    def test_inferdb_backend_prepare_node_rows(self):
        backend = get_backend("inferdb")
        rows = [{"id": "1", "name": "foo", "fts_text": "foo bar"}]
        prepared = backend.prepare_node_rows(rows)
        assert prepared == rows  # InferDB keeps fts_text
