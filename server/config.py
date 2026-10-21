"""
DevPulse configuration.
Copy .env.example to .env and fill in your values before running.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))


@dataclass
class Settings:
    # GitHub
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_api_url: str = "https://api.github.com"

    # Google Gemini (for AI summaries)
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))

    # Server
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8000")))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "info"))

    def validate(self) -> list[str]:
        missing = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        return missing


settings = Settings()
