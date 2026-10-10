"""Claude API prices (US$ per million tokens) used to log cost per restaurant.

Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-10). Update when prices change.
Haiku 5.5 is priced by prompt length: prompts over 100,000 tokens pay the higher rates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write_5m: float
    cache_read: float


PRICES: dict[str, tuple[Price, Price | None]] = {
    # model: (standard price, price for prompts > 100k tokens or None)
    "claude-haiku-5-5": (Price(0.10, 0.50, 0.125, 0.01), Price(0.50, 2.50, 0.625, 0.05)),
    "claude-sonnet-5-5": (Price(2.0, 10.0, 2.50, 0.10), None),
    "claude-opus-5-5": (Price(4.0, 20.0, 5.0, 0.20), None),
    "claude-haiku-4-5": (Price(1.0, 5.0, 1.25, 0.10), None),
}

LONG_PROMPT_TOKENS = 100_000


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )


def cost_usd(model: str, usage: Usage) -> float:
    standard, long = PRICES.get(model, (Price(2.0, 10.0, 2.5, 0.1), None))
    prompt = usage.input_tokens + usage.cache_write_tokens + usage.cache_read_tokens
    p = long if (long is not None and prompt > LONG_PROMPT_TOKENS) else standard
    total = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_write_tokens * p.cache_write_5m
        + usage.cache_read_tokens * p.cache_read
    ) / 1_000_000
    return round(total, 6)
