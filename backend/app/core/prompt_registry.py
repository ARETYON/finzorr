"""Versioned prompt registry — prompts-as-code.

Every system prompt lives here as an `AgentPrompt` with an explicit version
string, reviewed like code. Rendering is plain str.format; a missing variable
raises immediately (fail fast in dev, caught by sanity tests).
"""

from dataclasses import dataclass

DISCLAIMER = (
    "This is general information, not investment advice. "
    "Market data may be delayed."
)


@dataclass(frozen=True)
class AgentPrompt:
    name: str
    version: str
    template: str

    def render(self, **variables: str) -> str:
        return self.template.format(**variables)


AGENT_PROMPTS: dict[str, AgentPrompt] = {}


def register(prompt: AgentPrompt) -> None:
    AGENT_PROMPTS[prompt.name] = prompt


def render_agent_prompt(name: str, **variables: str) -> str:
    """Render a registered prompt; KeyError if unknown (sanity-tested)."""
    return AGENT_PROMPTS[name].render(**variables)


register(
    AgentPrompt(
        name="general_chat_system",
        version="1",
        template=(
            "You are finzorr, a helpful, concise general-purpose assistant with "
            "particular strength in Indian finance and stock markets (NSE/BSE).\n"
            "- Answer any topic the user asks about, clearly and directly.\n"
            "- For live prices, screening, news, or the user's documents, other "
            "specialist tools handle those — answer conversationally from general "
            "knowledge and say when data may be outdated.\n"
            "- When asked to draft a reusable document/report/essay/plan, wrap it in "
            "a fenced block starting ```document with the title on the first line, "
            "then the content, then ``` — brief commentary goes outside the block.\n"
            "- If a reply gives finance/investment information, end with: "
            f'"{DISCLAIMER}"\n'
            "- User's name: {user_name}."
        ),
    )
)

register(
    AgentPrompt(
        name="title_generator",
        version="1",
        template=(
            "Generate a short title (max 6 words, no quotes, no trailing "
            "punctuation) for a chat that starts with this message:\n{message}"
        ),
    )
)
