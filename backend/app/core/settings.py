"""Application settings. Loaded once at startup, immutable after."""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    RESEARCH = "research"
    PAPER = "paper"
    COPY = "copy"
    SEMI_AUTO = "semi_auto"
    AUTO = "auto"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "tradebot"
    log_level: str = "INFO"
    secret_key: SecretStr = Field(default=SecretStr("change-me"))

    # Storage
    database_url: str = "postgresql+asyncpg://tradebot:tradebot@localhost:5432/tradebot"
    redis_url: str = "redis://localhost:6379/0"

    # Trading mode + safety
    trading_mode: TradingMode = TradingMode.PAPER
    live_trading_enabled: bool = False
    live_trading_unlocked: bool = False

    # Data
    data_provider: Literal["mock", "alpaca", "yahoo"] = "yahoo"
    alpaca_api_key: SecretStr = SecretStr("")
    alpaca_api_secret: SecretStr = SecretStr("")
    alpaca_data_feed: Literal["iex", "sip"] = "iex"
    alpaca_trading_paper: bool = True

    # Broker
    broker: Literal["paper", "alpaca", "ibkr"] = "paper"
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1
    ibkr_account: str = ""

    # Aggressive options-paper defaults. Live trading still requires the
    # separate live gates in can_trade_live.
    risk_max_daily_loss_usd: float = 1500.0
    risk_max_trade_loss_usd: float = 500.0
    risk_max_open_positions: int = 20
    risk_max_trades_per_day: int = 80
    risk_max_option_premium_usd: float = 500.0
    risk_cooldown_after_losses: int = 3

    # Autonomous trading cadence + session policy
    auto_trade_regular_hours: bool = True
    auto_trade_premarket: bool = False
    auto_trade_afterhours: bool = False
    # Alpaca retail accounts do not provide a liquid, dependable "overnight"
    # session for this bot's options-first workflow. This flag is parsed for
    # config compatibility, but the AutoTradeLoop treats overnight entries as
    # inert and logs the reason instead of pretending they fill.
    auto_trade_overnight: bool = False
    auto_trade_options_extended_hours: bool = False
    auto_trade_max_entries_per_cycle: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "AUTO_TRADE_MAX_ENTRIES_PER_CYCLE",
            "MAX_ENTRIES_PER_CYCLE",
        ),
    )
    auto_trade_regular_min_confidence: float = 0.62
    auto_trade_extended_min_confidence: float = 0.75
    auto_trade_overnight_min_confidence: float = 0.85
    auto_trade_scan_interval_seconds: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "AUTO_TRADE_SCAN_INTERVAL_SECONDS",
            "SCAN_INTERVAL_SECONDS",
        ),
    )
    auto_trade_exit_interval_seconds: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "AUTO_TRADE_EXIT_INTERVAL_SECONDS",
            "EXIT_INTERVAL_SECONDS",
        ),
    )

    # Notifications
    discord_webhook_url: str = ""
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    @field_validator("live_trading_enabled", mode="after")
    @classmethod
    def _gate_live(cls, v: bool, info) -> bool:
        # Belt-and-braces: live trading requires BOTH flags.
        # The unlock check happens at order-placement time too.
        return v

    @property
    def can_trade_live(self) -> bool:
        """The ONE place we ask 'can we send a real order?'"""
        return (
            self.live_trading_enabled
            and self.live_trading_unlocked
            and self.trading_mode in (TradingMode.SEMI_AUTO, TradingMode.AUTO)
            and (
                self.broker == "ibkr"
                or (self.broker == "alpaca" and not self.alpaca_trading_paper)
            )
        )

    def validate_alpaca_credentials(self) -> None:
        """Raise clear missing-key errors before alpaca-py can emit a 403."""
        missing = []
        if not self.alpaca_api_key.get_secret_value():
            missing.append("ALPACA_API_KEY")
        if not self.alpaca_api_secret.get_secret_value():
            missing.append("ALPACA_API_SECRET")
        if missing:
            raise RuntimeError(
                "Missing Alpaca credential environment variable(s): "
                + ", ".join(missing)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
