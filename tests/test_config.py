from pydantic import SecretStr

from drlf.config import Settings


def test_database_url_is_secret() -> None:
    database_url = "postgresql://user:" + "password@" + "localhost/database"
    settings = Settings(database_url=database_url)

    assert isinstance(settings.database_url, SecretStr)
    assert "password" not in str(settings.database_url)
