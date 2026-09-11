import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str
    discord_token: str
    model: str = "gpt-4.1-mini"
    memory_model: str = "gpt-4.1-mini"
    db_path: str = "data/hina.sqlite3"
    prompt_path: str = ""
    dm_always_reply: bool = False
    public_memory_in_dm: bool = True
    allowed_guild_ids: frozenset[int] = frozenset()
    cooldown: float = 5
    concurrency: int = 3
    output_tokens: int = 1000
    summary_every: int = 8
    history_turns: int = 12

    @classmethod
    def load(cls):
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)
        api_key, token = os.getenv("OPENAI_API_KEY", ""), os.getenv("DISCORD_TOKEN", "")
        if not api_key.strip() or not token.strip():
            raise ValueError(".env.local에 OPENAI_API_KEY와 DISCORD_TOKEN을 설정해 주세요.")
        dm = os.getenv("DM_ALWAYS_REPLY", "false").lower()
        if dm not in {"true", "false"}:
            raise ValueError("DM_ALWAYS_REPLY는 true 또는 false여야 합니다.")
        public_memory = os.getenv("PUBLIC_SERVER_MEMORY_IN_DM", "true").lower()
        if public_memory not in {"true", "false"}:
            raise ValueError("PUBLIC_SERVER_MEMORY_IN_DM은 true 또는 false여야 합니다.")
        s = cls(
            api_key=api_key, discord_token=token,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            memory_model=os.getenv("MEMORY_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
            db_path=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
            prompt_path=os.getenv("CHARACTER_PROMPT_PATH", ""),
            dm_always_reply=dm == "true",
            public_memory_in_dm=public_memory == "true",
            allowed_guild_ids=frozenset(int(x.strip()) for x in
                                       os.getenv("ALLOWED_GUILD_IDS", "").split(",") if x.strip()),
            cooldown=float(os.getenv("COOLDOWN_SECONDS", "5")),
            concurrency=int(os.getenv("MAX_CONCURRENT_REQUESTS", "3")),
            output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "1000")),
            summary_every=int(os.getenv("SUMMARY_EVERY", "8")),
            history_turns=int(os.getenv("HISTORY_TURNS", "12")),
        )
        if not (0 <= s.cooldown <= 3600 and 1 <= s.concurrency <= 20
                and 128 <= s.output_tokens <= 4096
                and 2 <= s.summary_every <= s.history_turns <= 30):
            raise ValueError("설정 범위 오류: cooldown 0~3600, concurrency 1~20, "
                             "output_tokens 128~4096, 2 <= summary_every <= history_turns <= 30")
        return s
