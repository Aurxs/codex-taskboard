from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from injector.cdp_injector import (
    CODEX_BROWSER_DATABASES,
    CODEX_PROFILE_IMPORT_MARKER,
    import_codex_browser_profile,
)


class InjectorProfileTests(unittest.TestCase):
    def test_read_only_browser_backup_writes_marker_once(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as profile_dir:
            source = Path(source_dir)
            profile = Path(profile_dir)
            for index, relative_path in enumerate(CODEX_BROWSER_DATABASES):
                database = source / relative_path
                database.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("CREATE TABLE records (value TEXT)")
                    connection.execute("INSERT INTO records VALUES (?)", (str(index),))

            self.assertTrue(import_codex_browser_profile(source, profile))
            self.assertTrue((profile / CODEX_PROFILE_IMPORT_MARKER).is_file())
            for index, relative_path in enumerate(CODEX_BROWSER_DATABASES):
                with closing(sqlite3.connect(profile / relative_path)) as connection:
                    self.assertEqual(
                        connection.execute("SELECT value FROM records").fetchone(),
                        (str(index),),
                    )

            # The marker prevents a later launch from copying a changed source
            # database into the already-created independent profile.
            with closing(sqlite3.connect(source / CODEX_BROWSER_DATABASES[0])) as connection, connection:
                connection.execute("INSERT INTO records VALUES ('changed')")
            self.assertFalse(import_codex_browser_profile(source, profile))
            with closing(sqlite3.connect(profile / CODEX_BROWSER_DATABASES[0])) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM records").fetchone(), (1,))


if __name__ == "__main__":
    unittest.main()
