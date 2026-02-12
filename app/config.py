from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    modal_token_id: str = ""
    modal_token_secret: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
