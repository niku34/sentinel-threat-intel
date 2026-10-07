from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        extra="ignore",        # silently drop keys not declared here
    )

    database_url: str = ""
    ollama_base_url: str = "http://localhost:11434"
    youtube_api_key: str = ""

    certin_contact_email: str = "contact@example.com"
    certin_organisation: str = "Example Broadcaster Ltd"
    certin_reporting_email: str = "incident@cert-in.org.in"
    certin_deadline_hours: int = 6
    certin_reference_prefix: str = "JS-CERTIN"


settings = Settings()
