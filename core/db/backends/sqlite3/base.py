import re
import time

from django.conf import settings
from django.db import transaction
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper
from django.db.backends.utils import CursorDebugWrapper, CursorWrapper

from core.disaster_recovery import WriteLeaseUnavailable, assert_lease_valid_at_commit, is_read_only_node, local_role_state, require_write_lease
from core.disaster_recovery_snapshot import snapshot_write_lock


_FIRST_SQL_WORD = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*([A-Za-z]+)", re.DOTALL)
_READ_WORDS = {"SELECT", "EXPLAIN", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def _is_write_statement(sql):
    match = _FIRST_SQL_WORD.match(sql if isinstance(sql, str) else "")
    if not match:
        return True
    word = match.group(1).upper()
    if word in _READ_WORDS:
        return False
    if word == "PRAGMA":
        return "=" in sql
    return True


class DisasterRecoveryCursorWrapper(CursorWrapper):
    def execute(self, sql, params=None):
        if not getattr(settings, "TAVERN_DR_ENABLED", False) or not _is_write_statement(sql):
            return super().execute(sql, params)
        lease = require_write_lease()
        if not self.db.in_atomic_block:
            with transaction.atomic(using=self.db.alias):
                self.db._bind_disaster_recovery_lease(lease)
                return super().execute(sql, params)
        self.db._bind_disaster_recovery_lease(lease)
        return super().execute(sql, params)

    def executemany(self, sql, param_list):
        if not getattr(settings, "TAVERN_DR_ENABLED", False) or not _is_write_statement(sql):
            return super().executemany(sql, param_list)
        lease = require_write_lease()
        if not self.db.in_atomic_block:
            with transaction.atomic(using=self.db.alias):
                self.db._bind_disaster_recovery_lease(lease)
                return super().executemany(sql, param_list)
        self.db._bind_disaster_recovery_lease(lease)
        return super().executemany(sql, param_list)


class DisasterRecoveryDebugCursorWrapper(CursorDebugWrapper, DisasterRecoveryCursorWrapper):
    pass


class DatabaseWrapper(SQLiteDatabaseWrapper):
    def cursor(self):
        if getattr(settings, "TAVERN_DR_ENABLED", False) and self.connection is not None:
            generation = local_role_state()["generation"]
            if getattr(self, "_dr_connection_generation", generation) != generation:
                self.close()
        return super().cursor()

    def get_new_connection(self, conn_params):
        connection = super().get_new_connection(conn_params)
        self._dr_connection_generation = local_role_state()["generation"]
        if is_read_only_node():
            connection.execute("PRAGMA query_only = ON")
        return connection

    def make_cursor(self, cursor):
        return DisasterRecoveryCursorWrapper(cursor, self)

    def make_debug_cursor(self, cursor):
        return DisasterRecoveryDebugCursorWrapper(cursor, self)

    def _bind_disaster_recovery_lease(self, lease):
        if self.connection is not None and is_read_only_node():
            raise WriteLeaseUnavailable("备用节点处于只读状态")
        if self.connection is not None:
            self.connection.execute("PRAGMA query_only = OFF")
        current = getattr(self, "_dr_write_lease", None)
        if current and (current.holder_id != lease.holder_id or current.epoch != lease.epoch):
            raise WriteLeaseUnavailable("数据库事务中写租约任期发生变化")
        if current is None:
            lock = snapshot_write_lock(settings.TAVERN_DR_SNAPSHOT_LOCK_PATH, timeout_seconds=10)
            try:
                lock.__enter__()
                latest = require_write_lease()
                if latest.holder_id != lease.holder_id or latest.epoch != lease.epoch:
                    raise WriteLeaseUnavailable("等待快照锁期间写租约任期发生变化")
            except Exception:
                lock.__exit__(None, None, None)
                raise
            self._dr_write_lock = lock
            self._dr_write_lease = latest
            self._dr_write_started_at = time.monotonic()

    def commit(self):
        lease = getattr(self, "_dr_write_lease", None)
        if getattr(settings, "TAVERN_DR_ENABLED", False) and lease is not None:
            assert_lease_valid_at_commit(lease, transaction_started_at=self._dr_write_started_at)
        super().commit()
        self._clear_disaster_recovery_lease()

    def rollback(self):
        try:
            super().rollback()
        finally:
            self._clear_disaster_recovery_lease()

    def _clear_disaster_recovery_lease(self):
        lock = getattr(self, "_dr_write_lock", None)
        self._dr_write_lock = None
        self._dr_write_lease = None
        self._dr_write_started_at = None
        if lock is not None:
            lock.__exit__(None, None, None)
